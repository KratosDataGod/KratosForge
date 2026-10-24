"""The audit trail (FD-125): who did what, where, when, and how it ended.

Every engine call that changes something or runs something (a notebook cell, a SQL query, a run,
a publish, a promote, a write to OneLake, a schedule, a setting) adds one line to
``<data>/audit/audit-YYYY-MM.jsonl``. Read-only calls (listing, previewing, polling) don't.

Each line holds:

    at, action, outcome (ok or error), seconds
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
# is added here on purpose.
READ_ONLY = frozenset("""
access_cache access_find_person access_lakehouse access_review access_who_can_read agents_list all_schedules all_shortcuts app_settings
audit_log telemetry_status benchmark_notebooks capacity_health catalog catalog_tables cost_report endorsements environment fabric_runs
governance_applicability governance_overview governance_policy governance_resolve governance_version history
incidents_list incidents_summary kernel_complete kernel_info kernel_inspect kernel_sessions kernel_variable_detail kernel_variables notebook_frame_page notebook_frame_profile notebook_frame_query_profile notebook_frame_chart notebook_frame_wrangle mesh_about mesh_map mesh_overview notebook_environments environment_job
mesh_workspace_domains meta_alerts meta_compare meta_drift meta_entity meta_entity_shortcuts meta_fabric_result
meta_freshness meta_git_status meta_health meta_last_release meta_releases meta_rollback_preview meta_key_lookup meta_key_notebook meta_keys meta_model_plan
meta_model_ossie_models meta_model_ossie_export meta_model_ossie_read meta_model_ossie_preview
meta_preflight meta_preview meta_profile_preview meta_project meta_promote_check meta_promote_impact
meta_quarantine meta_run_detail meta_runs meta_runs_all meta_schedules meta_table_history meta_varlib
meta_versions notebook_dependencies notebook_environment notebook_inputs notebook_places notebook_runs notebook_schedules notebook_syntax notebook_format notebook_plotly_js open_notebook pc_schedule_runs
pc_schedules_for platform_admins platform_audit platform_domains platform_glossary platform_key_tables
platform_landing platform_landing_files platform_overview platform_product_trust platform_products platform_requests platform_sources
platform_status platform_whoami poll_cell preview_file preview_job preview_notebook preview_table prove_check
prove_list prove_rules_get prove_status queue_list receipt_get receipt_redactions receipts_list replay_bundle_preview replay_bundles_list replay_check run_as_list shortcut_access
shortcut_center shortcut_changes shortcut_check shortcut_connections shortcut_find_moved shortcut_preflight shortcut_readers
shortcut_rules side_by_side_list side_by_side_status table_columns wizard_preview wizard_templates
workload_actions workload_capacity_use workload_detail workload_preflight workloads_list workspace_marks xray xray_adopt_plan
xray_optimize_check xray_vacuum_preview
sql_analyze sql_compat_profiles sql_dialects sql_executions sql_export_status sql_format sql_page sql_status
git_branches git_commit_detail git_locations git_compare git_diff git_file_at git_info git_log git_notebook_info git_op_status git_ops git_path_info git_platform_diff git_repos git_scan git_settings git_status side_by_side_writes
local_delta_history local_explain local_query_status local_transform_plan local_transform_runs
pipeline_activity_registry pipeline_compatibility pipeline_export pipeline_import pipeline_inspect pipeline_validate
usage_imports usage_preview usage_reconciliation
placement_advice risk_acknowledgements risk_check meta_risk_gate risk_settings
incidents_board incident_sla_policy incidents_history
optimize_discover optimize_findings optimize_inspect optimize_recommendations optimize_status
tenant_settings_history
platform_notify_settings platform_notify_open
pipeline_run_check pipeline_run_status pipeline_runs pipeline_list
fabric_pipeline_runs fabric_pipeline_activity_runs
notebook_versions notebook_version notebook_version_compare notebook_validate_check notebook_validate_list notebook_validate_status notebook_cleanup_plan notebook_cleanup_apply
notebook_resources
notebook_folders
notebook_listing notebook_search notebook_import_check notebook_delete_check notebook_deleted notebook_fabric_check notebook_fabric_step_check notebook_vorder_cell notebook_fabric_step_result notebook_publish_check
notebook_formats_import_check
profile_get profile_report
notebook_comments_list
""".split())

# Parameters that hold code, queries or data: never recorded, only noted as given.
_CONTENT = frozenset({"code", "source", "sql", "query", "statement", "text", "cells", "content", "ipynb", "body", "value", "values",
                      "rows", "data", "parts", "definition", "notes", "description", "spec", "toml", "changes", "events", "routes",
                      "report_summary", "identity"})
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


class AuditLog:
    def __init__(self, folder: Path, app: str = "desktop"):
        self.folder = Path(folder)
        self.app = app
        self.host = socket.gethostname()
        try:
            self.os_user = getpass.getuser()
        except Exception:  # no user name on this system
            self.os_user = ""

    def _files(self) -> list[Path]:
        return audit_chain.files(self.folder)

    def record(self, action: str, outcome: str, seconds: float, params: dict[str, Any] | None = None,
               who: str = "", tenant: str = "", compute: str = "this PC", error: str = "") -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        entry: dict[str, Any] = {
            "schema": SCHEMA, "at": now.isoformat(timespec="milliseconds"), "action": action, "outcome": outcome,
            "seconds": round(seconds, 3), "who": who, "tenant": tenant, "host": self.host, "os_user": self.os_user,
            "app": self.app, "version": __version__, "compute": compute, "target": target(params or {}),
        }
        if error:
            entry["error"] = secrets(error)[:1000]
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
