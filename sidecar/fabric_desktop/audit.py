"""The audit trail (FD-125): who did what, where, when, and how it ended.

Every engine call that changes something or runs something (a notebook cell, a SQL query, a run,
a publish, a promote, a write to OneLake, a schedule, a setting) adds one line to
``<data>/audit/audit-YYYY-MM.jsonl``. Read-only calls (listing, previewing, polling) don't.

Each line holds:

    at, action, outcome (ok or error; "started" before a call in FAIL_CLOSED), seconds, and reason (one of REASONS) when a call that
             returned rather than raised refused or partly failed
    who (the signed-in account), tenant, host, os user, app version, compute ("this PC")
    target   the call's short identifying values: workspace, lakehouse, environment, entity,
             notebook name, ids. Code, SQL, cell text and data are never recorded.
    error    the error, with secrets removed
    prev, hash   a SHA-256 chain (audit_chain.py): each line's hash covers the line before it, so a
                 deleted or edited line shows up in ``fabric-desktop audit --verify``. The app, scheduled
                 runs and the agent append under one lock, so they never fork the chain

It is append-only and local to the PC (or to the agent's home on a server). It is evidence for the
person and their organization, not a security boundary: someone who controls the PC can delete
the whole folder, which ``--verify`` can't detect. Ship the files to a log store when that
matters.
"""

from __future__ import annotations

import getpass
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import __version__, audit_chain
from .redact import secrets

SCHEMA = "audit@1"
GENESIS = audit_chain.GENESIS

# Calls that only read or watch. Everything else is recorded, so a new call is audited unless it
# is added here on purpose. academy_run and learn_lab_run run a learner's code, but in a sandbox
# whose writes are rolled back, so they change nothing a person would audit; pbi_embed_token is
# left off because it issues a credential.
READ_ONLY = frozenset("""
skins_list access_cache access_find_person access_lakehouse access_review access_who_can_read agents_list all_schedules cached_schemas app_settings
editor_pending editor_status telemetry_status support_bundle_preview benchmark_notebooks capacity_health catalog catalog_tables cost_report endorsements environment fabric_runs
governance_applicability governance_overview governance_policy governance_resolve history
governance_templates governance_last_check governance_practice_settings governance_product_checklist
incidents_list incidents_summary kernel_complete kernel_inspect kernel_sessions kernel_variable_detail kernel_variables notebook_frame_page notebook_frame_profile notebook_frame_query_profile notebook_frame_chart notebook_frame_wrangle mesh_about mesh_home mesh_map mesh_members mesh_overview notebook_environments environment_job
mesh_workspace_domains meta_alerts meta_compare meta_drift meta_entity meta_entity_shortcuts meta_fabric_result
meta_freshness meta_health meta_last_release meta_releases meta_rollback_preview meta_key_lookup meta_key_notebook meta_keys meta_model_plan
meta_model_ossie_models meta_model_ossie_export meta_model_ossie_read meta_model_ossie_preview
meta_model_directlake_setup meta_model_directlake_check
meta_model_ossie_folders meta_model_ossie_export_folder
meta_model_dictionary meta_model_dictionary_preview
meta_preflight meta_profile_preview meta_project meta_promote_check meta_promote_impact
meta_quarantine meta_run_detail meta_runs meta_runs_all meta_schedules meta_table_history meta_varlib
meta_versions notebook_dependencies notebook_inputs notebook_places notebook_runs notebook_schedules notebook_syntax notebook_format notebook_practices notebook_practice_rules notebook_plotly_js open_notebook pc_schedule_runs
pc_schedules_for time_zones platform_admins platform_audit platform_change_window platform_domains platform_entra_check platform_entra_find platform_entra_groups platform_entra_script platform_glossary platform_key_tables
platform_access_reviews platform_landing platform_landing_files platform_overview platform_owner_coverage platform_product_items platform_product_trust platform_products platform_requests platform_sources
platform_status platform_whoami poll_cell preview_file preview_notebook preview_table prove_check
prove_list prove_rules_get prove_status queue_list receipt_get receipt_redactions replay_bundle_preview replay_bundles_list replay_check run_as_list shortcut_access
shortcut_center shortcut_changes shortcut_check shortcut_connections shortcut_find_moved shortcut_preflight shortcut_readers
shortcut_request_envs
shortcut_rules side_by_side_list side_by_side_status table_columns wizard_preview wizard_templates
workload_capacity_use workload_detail workload_preflight workloads_list workspace_marks item_pins notebook_details notebook_batch xray xray_adopt_plan
xray_optimize_check xray_vacuum_preview xray_staging_preview
sql_analyze sql_practices sql_practice_rules sql_export_status sql_format sql_page sql_status
sql_templates_list sql_queries_list wh_projects_list wh_project_files wh_project_read wh_project_compare wh_project_diff wh_project_deploy_plan wh_monitoring_status
fabric_item_support fabric_git_links fabric_git_check fabric_git_overview fabric_git_status git_branches git_capacity_diff git_commit_detail git_locations git_compare git_diff git_file_at git_info git_log git_notebook_info git_op_status git_platform_diff git_settings git_status git_vscode_plan side_by_side_writes side_by_side_cores
local_delta_history local_explain local_query_status local_transform_plan local_transform_runs
pipeline_compatibility pipeline_export pipeline_import
usage_preview
risk_check meta_risk_gate risk_settings
model_bpa_status model_bpa_risk
incidents_board incidents_for_me incident_sla_policy incidents_history incident_impact
optimize_discover optimize_findings optimize_recommendations optimize_status optimization_assess
tenant_settings_history
platform_notify_settings platform_notify_open
pipeline_run_check pipeline_run_status pipeline_runs pipeline_list
fabric_pipeline_runs fabric_pipeline_activity_runs workspace_pipelines workspace_pipeline_definition fabric_connections workspace_dataflows workspace_dataflow_definition fabric_dataflow_refreshes
workspace_copy_jobs workspace_copy_job_definition fabric_copy_job_runs
notebook_versions notebook_version notebook_version_compare notebook_validate_check notebook_validate_list notebook_validate_status notebook_cleanup_plan notebook_cleanup_apply
notebook_resources
local_writes
notebook_folders
notebook_file_path notebook_disk_stamp notebook_name_for_path notebook_listing notebook_search notebook_import_check notebook_delete_check notebook_deleted notebook_fabric_check notebook_fabric_step_check notebook_vorder_cell notebook_fabric_step_result notebook_publish_check
notebook_formats_import_check notebook_print_page
profile_get profile_report profile_avatar
notebook_comments_list
engine_catalog engine_connections engine_prepare engine_state engine_poll_cell engine_profiles engine_discover databricks_runs databricks_run databricks_billing_last databricks_sync_pairs databricks_sync_compare databricks_bundle_targets databricks_bundle_validate databricks_bundle_plan
learn_catalog learn_module learn_lab_run learn_lab_check learn_lab_hint learn_rule_guides learn_dismissals
learn_fixes learn_fix_compare learn_fix_checked learn_patterns learn_team_notes
academy_catalog academy_exercise academy_run academy_validate academy_hint academy_readiness academy_sandbox_status
pbi_embed_content
appstudio_home appstudio_open appstudio_history appstudio_versions appstudio_preview appstudio_connections appstudio_publish_check
academy_exam_home academy_exam_heartbeat academy_exam_review
""".split())

# Parameters that hold code, queries or data: never recorded, only noted as given.
_CONTENT = frozenset({"code", "source", "sql", "query", "statement", "text", "cells", "content", "ipynb", "body", "value", "values",
                      "rows", "data", "parts", "definition", "notes", "description", "spec", "toml", "changes", "events", "routes",
                      "report_summary", "identity", "teams_webhook"})
_MAX = 200


def audited(method: str) -> bool:
    return method not in READ_ONLY and not method.startswith("list_")


def target(params: dict[str, Any]) -> dict[str, Any]:
    """The identifying values of a call's parameters; code, queries and data are left out."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if key.lower() in _CONTENT or any(word in key.lower() for word in ("secret", "password", "token")):
            out[key] = "(given)"
        elif isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        elif isinstance(value, str):
            out[key] = secrets(value) if len(value) <= _MAX else "(given)"
        elif isinstance(value, list) and len(value) <= 20 and all(isinstance(v, str) and len(v) <= _MAX for v in value):
            out[key] = [secrets(v) for v in value]
        elif isinstance(value, dict):
            inner = target(value)
            out[key] = inner if len(json.dumps(inner, default=str)) <= 4 * _MAX else "(given)"
        else:
            out[key] = "(given)"
    return out


# Calls that must not run without a record: a "started" line is written before them, and they are
# refused when it can't be (a full disk, a locked file). These Prod-reaching changes always; any other
# call when it is aimed at Prod (an env or target of "prod"); and any other call when it is given a
# confirmation, an approval, a typed name, a risk-check acknowledgement or an override (a non-empty
# value). An ordinary query or edit still runs as before when its line can't be written.
FAIL_CLOSED = frozenset({"meta_promote", "meta_rollback", "meta_restore", "xray_vacuum", "shortcut_repoint",
                         "shortcut_repoint_many", "shortcut_undo", "shortcut_accept_risk"})
# Parameter names, matched exactly. test_audit lists every audited method's parameters that look like
# one of these and fails on a name in neither set, so a new one is reviewed rather than missed.
GUARD_PARAMS = frozenset({
    "confirm", "confirmed", "confirm_name", "confirm_deletes", "confirm_unknown", "confirm_writes",
    "approve", "approval_id", "typed", "risk_id", "require_acknowledgement",
    "allow", "allow_commands", "allow_override", "allow_secrets", "allow_uncontained", "force"})
NOT_GUARD_PARAMS = frozenset({"expect"})        # the version a tab opened, not a go-ahead
_PROD_PARAMS = ("env", "envs", "target")


def _given(value: Any) -> bool:
    return value not in (None, False, "", 0, [], {})


def _is_prod(value: Any) -> bool:
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_is_prod(v) for v in value)
    return isinstance(value, str) and value.strip().lower() == "prod"


def fails_closed(method: str, params: Any) -> bool:
    """Whether this call of ``method`` with ``params`` runs only once its line is written."""
    if method in FAIL_CLOSED:
        return True
    if not isinstance(params, dict):
        return False
    return any((key in GUARD_PARAMS and _given(value)) or (key in _PROD_PARAMS and _is_prod(value))
               for key, value in params.items())


NOT_RECORDED = ("The audit log couldn't be written, so this didn't run ({error}). Free some disk space for "
                "KratosForge's data folder, or close what holds its audit file, then try again.")


# Why a call that returned (rather than raised) still counts as an error: one of these fixed codes,
# never anything taken from the reply's text.
REASONS = ("refused", "busy", "policy", "preview", "invalid", "partial", "failed", "cancelled")
_DONE = ("ok", "skipped")


def outcome(result: Any) -> tuple[str, str]:
    """("ok", "") or ("error", reason) for a call's result.

    A call that refuses or partly fails without raising says so in what it returns: ``ok`` False
    (busy, a policy, only a preview, a definition with problems), a non-empty ``failed`` list, a
    ``status`` of failed, or run rows (meta runs) where a row didn't end ok or skipped. The app, PC
    schedules and the agent all use this, so one run reads the same whichever way it started."""
    if isinstance(result, list):
        rows = [r for r in result if isinstance(r, dict) and "status" in r and "entity" in r]
        bad = [r for r in rows if r.get("status") not in _DONE]
        if bad:
            return "error", "partial" if len(bad) < len(rows) else "failed"
        return "ok", ""
    if not isinstance(result, dict):
        return "ok", ""
    if result.get("ok") is False:
        if result.get("preview"):
            return "error", "preview"
        codes = [str(p.get("code") or "") for p in result.get("problems") or [] if isinstance(p, dict)]
        if "busy" in codes:
            return "error", "busy"
        if any("policy" in c or "uncontained" in c or "not_allowed" in c for c in codes):
            return "error", "policy"
        return "error", "invalid" if codes else "refused"
    if result.get("status") == "failed":
        return "error", "failed"
    failed = result.get("failed")
    if isinstance(failed, list) and failed:
        done = result.get("done")
        return "error", "partial" if isinstance(done, list) and done else "failed"
    return "ok", ""


class AuditLog:
    def __init__(self, folder: Path, app: str = "desktop"):
        self.folder = Path(folder)
        self.app = app
        self.host = socket.gethostname()
        try:
            self.os_user = getpass.getuser()
        except Exception:  # noqa: BLE001 - no user name on this system
            self.os_user = ""

    def _files(self) -> list[Path]:
        return audit_chain.files(self.folder)

    def record(self, action: str, outcome: str, seconds: float, params: dict[str, Any] | None = None,
               who: str = "", tenant: str = "", compute: str = "this PC", error: str = "",
               reason: str = "") -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        entry: dict[str, Any] = {
            "schema": SCHEMA, "at": now.isoformat(timespec="milliseconds"), "action": action, "outcome": outcome,
            "seconds": round(seconds, 3), "who": who, "tenant": tenant, "host": self.host, "os_user": self.os_user,
            "app": self.app, "version": __version__, "compute": compute, "target": target(params or {}),
        }
        if error:
            entry["error"] = secrets(error)[:1000]
        if reason:
            if reason not in REASONS:
                raise ValueError(f"reason is one of {', '.join(REASONS)}")
            entry["reason"] = reason
        audit_chain.append(self.folder, entry)  # under the folder's lock: no other process forks the chain
        return entry

    def entries(self) -> Iterator[dict[str, Any]]:
        for path in self._files():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    yield json.loads(line)

    def recent(self, limit: int = 200, action: str = "") -> list[dict[str, Any]]:
        rows = [e for e in self.entries() if not action or e.get("action") == action]
        return rows[-limit:][::-1]

    def verify(self) -> dict[str, Any]:
        """Walks the chain: every line's hash must match its content and the line before it."""
        return audit_chain.verify(self.folder)
