"""HD-050 / AR-2 first slice: every method the engine accepts over RPC (`server.METHODS`) is either
called by the desktop UI or listed below with the reason it stays callable without a screen.

Only the desktop shell talks to the engine's JSON-lines RPC, and the shell only forwards what
`app/ui` asks for. The MCP tools, the local HTTP API, the CLI and the headless agent call the same
`Service` methods in-process, not by RPC name. So an allowlisted name with no UI caller is surface
that nothing on screen needs. This test doesn't remove any (no behaviour change); it stops the list
growing unnoticed and keeps the exceptions honest. Narrowing comes in later slices.

"Called by the UI" means the name appears as a quoted string ("x", 'x' or `x`) in app/ui/*.js or
app/ui/*.html: every UI helper (rpc, metaCall, platformCall, pollSidecar, attempt(() => rpc(...)))
passes the method name as a literal.
"""

from __future__ import annotations

import re
from pathlib import Path

from fabric_desktop import server

UI = Path(__file__).resolve().parents[2] / "app" / "ui"

_MCP = "MCP/HTTP tool calls the same Service method in-process (integrations/services.py); no screen"
_CLI = "CLI or headless agent calls the same Service method in-process (cli.py, agent.py); no screen"
_LOCAL = "local data projects: engine only, no screen yet (docs/local-data.md, QA-017, UX-004)"
_INCIDENTS = "incident desk stage 1: engine released, screens are stage 2 (docs/incidents.md)"
_NOTIFY = "governance Teams notices: governance sends the events; the UI only reads and sets routes (platform_notify_service.py)"
_QA017 = "kept for API compatibility, no screen yet (QA-017)"
_LOCAL_RUNS = "sharing this PC's notebook runs with Optimize: engine only, the button waits on a mockup (docs/optimize/README.md)"
_SUPERSEDED = "superseded on screen but kept for API compatibility (QA-017)"
_INTERNAL = "used in-process by another Service method; RPC name kept, no screen (QA-017)"
_NO_SCREEN = "engine method with no screen yet; kept for API compatibility (QA-017)"

# Allowlisted methods with no UI caller, each with why it stays. Remove a name when the UI starts
# calling it or when it leaves METHODS; the test below fails until you do.
# Dropping a name from METHODS? Also drop it from the timeout list in app/src-tauri/src/watchdog.rs.
_OSSIE = "Semantic model tab's Ossie card: engine only, the card waits on Chris's mockup review (docs/semantic-model-ossie.md)"

_DF1 = "Data Factory DF-1: Fabric pipeline runs in Monitor, engine only; the rows wait on Chris's mockup review"

NO_UI_CALLER: dict[str, str] = {
    "fabric_pipeline_runs": _DF1,
    "fabric_pipeline_activity_runs": _DF1,
    # Export to Ossie / Import from Ossie.
    "meta_model_ossie_models": _OSSIE,
    "meta_model_ossie_export": _OSSIE,
    "meta_model_ossie_save": _OSSIE,
    "meta_model_ossie_read": _OSSIE,
    "meta_model_ossie_preview": _OSSIE,
    "meta_model_ossie_import": _OSSIE,
    "meta_model_tmdl_save": _OSSIE,
    # MCP / local HTTP API tools (in-process).
    "placement_advice": _MCP + "; Prove shows it later (docs/finops/placement-advice.md)",
    "notebook_environment": _MCP,
    "pipeline_activity_registry": _MCP,
    "pipeline_inspect": _MCP,
    "pipeline_validate": _MCP,
    "receipt_export": _MCP,
    "receipts_list": _MCP,
    "run_sql": _MCP,
    # CLI and headless agent (in-process).
    "preview_job": _CLI,
    "run_job": _CLI,
    # Local data projects.
    "list_local_tables": _LOCAL,
    "local_add_source": _LOCAL,
    "local_close_project": _LOCAL,
    "local_create_project": _LOCAL,
    "local_change_delta": _LOCAL,
    "local_delta_history": _LOCAL,
    "local_explain": _LOCAL,
    "local_transform_plan": _LOCAL,
    "local_transform_run": _LOCAL,
    "local_transform_runs": _LOCAL,
    "local_drop_delta": _LOCAL,
    "local_forget_project": _LOCAL,
    "local_project_settings": _LOCAL,
    "local_query": _LOCAL,
    "local_query_cancel": _LOCAL,
    "local_query_start": _LOCAL,
    "local_query_status": _LOCAL,
    "local_refresh": _LOCAL,
    "local_remove_source": _LOCAL,
    "local_set_types": _LOCAL,
    "local_write_delta": _LOCAL,
    # Engine features whose screens come later.
    "incidents_board": _INCIDENTS,
    "incident_report": _INCIDENTS,
    "incident_set_priority": _INCIDENTS,
    "incident_sla_policy": _INCIDENTS,
    "incidents_history": _INCIDENTS,
    "incidents_history_sync": _INCIDENTS,
    "platform_notify": _NOTIFY,
    "governance_version": _NO_SCREEN,
    "kernel_info": _NO_SCREEN,
    "meta_git_status": _NO_SCREEN,
    "optimize_inspect": _NO_SCREEN,
    "optimize_share_local_runs": _LOCAL_RUNS,
    "risk_acknowledgements": _NO_SCREEN,
    "sql_compat_profiles": _NO_SCREEN,
    "sql_dialects": _NO_SCREEN,
    "sql_executions": _NO_SCREEN,
    "usage_imports": _NO_SCREEN,
    "usage_reconciliation": _NO_SCREEN,
    # Superseded on screen.
    "export_sql_csv": _SUPERSEDED,  # the SQL page uses sql_export
    "integrations_approvals": _SUPERSEDED,  # Settings > Integrations uses integrations_pending
    "run_cell": _SUPERSEDED,  # notebooks use start_cell / poll_cell
    # Reused in-process by another method.
    "all_shortcuts": _INTERNAL,  # shortcut_service
    "list_shortcuts": _NO_SCREEN,  # removal candidate: estate/shortcut services call api.list_shortcuts, not this
    "meta_preflight": _INTERNAL,  # risk_service
    "git_path_info": _INTERNAL,  # git_service
    "git_repos": _INTERNAL,  # git_service
    # Listed by QA-017 as having no screen.
    "audit_log": _QA017,
    "delete_notebook": _QA017,
    "git_ops": _QA017,
    "git_scan": _QA017,
    "meta_diff": _QA017,
    "meta_preview": _QA017,
    "notebook_diff": _QA017,
    "platform_revoke": _QA017,
    "platform_sources": _QA017,
    "workload_actions": _QA017,
}


def _ui_names(methods: set[str]) -> set[str]:
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(UI.iterdir()) if p.suffix in (".js", ".html"))
    return {m for m in methods if re.search(r"""["'`]%s["'`]""" % re.escape(m), text)}


def test_every_rpc_method_has_a_ui_caller_or_a_listed_reason():
    called = _ui_names(set(server.METHODS))
    assert len(called) > 300, "the UI scan found almost nothing; did app/ui move?"
    missing = sorted(set(server.METHODS) - called - set(NO_UI_CALLER))
    assert not missing, ("Allowlisted in server.METHODS but nothing in app/ui calls them. Call them from the UI, "
                         f"drop them from METHODS, or list them in NO_UI_CALLER with a reason: {missing}")


def test_the_exception_list_stays_honest():
    gone = sorted(set(NO_UI_CALLER) - set(server.METHODS))
    assert not gone, f"NO_UI_CALLER lists names that are no longer in server.METHODS: {gone}"
    now_called = sorted(_ui_names(set(NO_UI_CALLER)))
    assert not now_called, f"NO_UI_CALLER lists names the UI now calls; remove them from the list: {now_called}"
    assert all(reason.strip() for reason in NO_UI_CALLER.values())
