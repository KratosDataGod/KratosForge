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

import json
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
_BPA = "best practices check of a semantic model: engine first, screens wait on the mockup (docs/semantic-model-best-practices.md)"
_NO_SCREEN = "engine method with no screen yet; kept for API compatibility (QA-017)"

# Allowlisted methods with no UI caller, each with why it stays. Remove a name when the UI starts
# calling it or when it leaves METHODS; the test below fails until you do.
# Dropping a name from METHODS? Also drop it from the timeout list in app/src-tauri/src/watchdog.rs.
_PRACTICES = "the notebook rules list, for a template editor to list them; the panel shows findings only (docs/notebook-best-practices.md)"

_SQL_PRACTICES = "the SQL rules list, for a template editor to list them; the SQL page shows findings only (docs/sql-best-practices.md)"


NO_UI_CALLER: dict[str, str] = {
    "notebook_practice_rules": _PRACTICES,
    "sql_practice_rules": _SQL_PRACTICES,
    # MCP / local HTTP API tools (in-process).
    "placement_advice": _MCP + "; Prove shows it later (docs/finops/placement-advice.md)",
    "notebook_environment": _MCP,
    "pipeline_activity_registry": _MCP,
    "pipeline_inspect": _MCP,
    "pipeline_validate": _MCP,
    "receipt_export": _MCP,
    "receipts_list": _MCP,
    "run_sql": _MCP,
    # Best practices check (semantic-link-labs in Fabric).
    "model_bpa_status": _BPA,
    "model_bpa_risk": _BPA,
    "model_bpa_check": _BPA,
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
    "meta_preflight": _NO_SCREEN,  # the PB-067 run dialog (#915, after a mockup) shows its estimate
    # Listed by QA-017 as having no screen.
    "audit_log": _QA017,
    "delete_notebook": _QA017,
    "git_ops": _QA017,
    "git_scan": _QA017,
    "meta_diff": _QA017,
    "meta_preview": _QA017,
    "notebook_diff": _QA017,
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


# HD-050 slice 2 (#927): names no screen called, taken out of METHODS. The Service methods stay, because
# git_service, shortcut_service and pipeline_service still call git_path_info, all_shortcuts and git_repos.
REMOVED = ("export_sql_csv", "integrations_approvals", "all_shortcuts", "git_path_info", "git_repos",
           "list_shortcuts",
           # Slice 3 (#965): the app runs cells with start_cell / poll_cell, and so do the engine tests
           # (test_prove.run_cell). Service.run_cell stays for in-process tests and scripts.
           "run_cell")


def test_methods_no_screen_called_are_refused_over_rpc(tmp_path):
    from fabric_desktop.service import Service
    svc = Service(client_id="x", root=tmp_path)
    for name in REMOVED:
        reply = server.handle(svc, json.dumps({"id": 1, "method": name, "params": {}}))
        assert reply == {"id": 1, "error": f"unknown method {name!r}"}, name
        assert callable(getattr(svc, name)), name
