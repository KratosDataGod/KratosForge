"""JSON-lines RPC over stdin/stdout, used by the Tauri shell.

Request:  {"id": 1, "method": "list_workspaces", "params": {}}
Response: {"id": 1, "result": [...]}  or  {"id": 1, "error": "message"}
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import IO

from . import audit
from .redact import secrets
from .service import Service

METHODS = {"environment", "readiness_check", "readiness_write_test", "sign_in", "sign_out", "app_settings", "save_app_settings", "list_workspaces", "list_capacities", "list_lakehouses", "create_lakehouse",
           "catalog", "catalog_tables", "endorsements", "set_favorite", "list_items", "workspace_marks", "set_workspace_mark",
           "list_tables", "list_files", "preview_job", "run_job", "history",
           "run_cell", "start_cell", "poll_cell", "interrupt_cell", "kernel_variables", "kernel_complete", "restart_kernel", "run_sql", "save_sql", "export_sql_csv", "table_columns", "list_notebooks", "new_notebook", "open_notebook",
           "save_notebook", "rename_notebook", "delete_notebook", "publish_notebook",
           "platform_status", "platform_setup", "platform_join", "platform_forget", "platform_domains",
           "platform_save_domain", "platform_delete_domain", "platform_import_fabric_domains", "mesh_workspace_domains", "mesh_map", "platform_glossary", "platform_save_term", "platform_audit",
           "platform_whoami", "platform_admins", "platform_claim_admin", "platform_add_admin", "platform_remove_admin",
           "platform_key_tables", "platform_create_key_table", "platform_sources", "platform_save_source",
           "platform_delete_source", "platform_landing", "platform_landing_files", "platform_requests", "platform_request",
           "platform_decide", "platform_withdraw", "platform_revoke", "platform_overview", "platform_products",
           "mesh_overview", "mesh_set_project", "mesh_save_product", "mesh_delete_product", "mesh_publish", "mesh_save_link",
           "mesh_delete_link", "mesh_resolve_links", "mesh_about", "mesh_save_about", "meta_set_designation",
           "meta_create_designation_workspace", "meta_keys_to_platform", "meta_keys_to_lakehouse",
           "meta_project", "meta_init", "meta_open", "meta_set_environment", "meta_create_workspace", "meta_entity", "meta_save_entity",
           "meta_new_entity", "meta_delete_entity", "meta_set_layers", "meta_run", "meta_preflight", "meta_runs", "meta_runs_all", "meta_run_detail",
           "meta_preview", "meta_promote_check", "meta_promote", "meta_git_status", "meta_diff", "notebook_diff", "meta_lineage", "meta_keys", "meta_set_keys_workspace",
           "meta_new_key_table", "meta_delete_key_table", "meta_key_lookup", "meta_save_source_system",
           "meta_delete_source_system", "meta_keys_sync", "meta_key_notebook",
           "run_notebook_in_fabric", "fabric_runs", "cancel_fabric_run", "notebook_schedules", "add_notebook_schedule",
           "delete_notebook_schedule", "meta_health", "meta_optimize", "meta_set_maintenance", "meta_model_plan",
           "meta_build_model", "meta_profile", "meta_profile_preview", "meta_add_rules",
           "meta_set_compute", "meta_fabric_result", "meta_schedules", "meta_add_schedule", "meta_delete_schedule",
           "meta_versions", "meta_compare", "meta_promote_impact", "meta_quarantine", "meta_replay",
           "meta_discard_quarantine", "meta_set_freshness", "meta_freshness", "meta_alerts", "meta_set_alerts",
           "meta_drift", "meta_drift_apply",
           "meta_table_history", "meta_restore", "cost_report", "set_cost_settings",
           "list_shortcuts", "all_shortcuts", "create_shortcut", "delete_shortcut", "shortcut_connections",
           "shortcut_center", "shortcut_check", "shortcut_readers", "shortcut_access", "shortcut_rules",
           "shortcut_save_rules", "shortcut_preflight", "shortcut_changes", "shortcut_repoint", "shortcut_undo",
           "shortcut_request",
           "meta_entity_shortcuts", "access_review", "access_set_role", "access_find_person", "access_lakehouse",
           "access_save_role", "access_delete_role", "access_who_can_read", "meta_tests", "meta_varlib",
           "meta_publish_varlib", "add_pc_schedule", "pc_schedules_for", "delete_pc_schedule",
           "set_pc_schedule_enabled", "run_pc_schedule_now", "sync_pc_schedules", "pc_schedule_runs", "all_schedules",
           "set_fabric_schedule_enabled", "delete_fabric_schedule", "run_fabric_item_now",
           "xray", "xray_pause", "xray_optimize", "xray_vacuum", "xray_vacuum_preview", "xray_adopt_plan", "xray_adopt",
           "preview_table", "preview_file", "preview_notebook",
           "side_by_side_start", "side_by_side_status", "side_by_side_list", "side_by_side_cancel", "benchmark_notebooks",
           "workloads_list", "workload_detail", "workload_assign", "workload_link", "workload_unlink",
           "workload_preflight", "workload_act", "workload_actions",
           "incidents_list", "incidents_summary", "incidents_watch", "incidents_evaluate", "incident_acknowledge", "incident_assign", "incident_note",
           "incident_resolve",
           "notebook_environment", "receipts_list", "receipt_get", "receipt_export", "receipt_redactions",
           "prove_check", "prove_start", "prove_status", "prove_list", "prove_cancel", "prove_verify",
           "prove_delete_outputs", "prove_rules_get", "prove_rules_set", "receipt_save",
           "wizard_templates", "wizard_preview", "wizard_create", "kernel_info", "stop_kernel_process",
           "notebook_dependencies", "side_by_side_writes",
           "git_info", "git_repos", "git_open", "git_forget", "git_init", "git_clone_start", "git_fetch", "git_pull",
           "git_push", "git_op_status", "git_op_cancel", "git_ops", "git_status", "git_diff", "git_stage",
           "git_stage_all", "git_unstage", "git_discard", "git_scan", "git_commit", "git_log", "git_commit_detail",
           "git_compare", "git_file_at", "git_branches", "git_create_branch", "git_switch", "git_delete_branch",
           "git_restore_stash", "git_abort_merge", "git_settings", "git_set_identity", "git_add_remote",
           "git_path_info", "git_notebook_info", "audit_log"}


# Settings > Integrations: only the app reaches these (integrations/app_service.py).
from .integrations.app_service import METHODS as INTEGRATION_METHODS  # noqa: E402

METHODS |= INTEGRATION_METHODS


def handle(service: Service, line: str) -> dict:
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        return {"id": None, "error": f"invalid JSON: {exc}"}
    req_id, method = req.get("id"), req.get("method")
    if method not in METHODS:
        return {"id": req_id, "error": f"unknown method {method!r}"}
    params = req.get("params", {})
    started = time.monotonic()
    try:
        reply = {"id": req_id, "result": getattr(service, method)(**params)}
    except Exception as exc:  # report every failure to the UI rather than dying
        # Errors can quote a request (a URL with a signature, a header): never pass a token on.
        if hasattr(exc, "errors"):  # a gate: its message already lists every reason
            reply = {"id": req_id, "error": secrets(str(exc))}
        else:
            reply = {"id": req_id, "error": secrets(f"{type(exc).__name__}: {exc}")}
    if audit.audited(method):
        _audit(service, method, params, reply, time.monotonic() - started)
    return reply


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


def serve(service: Service | None = None, stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
    service = service or Service()
    for line in stdin:
        if line.strip():
            stdout.write(json.dumps(handle(service, line), default=str) + "\n")
            stdout.flush()


def _log_file() -> IO[str]:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    os.makedirs(os.path.join(base, "FabricDesktop"), exist_ok=True)
    return open(os.path.join(base, "FabricDesktop", "sidecar.log"), "a", encoding="utf-8", buffering=1)


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
    _refresh_integration_launcher()
    service = Service()
    threading.Thread(target=_start_integration_http, args=(service,), daemon=True, name="integrations-http").start()
    serve(service, stdin=sys.stdin, stdout=rpc_out)


def _start_integration_http(service: Service) -> None:
    """If the person switched the local API on, start it with the engine (it stops with it)."""
    try:
        service.integrations_sync_http()
    except Exception as exc:  # noqa: BLE001 - the app works without it; Settings shows why
        print(f"integrations: local API not started: {exc}", file=sys.stderr)


def _refresh_integration_launcher() -> None:
    """After an update the engine is in a new folder: point the AI tools' launcher at it."""
    try:
        from .integrations.config import launcher_path, write_launcher
        from .service import data_dir
        if launcher_path(data_dir()).exists():
            write_launcher(data_dir())
    except Exception as exc:  # noqa: BLE001 - never stop the engine over this
        print(f"integrations: could not update the launcher: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
