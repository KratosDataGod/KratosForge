"""JSON-lines RPC over stdin/stdout, used by the Tauri shell.

Request:  {"id": 1, "method": "list_workspaces", "params": {}}
Response: {"id": 1, "result": [...]}  or  {"id": 1, "error": "message"}

Replies can arrive in a different order from the requests (see QUICK); the shell matches them by id.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any, Callable

from . import audit, net_trust, rpc_params
from .redact import secrets
from .service import Service

METHODS = {"environment", "readiness_check", "readiness_write_test", "sign_in", "sign_out", "app_settings", "save_app_settings", "list_workspaces", "list_capacities", "list_lakehouses", "create_lakehouse",
           "catalog", "catalog_tables", "endorsements", "set_favorite", "list_items", "workspace_marks", "set_workspace_mark", "item_pins", "set_item_pin",
           "notebook_details", "notebook_batch_start", "notebook_batch", "notebook_batch_stop", "notebook_batch_fabric",
           "list_tables", "list_files", "history",
           "start_cell", "poll_cell", "interrupt_cell", "debug_cell", "kernel_variables", "kernel_variable_detail", "kernel_complete", "kernel_inspect", "notebook_syntax", "notebook_format", "notebook_practices", "notebook_practice_rules", "notebook_plotly_js", "notebook_frame_page", "notebook_frame_profile", "notebook_frame_query_profile", "notebook_frame_chart", "notebook_frame_wrangle", "notebook_ipywidget_event", "notebook_frame_download", "restart_kernel", "save_sql", "table_columns", "cached_schemas", "sql_format", "sql_analyze", "sql_practices", "sql_practice_rules", "sql_compat_export", "sql_start", "sql_status", "sql_page", "sql_cancel", "sql_export", "sql_export_status", "sql_export_cancel", "list_notebooks", "new_notebook", "open_notebook",
           "save_notebook", "notebook_inputs", "notebook_inputs_set", "notebook_onelake_writes_set", "rename_notebook", "publish_notebook",
           "platform_status", "platform_setup", "platform_join", "platform_forget", "platform_keys_setup", "platform_keys_join", "platform_share_keys", "platform_cancel_share_keys", "platform_domains",
           "platform_save_domain", "platform_create_domain", "platform_delete_domain", "platform_import_fabric_domains", "mesh_workspace_domains", "mesh_map", "mesh_home", "platform_glossary", "platform_save_term", "platform_audit",
           "platform_whoami", "platform_admins", "platform_claim_admin", "platform_add_admin", "platform_remove_admin",
           "platform_key_tables", "platform_create_key_table", "platform_sources", "platform_save_source",
           "platform_delete_source", "platform_landing", "platform_landing_files", "platform_landing_setup",
           "platform_create_landing_zone", "platform_delete_landing_zone", "platform_switch_landing_reads", "platform_requests", "platform_request",
           "platform_decide", "platform_withdraw", "platform_revoke", "platform_owner_coverage", "platform_note_seen", "platform_access_reviews", "platform_confirm_access", "platform_overview", "platform_products",
           "platform_product_trust", "platform_scan_endorsement", "platform_add_product_term", "platform_remove_product_term", "platform_follow",
           "platform_product_items", "platform_add_product_item", "platform_remove_product_item",
           "platform_entra_groups", "platform_entra_script", "platform_entra_save", "platform_entra_link", "platform_entra_unlink",
           "platform_entra_naming_save", "platform_entra_find", "platform_entra_check",
           "governance_overview", "governance_policy", "governance_create", "governance_save_draft",
           "governance_discard_draft", "governance_publish", "governance_set_status", "governance_assign",
           "governance_unassign", "governance_applicability", "governance_resolve",
           "governance_templates", "governance_template_save", "governance_template_delete", "governance_template_use",
           "governance_template_from_policy", "governance_template_authors", "governance_check_now", "governance_last_check", "governance_practice_settings",
           "governance_product_checklist",
           "mesh_overview", "mesh_members", "mesh_set_member", "mesh_set_project", "mesh_save_product", "mesh_delete_product", "mesh_publish", "mesh_save_link",
           "mesh_delete_link", "mesh_resolve_links", "mesh_about", "mesh_save_about", "meta_set_designation",
           "meta_create_designation_workspace", "meta_keys_to_platform", "meta_keys_to_lakehouse",
           "meta_project", "meta_init", "meta_open", "meta_set_environment", "meta_create_workspace", "meta_entity", "meta_save_entity",
           "meta_new_entity", "meta_delete_entity", "meta_set_layers", "meta_run", "meta_preflight", "meta_runs", "meta_runs_all", "meta_run_detail",
           "meta_promote_check", "meta_promote", "meta_last_release", "meta_releases", "meta_rollback_preview", "meta_rollback", "meta_lineage", "meta_keys", "meta_set_keys_workspace",
           "meta_new_key_table", "meta_delete_key_table", "meta_key_lookup", "meta_save_source_system",
           "meta_delete_source_system", "meta_source_read", "meta_sources_move", "meta_keys_sync", "meta_key_notebook",
           "run_notebook_in_fabric", "notebook_fabric_step_check", "notebook_vorder_cell", "run_notebook_fabric_step", "notebook_fabric_step_result", "fabric_runs", "cancel_fabric_run", "notebook_schedules", "add_notebook_schedule",
           "delete_notebook_schedule", "meta_health", "meta_optimize", "meta_set_maintenance", "meta_model_plan",
           "meta_build_model", "meta_profile", "meta_profile_preview", "meta_add_rules",
           "meta_set_compute", "meta_fabric_result", "meta_schedules", "meta_add_schedule", "meta_delete_schedule",
           "meta_versions", "meta_compare", "meta_promote_impact", "meta_quarantine", "meta_replay",
           "meta_discard_quarantine", "meta_set_freshness", "meta_freshness", "meta_alerts", "meta_set_alerts",
           "meta_drift", "meta_drift_apply",
           "meta_table_history", "meta_restore", "cost_report", "set_cost_settings", "capacity_health",
           "usage_preview", "usage_import", "usage_import_delete",
           "risk_check", "meta_risk_gate", "risk_settings", "set_risk_settings", "risk_acknowledge",
           "model_bpa_status", "model_bpa_risk", "model_bpa_check",
           "create_shortcut", "delete_shortcut", "shortcut_connections",
           "shortcut_center", "shortcut_check", "shortcut_readers", "shortcut_access", "shortcut_rules",
           "shortcut_save_rules", "shortcut_preflight", "shortcut_changes", "shortcut_repoint", "shortcut_undo",
           "shortcut_request", "shortcut_find_moved", "shortcut_repoint_many", "shortcut_accept_risk", "shortcut_withdraw_risk",
           "meta_entity_shortcuts", "access_review", "access_set_role", "access_find_person", "access_lakehouse",
           "access_save_role", "access_delete_role", "access_who_can_read", "meta_tests", "meta_varlib",
           "meta_publish_varlib", "add_pc_schedule", "pc_schedules_for", "delete_pc_schedule",
           "set_pc_schedule_enabled", "update_pc_schedule", "run_pc_schedule_now", "sync_pc_schedules", "pc_schedule_runs", "notebook_runs", "all_schedules",
           "set_fabric_schedule_enabled", "update_fabric_schedule", "time_zones", "delete_fabric_schedule", "run_fabric_item_now",
           "xray", "xray_pause", "xray_optimize", "xray_optimize_check", "xray_vacuum", "xray_vacuum_preview", "xray_adopt_plan", "xray_adopt",
           "preview_table", "preview_file", "preview_notebook",
           "side_by_side_start", "side_by_side_status", "side_by_side_list", "side_by_side_cancel", "benchmark_notebooks",
           "workloads_list", "workload_detail", "workload_assign", "workload_link", "workload_unlink",
           "workload_preflight", "workload_act", "workload_capacity_use",
           "agents_list", "queue_list", "run_as_list", "incidents_list", "incidents_summary", "incidents_watch", "incidents_evaluate", "incident_acknowledge", "incident_assign", "incident_note",
           "incident_resolve",
           "incidents_board", "incident_sla_policy", "incident_report", "incident_set_priority", "incidents_history_sync", "incidents_history",
           "incident_impact", "incident_tell_impacted",
           "receipt_get", "receipt_redactions",
           "prove_check", "prove_start", "prove_status", "prove_list", "prove_cancel", "prove_verify",
           "prove_delete_outputs", "prove_rules_get", "prove_rules_set", "receipt_save",
           "notebook_debug_get", "notebook_debug_set", "replay_bundle_preview", "replay_bundle_save",
           "replay_bundles_list", "replay_bundle_open", "replay_check", "replay_start", "replay_result", "replay_end",
           "replay_delete_outputs",
           "wizard_templates", "wizard_preview", "wizard_create", "kernel_sessions", "stop_kernel_process", "kernel_session_share", "kernel_session_leave", "notebook_environments", "environment_create", "environment_delete", "environment_attach", "environment_install", "environment_uninstall", "environment_job",
           "notebook_dependencies", "side_by_side_writes", "side_by_side_cores", "side_by_side_cores_set",
           "git_info", "git_open", "git_forget", "git_init", "git_clone_start", "git_fetch", "git_pull",
           "git_push", "git_op_status", "git_op_cancel", "git_status", "git_diff", "git_stage",
           "git_stage_all", "git_unstage", "git_discard", "git_commit", "git_log", "git_commit_detail",
           "git_compare", "git_file_at", "git_branches", "git_create_branch", "git_switch", "git_delete_branch",
           "git_restore_stash", "git_abort_merge", "git_settings", "git_set_identity", "git_add_remote",
           "git_notebook_info", "git_create", "git_set_kind", "git_locations", "git_set_locations",
           "git_platform_diff", "git_platform_export", "git_platform_deploy", "git_capacity_diff", "git_capacity_write", "git_apply_hunk", "git_vscode_plan", "git_vscode_apply",
           "notebook_places", "move_notebook",
           "pipeline_import", "pipeline_export",
           "pipeline_compatibility",
           "pipeline_run_check", "pipeline_run_start", "pipeline_run_status", "pipeline_run_cancel", "pipeline_runs",
           "pipeline_list", "fabric_pipeline_runs", "fabric_pipeline_activity_runs",
           "workspace_pipelines", "workspace_pipeline_definition", "fabric_connections",
           "workspace_dataflows", "workspace_dataflow_definition", "fabric_dataflow_refreshes",
           "workspace_copy_jobs", "workspace_copy_job_definition", "fabric_copy_job_runs",
           "git_notebook_info", "telemetry_status", "telemetry_new_install_id"}


# Settings > Integrations: only the app reaches these (integrations/app_service.py).
from .integrations.app_service import METHODS as INTEGRATION_METHODS  # noqa: E402

METHODS |= INTEGRATION_METHODS

# Local data projects (localdata_service.py): SQL over files on this PC.
from .localdata_service import METHODS as LOCAL_DATA_METHODS  # noqa: E402

METHODS |= LOCAL_DATA_METHODS

# Optimize's shared lakehouse (optimize_service.py).
from .optimize_service import METHODS as OPTIMIZE_METHODS  # noqa: E402

METHODS |= OPTIMIZE_METHODS

from .tenant_history_service import METHODS as TENANT_HISTORY_METHODS  # noqa: E402
from .capacity_history_service import METHODS as CAPACITY_HISTORY_METHODS  # noqa: E402

METHODS |= TENANT_HISTORY_METHODS
METHODS |= CAPACITY_HISTORY_METHODS

# Governance's request and shortcut notices to Teams (platform_notify_service.py).
from .platform_notify_service import METHODS as PLATFORM_NOTIFY_METHODS  # noqa: E402

METHODS |= PLATFORM_NOTIFY_METHODS

# The notebook's Engine picker (engines_service.py, Preview).
from .engines_service import METHODS as ENGINE_METHODS  # noqa: E402

METHODS |= ENGINE_METHODS

# OneLake > Access across every workspace: the access cache (access_cache_service.py).
from .access_cache_service import METHODS as ACCESS_CACHE_METHODS  # noqa: E402

METHODS |= ACCESS_CACHE_METHODS

# Learn: lessons and labs on this PC (learn_service.py).
from .learn_service import METHODS as LEARN_METHODS  # noqa: E402

METHODS |= LEARN_METHODS

# Settings > Advanced > OneLake writes: the local write folders (local_writes_service.py).
from .local_writes_service import METHODS as LOCAL_WRITES_METHODS  # noqa: E402

METHODS |= LOCAL_WRITES_METHODS

# A notebook's resources folder (notebook_resources_service.py).
from .notebook_resources_service import METHODS as NOTEBOOK_RESOURCE_METHODS  # noqa: E402

METHODS |= NOTEBOOK_RESOURCE_METHODS

# Semantic model tab: Export to Ossie and Import from Ossie (model_ossie_service.py).
from .model_ossie_service import METHODS as MODEL_OSSIE_METHODS  # noqa: E402

METHODS |= MODEL_OSSIE_METHODS

# Semantic model tab: the Direct Lake check (model_directlake_service.py).
from .model_directlake_service import METHODS as MODEL_DIRECTLAKE_METHODS  # noqa: E402

METHODS |= MODEL_DIRECTLAKE_METHODS

# Semantic model tab: the data dictionary (model_dictionary_service.py).
from .model_dictionary_service import METHODS as MODEL_DICTIONARY_METHODS  # noqa: E402

METHODS |= MODEL_DICTIONARY_METHODS

# Validate notebook (validate_service.py): code checks and a clean run in a fresh kernel.
from .validate_service import METHODS as VALIDATE_METHODS  # noqa: E402

METHODS |= VALIDATE_METHODS
from .notebook_versions_service import METHODS as NOTEBOOK_VERSIONS_METHODS  # noqa: E402
METHODS |= NOTEBOOK_VERSIONS_METHODS

# Notebook files (notebook_files_service.py): search, open a file, save a copy, delete and restore,
# and copy a Fabric notebook to this PC.
from .notebook_files_service import METHODS as NOTEBOOK_FILES_METHODS  # noqa: E402

METHODS |= NOTEBOOK_FILES_METHODS

# Your own SQL templates for the Warehouse page's gallery (sql_templates_service.py).
from .sql_templates_service import METHODS as SQL_TEMPLATE_METHODS  # noqa: E402

METHODS |= SQL_TEMPLATE_METHODS
from .sql_queries_service import METHODS as SQL_QUERY_METHODS  # noqa: E402

METHODS |= SQL_QUERY_METHODS
# A warehouse as files in Fabric's Git layout (warehouse_project_service.py).
from .warehouse_project_service import METHODS as WH_PROJECT_METHODS  # noqa: E402

METHODS |= WH_PROJECT_METHODS
# Workspace monitoring views for a warehouse (warehouse_monitoring_service.py).
from .warehouse_monitoring_service import METHODS as WH_MONITORING_METHODS  # noqa: E402

METHODS |= WH_MONITORING_METHODS

# Folders for notebooks (notebook_folders_service.py): new, rename, delete when empty, move into one.
from .notebook_folders_service import METHODS as NOTEBOOK_FOLDERS_METHODS  # noqa: E402

METHODS |= NOTEBOOK_FOLDERS_METHODS

# Notebook export and import in Fabric, Databricks and Jupyter formats (notebook_formats_service.py).
from .notebook_formats_service import METHODS as NOTEBOOK_FORMATS_METHODS  # noqa: E402

METHODS |= NOTEBOOK_FORMATS_METHODS
# Profile (progress_service.py): the person's private record of verified work and its report.
from .progress_service import METHODS as PROFILE_METHODS  # noqa: E402

METHODS |= PROFILE_METHODS

# Comments on notebook cells (comments_service.py), kept in the notebook file.
from .comments_service import METHODS as COMMENT_METHODS  # noqa: E402

METHODS |= COMMENT_METHODS

# Run cells from VS Code (editor_service.py): the editor channel's switch, pairing requests and
# the OneLake writes paired windows ask for.
from .editor_service import METHODS as EDITOR_METHODS  # noqa: E402

METHODS |= EDITOR_METHODS

# Policy exceptions (central/governance_service.py): ending one early (the platform checks for a platform
# admin), and the ones about to end, for the bell, the Data mesh banner and a Teams notice.
METHODS |= {"governance_end_exception", "governance_exceptions_ending"}

# Embed Studio (powerbi_embed_service.py, Preview): a Power BI token for the embed frame, and a report's
# or dashboard's embed URL, pages and tiles. Both read only; the token can write (see the module).
from .powerbi_embed_service import METHODS as POWERBI_EMBED_METHODS  # noqa: E402

METHODS |= POWERBI_EMBED_METHODS


# The calls the shell gives a short time limit (QUICK in app/src-tauri/src/watchdog.rs): they read
# local state or the kernel and answer at once. They run in their own lane, so a long run, publish
# or sign-in can't hold them up (and the shell's time limit can't end the engine because one waited
# behind it). Everything else runs one at a time in the order it arrives, as before. Notebook writes
# (new, save, rename) stay ordered with publish and delete, which save too: a publish queued behind a
# long call must not write its older cells over an autosave that overtook it.
QUICK = frozenset({
    "app_settings", "cached_schemas", "debug_cell", "editor_pending", "editor_status", "interrupt_cell", "kernel_complete", "kernel_variables",
    "list_notebooks", "notebook_comments_list", "notebook_debug_get", "notebook_dependencies",
    "notebook_folders", "notebook_practices", "notebook_resources", "notebook_version", "notebook_versions",
    "open_notebook", "poll_cell", "prove_list", "prove_rules_get", "prove_status",
    "receipt_get", "side_by_side_list",
    "side_by_side_status", "side_by_side_writes", "sql_analyze", "sql_cancel",
    "sql_export", "sql_export_cancel", "sql_export_status",
    "sql_page", "sql_practices", "sql_start", "sql_status",
    "wizard_preview", "wizard_templates",
    "engine_state", "engine_poll_cell", "engine_cancel",
})


def handle(service: Service, line: str) -> dict:
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        return {"id": None, "error": f"invalid JSON: {exc}"}
    if not isinstance(req, dict):  # a list or a bare value has no id to answer, but the shell still gets a line
        return {"id": None, "error": "invalid request: expected an object with id, method and params"}
    req_id, method = req.get("id"), req.get("method")
    if not isinstance(method, str) or method not in METHODS:
        return {"id": req_id, "error": _short(f"unknown method {method!r}")}
    params = req.get("params", {})
    started = time.monotonic()
    try:
        call = getattr(service, method)
        rpc_params.check(method, call, params)
        reply = {"id": req_id, "result": call(**params)}
    except Exception as exc:  # report every failure to the UI rather than dying
        # Errors can quote a request (a URL with a signature, a header): never pass a token on.
        if hasattr(exc, "errors"):  # a gate: its message already lists every reason
            reply = {"id": req_id, "error": _short(_home(secrets(str(exc))))}
        else:
            text = f"{type(exc).__name__}: {exc}"
            if net_trust.certificate_refused(exc):  # one line that says what to do, then the detail
                text = f"{net_trust.HINT} ({text})"
            reply = {"id": req_id, "error": _short(_home(secrets(text)))}
    if audit.audited(method):
        seconds = time.monotonic() - started
        _audit(service, method, params, reply, seconds)
        _count(service, method, reply, seconds)
    return reply


ERROR_CHARS = 4000  # an error quoting a huge input (a 200,000-character name) is cut to this


def _home(message: str) -> str:
    """``message`` with the home folder written as ~, so an error pasted into a ticket or a screenshot
    doesn't usually carry the Windows account name (other text, such as a OneDrive folder or a sign-in
    name, still can). Results keep full paths: the app opens them."""
    try:
        home = Path.home()
    except (RuntimeError, KeyError, OSError):  # no HOME or USERPROFILE: the error still has to reach the shell
        return message
    for form in dict.fromkeys((str(home).replace("\\", "\\\\"), str(home), home.as_posix())):  # repr() doubles \
        if len(form) > 3:  # never a bare drive or /
            message = re.sub(re.escape(form) + r"(?![^\\/'\"\s:)])", "~", message, flags=re.IGNORECASE)
    return message


def _short(message: str) -> str:
    return message if len(message) <= ERROR_CHARS else message[:ERROR_CHARS] + f"… ({len(message) - ERROR_CHARS} more characters)"


def _audit(service: Service, method: str, params: dict, reply: dict, seconds: float) -> None:
    """One audit line per call that changes or runs something. Never fails the call itself."""
    try:
        log = getattr(service, "audit", None)
        if log is None:
            return
        auth = getattr(service, "_auth", None)  # only an account already signed in; never sign in here
        who = tenant = ""
        if auth is not None:
            who, tenant = auth.signed_in_user() or "", auth.signed_in_tenant() or ""
        log.record(method, "error" if "error" in reply else "ok", seconds, params if isinstance(params, dict) else {},
                   who=who, tenant=tenant, error=str(reply.get("error") or ""))
    except Exception as exc:  # noqa: BLE001 - an audit problem is logged, the user's call goes on
        print(f"audit: could not record {method}: {type(exc).__name__}: {exc}", file=sys.stderr)


def _count(service: Service, method: str, reply: dict, seconds: float) -> None:
    """Opt-in usage counts (telemetry.py): the action's name, whether it failed, how long it took."""
    try:
        usage = getattr(service, "telemetry", None)
        if usage is not None:
            usage.record(method, "error" not in reply, seconds, str(reply.get("error") or ""))
    except Exception as exc:  # noqa: BLE001 - never fails the call
        print(f"telemetry: could not count {method}: {type(exc).__name__}", file=sys.stderr)


def _finite(value: Any) -> Any:
    """``value`` with NaN and infinity as null, anywhere in it."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def _reply_text(reply: dict[str, Any]) -> str:
    """The reply as one line of strict JSON. Python writes NaN and Infinity, which the shell's parser
    (and JavaScript's) refuses, failing the whole call: a widget's value or a chart's data can hold them."""
    try:
        return json.dumps(reply, default=str, allow_nan=False)
    except ValueError:
        return json.dumps(_finite(json.loads(json.dumps(reply, default=str))), allow_nan=False)


def serve(service: Service | None = None, stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
    """Hands each request to its lane (QUICK, or everything else in order). Replies can come back
    in a different order from the requests: each carries its request's id, which the shell matches."""
    service = service or Service()
    write = threading.Lock()  # one whole reply line at a time

    def answer(line: str) -> None:
        reply = handle(service, line)
        try:
            text = _reply_text(reply)
        except Exception as exc:  # noqa: BLE001 - the caller still gets an answer
            text = json.dumps({"id": reply.get("id"), "error": f"Could not send the result: {type(exc).__name__}"})
        with write:
            stdout.write(text + "\n")
            stdout.flush()

    lanes: dict[str, queue.Queue] = {"quick": queue.Queue(), "ordered": queue.Queue()}
    workers = [threading.Thread(target=_lane, args=(q, answer), daemon=True, name=f"rpc-{name}")
               for name, q in lanes.items()]
    for worker in workers:
        worker.start()
    for line in stdin:
        if line.strip():
            lanes["quick" if _method(line) in QUICK else "ordered"].put(line)
    for q in lanes.values():
        q.put(None)  # finish what was asked, then stop
    for worker in workers:
        worker.join()


def _method(line: str) -> object:
    try:
        req = json.loads(line)
    except json.JSONDecodeError:
        return None
    return req.get("method") if isinstance(req, dict) else None


def _lane(requests: queue.Queue, answer: Callable[[str], None]) -> None:
    while (line := requests.get()) is not None:
        try:
            answer(line)
        except Exception as exc:  # noqa: BLE001 - e.g. a broken pipe: the shell is gone; keep draining
            print(f"rpc: could not answer: {type(exc).__name__}: {exc}", file=sys.stderr)


def _log_file() -> IO[str]:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    os.makedirs(os.path.join(base, "FabricDesktop"), exist_ok=True)
    return open(os.path.join(base, "FabricDesktop", "sidecar.log"), "a", encoding="utf-8", buffering=1)


class _SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return secrets(super().format(record))


def _log_to_stderr() -> None:
    """The engine's ``fabric_desktop.*`` log lines (INFO and up) go to stderr, which the shell keeps
    in sidecar.log, with anything credential-shaped removed. Without a handler they went nowhere."""
    logger = logging.getLogger("fabric_desktop")
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_SafeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def main() -> None:
    # Started from a windowed app, Python can have no stderr at all (sys.stderr is
    # None). Log to a file then, rather than failing on the first write.
    if sys.stderr is None:
        sys.stderr = _log_file()
    # Keep a private handle on stdout for responses, and send fd 1 to stderr so
    # anything a notebook cell or a native library prints cannot corrupt the protocol.
    rpc_out = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    sys.stdin.reconfigure(encoding="utf-8")  # the shell sends UTF-8 whatever the Windows code page
    _log_to_stderr()
    _refresh_integration_launcher()
    from . import notebook
    notebook.sweep_spills()  # output files a closed notebook session left behind
    service = Service()
    threading.Thread(target=_start_integration_http, args=(service,), daemon=True, name="integrations-http").start()
    try:
        serve(service, stdin=sys.stdin, stdout=rpc_out)
    finally:
        runs = getattr(service, "_sql_runs", None)
        if runs is not None:
            runs.shutdown()  # end the SQL page's query processes with the engine
        engines = getattr(service, "_engines", None)
        if engines is not None:
            engines.close_all()  # end engine sessions (and remote sessions in Fabric) with the app


def _start_integration_http(service: Service) -> None:
    """If the person switched the local API on, start it with the engine (it stops with it)."""
    try:
        service.integrations_sync_http()
    except Exception as exc:  # noqa: BLE001 - the app works without it; Settings shows why
        print(f"integrations: local API not started: {exc}", file=sys.stderr)
    try:
        service.editor_sync()  # the editor channel, if VS Code may run notebooks
    except Exception as exc:  # noqa: BLE001 - likewise
        print(f"integrations: editor channel not started: {exc}", file=sys.stderr)


def _refresh_integration_launcher() -> None:
    """After an update the engine is in a new folder: point the AI tools' and the CLI's launchers at it."""
    try:
        from .integrations.config import launcher_path, write_cli_launcher, write_launcher
        from .service import data_dir
        if launcher_path(data_dir()).exists():
            write_launcher(data_dir())
        write_cli_launcher(data_dir())  # VS Code's Validate task runs it on every developer's PC
    except Exception as exc:  # noqa: BLE001 - never stop the engine over this
        print(f"integrations: could not update the launcher: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
