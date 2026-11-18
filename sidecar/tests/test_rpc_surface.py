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

_LOCAL = "local data projects: engine only, no screen yet (docs/local-data.md, QA-017, UX-004)"
_INCIDENTS = "incident history: the engine is released, its screen is stage 4 (docs/incidents.md)"
_NOTIFY = "governance Teams notices: governance sends the events; the UI only reads and sets routes (platform_notify_service.py)"
_LOCAL_RUNS = "sharing this PC's notebook runs with Optimize: engine only, the button waits on a mockup (docs/optimize/README.md)"
_BPA = "best practices check of a semantic model: engine first, screens wait on the mockup (docs/semantic-model-best-practices.md)"
_QA017 = "kept for API compatibility, no screen yet (QA-017)"
_NO_SCREEN = "engine method with no screen yet; kept for API compatibility (QA-017)"
_FABRIC_GIT = "Fabric workspace Git (Git-native Phase 2): engine first, the item support view comes with the Deploy work (docs/git-integration-plan.md)"

# Allowlisted methods with no UI caller, each with why it stays. Remove a name when the UI starts
# calling it or when it leaves METHODS; the test below fails until you do.
# Dropping a name from METHODS? Also drop it from the timeout list in app/src-tauri/src/watchdog.rs.

NO_UI_CALLER: dict[str, str] = {
    # OE-004: the Share button on Optimize > Assessment waits on its mockup.
    "optimization_share": "OE-004 engine call; the Assessment tab's Share button waits on its mockup",
    # PB-110: the Semantic model card's "Ossie file to TMDL" waits on Chris's mockup reaction (slice 3).
    "meta_model_ossie_to_tmdl": "engine first; the Semantic model card calls it once the PB-110 mockup is approved",
    "excellence_assess": "FEA-105 engine call; the Excellence scorecard (FEA-108 mockup) waits on Chris's reaction",
    # Best practices check (semantic-link-labs in Fabric).
    "model_bpa_status": _BPA,
    "model_bpa_risk": _BPA,
    "model_bpa_check": _BPA,
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
    "incidents_history": _INCIDENTS,
    "incidents_history_sync": _INCIDENTS,
    "incidents_measures": _INCIDENTS,
    "platform_notify": _NOTIFY,
    "optimize_share_local_runs": _LOCAL_RUNS,
    "meta_preflight": _NO_SCREEN,  # the PB-067 run dialog (#915, after a mockup) shows its estimate
    "fabric_item_support": _FABRIC_GIT,
    "fabric_git_links": _FABRIC_GIT,  # the tab reads links through fabric_git_overview
    # Listed by QA-017 as having no screen.
    "platform_sources": _QA017,
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
           "run_cell",
           # Slice 4 (#1049): only the MCP tools, the local HTTP API, the CLI and the headless agent
           # used these, and they call the Service methods in-process (integrations/services.py,
           # cli.py, agent.py), never by RPC name.
           "placement_advice", "notebook_environment", "pipeline_activity_registry", "pipeline_inspect",
           "pipeline_validate", "receipt_export", "receipts_list", "run_sql", "preview_job", "run_job",
           "environment_fabric_drift",
           # Slice 5 (#1087, QA-017): nothing called these, in-process or by RPC name. The Service
           # methods stay for tests and engine code; a screen that needs one adds it back with its caller.
           "audit_log", "delete_notebook", "git_ops", "git_scan", "meta_diff", "meta_preview", "notebook_diff",
           "workload_actions", "governance_version", "meta_git_status", "usage_reconciliation",
           # Slice 6 (#1153): no screen, MCP tool or other caller used these by RPC name; the
           # tests call the Service methods in-process.
           "optimize_inspect", "risk_acknowledgements", "usage_imports",
           # Slice 7 (#1156): no screen called these; only engine and browser tests did, and they now call the
           # Service methods or a call the app makes. They also leave QUICK and watchdog.rs.
           "kernel_info", "sql_compat_profiles", "sql_dialects", "sql_executions")


def test_methods_no_screen_called_are_refused_over_rpc(tmp_path):
    from fabric_desktop.service import Service
    svc = Service(client_id="x", root=tmp_path)
    for name in REMOVED:
        reply = server.handle(svc, json.dumps({"id": 1, "method": name, "params": {}}))
        assert reply == {"id": 1, "error": f"unknown method {name!r}"}, name
        assert callable(getattr(svc, name)), name


# WR-613 (QA-017): a method with no screen can't lean on a screen's confirm dialog, so its destructive
# step must wait for a go-ahead parameter the engine checks itself. That parameter is one of
# audit.GUARD_PARAMS, so a call that passes it is recorded before it runs and refused when it can't
# be (fail closed), the same as when a screen sends it. Every no-screen method that changes something
# is listed here: with the parameter its destructive step needs, or with why it has none.
NO_SCREEN_GATE: dict[str, str] = {
    "meta_model_ossie_to_tmdl": "confirmed",   # without it, only the preview: files, removals, report
    "incidents_history_sync": "approve",       # sets up the central incident tables only when approved
    "incidents_measures": "approve",           # copies through incidents_history_sync first
    "local_change_delta": "confirm",           # UPDATE or DELETE without WHERE
    "local_drop_delta": "confirm",             # without it, only says what would be deleted
    "local_query": "confirm",                  # DROP, TRUNCATE, CREATE OR REPLACE, INSERT OVERWRITE
    "local_query_start": "confirm",
    "local_write_delta": "confirm",            # mode overwrite, like INSERT OVERWRITE
    "optimization_share": "confirm",
    "optimize_share_local_runs": "confirm",
}
NOT_DESTRUCTIVE: dict[str, str] = {
    "local_add_source": "adds views over a folder; the files are only read",
    "local_close_project": "closes the database file; nothing is deleted",
    "local_create_project": "makes a new project",
    "local_forget_project": "takes the project off the list; its folder and database stay on disk",
    "local_project_settings": "changes the project's own settings",
    "local_query_cancel": "stops a query this PC started",
    "local_refresh": "re-reads the project's source folders",
    "local_remove_source": "drops the views over a folder; the files are never touched",
    "local_set_types": "changes how a file's columns are read; the file is untouched",
    "local_transform_run": "steps get local_query's checks with no confirm, so a destructive step is refused",
    "model_bpa_check": ("reads a semantic model in Fabric; the risk check runs first, a blocked one stops it "
                        "and a warning needs the risk_id the person acknowledged"),
    "platform_notify": "posts notices only to routes an admin saved, once per dedupe key; changes no data",
}


def test_every_no_screen_method_that_changes_something_waits_for_a_recorded_go_ahead():
    import inspect

    from fabric_desktop import audit
    from fabric_desktop.service import Service
    changing = {m for m in NO_UI_CALLER if audit.audited(m)}
    unlisted = sorted(changing - set(NO_SCREEN_GATE) - set(NOT_DESTRUCTIVE))
    assert not unlisted, ("These methods have no screen and change something. Give each a go-ahead parameter "
                          f"(audit.GUARD_PARAMS) in NO_SCREEN_GATE, or say in NOT_DESTRUCTIVE why it needs none: {unlisted}")
    stale = sorted((set(NO_SCREEN_GATE) | set(NOT_DESTRUCTIVE)) - changing)
    assert not stale, f"Listed but no longer a no-screen method that changes something: {stale}"
    assert not set(NO_SCREEN_GATE) & set(NOT_DESTRUCTIVE)
    for method, gate in NO_SCREEN_GATE.items():
        assert gate in audit.GUARD_PARAMS, (method, gate)
        assert gate in inspect.signature(getattr(Service, method)).parameters, (method, gate)
        assert audit.fails_closed(method, {gate: True}), (method, gate)
