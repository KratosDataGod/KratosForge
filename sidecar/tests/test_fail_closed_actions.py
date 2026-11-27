"""WR-410: every Prod, approve, confirm and typed-name action runs only once its "started" audit line is
written, and is refused when that line can't be written (audit.fails_closed, server._audit_started).

ACTIONS lists each such action the engine takes over RPC, with what makes it fail closed: "always" (it
is in audit.FAIL_CLOSED), or the parameters whose go-ahead (a confirmation, an approval, a typed name, a
risk acknowledgement, an override, or an env or target of Prod) makes it so. The scan below finds them
on the dispatch surface (server.METHODS) by name and by parameter, so a new one fails here until it is
listed, and a listed one that stops failing closed fails too. NOT_ACTIONS says why a method the scan
finds needs no record first, and PREVIEWS why a read-only call named like an action changes nothing.
"""

from __future__ import annotations

import functools
import inspect
import json
import re
import typing
from typing import Any

import pytest

from fabric_desktop import audit, rpc_params, server
from fabric_desktop.service import Service

ALWAYS = "always"

ACTIONS: dict[str, str] = {
    # In audit.FAIL_CLOSED: Prod-reaching changes, deletes in Fabric or the shared catalog, removed access,
    # an accepted shortcut risk, and an access review's confirmation.
    "access_delete_role": ALWAYS,
    "delete_fabric_schedule": ALWAYS,
    "delete_notebook_schedule": ALWAYS,
    "delete_shortcut": ALWAYS,
    "governance_template_delete": ALWAYS,
    "governance_unassign": ALWAYS,
    "integrations_revoke_client": ALWAYS,
    "meta_delete_schedule": ALWAYS,
    "meta_promote": ALWAYS,
    "meta_restore": ALWAYS,
    "meta_rollback": ALWAYS,
    "platform_confirm_access": ALWAYS,
    "platform_delete_domain": ALWAYS,
    "platform_delete_landing_zone": ALWAYS,
    "platform_delete_source": ALWAYS,
    "platform_remove_admin": ALWAYS,
    "platform_remove_product_item": ALWAYS,
    "platform_revoke": ALWAYS,
    "prove_delete_outputs": ALWAYS,
    "replay_delete_outputs": ALWAYS,
    "shortcut_accept_risk": ALWAYS,
    "shortcut_repoint": ALWAYS,
    "shortcut_repoint_many": ALWAYS,
    "shortcut_undo": ALWAYS,
    "xray_optimize": ALWAYS,
    "xray_remove_staging": ALWAYS,
    "xray_vacuum": ALWAYS,
    # Typed names: the Prod workspace's name, or a Databricks target's.
    "appstudio_publish": "confirm_name replace",
    "cp_adopt": "confirm_name confirmed",
    "cp_create": "confirm_name confirmed",
    "cp_upgrade": "confirm_name confirmed",
    "databricks_bundle_deploy": "allow_commands target typed",
    "fabric_git_update": "allow_override confirm_name env",
    "notebook_prod_confirm": "confirm_name",
    "save_sql": "confirm_name",
    "sql_start": "allow confirm confirm_name target",
    # Approvals and decisions.
    "access_cache_protect": "approve",
    "access_cache_refresh": "approve",
    "capacity_settings_read": "approve",
    "editor_decide_pairing": "approve",
    "editor_decide_write": "approve",
    "incidents_history_sync": "approve",
    "incidents_measures": "approve env",
    "integrations_decide": "approval_id approve",
    "optimization_share": "approve confirm",
    "optimize_share_local_runs": "approve confirm",
    "platform_cancel_share_keys": "approve",
    "platform_decide": "approve env",
    "platform_share_keys": "approve",
    "tenant_settings_read": "approve",
    # Confirmations.
    "databricks_billing": "confirmed",
    "snowflake_query_credits": "confirmed",
    "databricks_sync_push": "confirm_deletes",
    "databricks_volume_delete": "confirm",
    "databricks_volume_download": "overwrite",
    "databricks_volume_drop": "confirm",
    "databricks_volume_upload": "overwrite",
    "engine_select": "confirmed",
    "fabric_git_link": "confirm env",
    "git_abort_merge": "confirm",
    "git_apply_hunk": "confirm",
    "git_capacity_write": "confirm",
    "git_delete_branch": "confirm force",
    "git_discard": "confirm",
    "git_platform_deploy": "confirm",
    "git_platform_export": "confirm",
    "git_set_kind": "confirm",
    "local_change_delta": "confirm",
    "local_drop_delta": "confirm",
    "local_query": "confirm",
    "local_query_start": "confirm",
    "local_write_delta": "confirm",
    "meta_model_dictionary_import": "confirmed",
    "meta_model_ossie_import": "confirmed env",
    "meta_model_ossie_to_tmdl": "confirmed",
    "meta_model_semantic_view_save": "confirmed",
    "side_by_side_start": "confirm_unknown confirm_writes",
    "wh_monitoring_build": "confirm",
    # Risk acknowledgements and overrides.
    "git_commit": "allow_secrets",
    "meta_optimize": "env risk_ids",
    "model_bpa_check": "env risk_id",
    "notebook_validate_start": "allow_uncontained",
    "pipeline_run_start": "allow_uncontained",
    "risk_acknowledge": "env risk_id",
    "set_risk_settings": "require_acknowledgement",
    # Go-aheads to replace what is there.
    "engine_connection_save": "replace",
    "meta_build_model": "env replace",
    "notebook_batch_fabric": "replace_fabric_changes",
    "publish_notebook": "replace_fabric_changes",
    "run_notebook_in_fabric": "replace_fabric_changes",
    "sql_file_write": "overwrite",
    "wh_project_write": "replace",
    # Prod actions: each fails closed when its env, envs or target names Prod.
    "add_pc_schedule": "target",
    "appstudio_create": "target",
    "appstudio_plan": "target",
    "fabric_git_unlink": "env",
    "git_set_locations": "envs",
    "governance_assign": "target",
    "incident_acknowledge": "env",
    "incident_assign": "env",
    "incident_note": "env",
    "incident_report": "env",
    "incident_resolve": "env",
    "incident_set_priority": "env",
    "incident_tell_impacted": "env",
    "incidents_evaluate": "env",
    "mesh_publish": "env",
    "mesh_resolve_links": "env",
    "meta_add_schedule": "env",
    "meta_create_designation_workspace": "env",
    "meta_create_workspace": "env",
    "meta_data_health_quiet": "env",
    "meta_data_health_watch": "env",
    "meta_delete_key_table": "env",
    "meta_delete_source_system": "env",
    "meta_discard_quarantine": "env",
    "meta_keys_sync": "env",
    "meta_keys_to_lakehouse": "env",
    "meta_keys_to_platform": "env",
    "meta_model_tmdl_save": "env",
    "meta_new_key_table": "env",
    "meta_openlineage_export": "env",
    "meta_profile": "env",
    "meta_publish_varlib": "env",
    "meta_replay": "env",
    "meta_run": "env",
    "meta_save_entity": "confirm_name",
    "meta_save_source_system": "env",
    "meta_set_compute": "env",
    "meta_set_designation": "env",
    "meta_set_delta_features": "confirm_name",
    "meta_set_environment": "env target",
    "meta_set_freshness": "envs",
    "meta_set_keys_workspace": "env",
    "meta_set_maintenance": "env",
    "meta_source_read": "env",
    "meta_sources_move": "env",
    "platform_entra_link": "env envs",
    "platform_entra_save": "envs",
    "platform_entra_unlink": "env",
    "platform_forget": "env",
    "platform_join": "env",
    "platform_request": "target",
    "platform_setup": "env",
    "sql_compat_export": "target",
    "workload_act": "env",
}

# Audited methods named like an approval, a confirmation or a Prod change that take no go-ahead and
# needn't fail closed, each with why.
NOT_ACTIONS: dict[str, str] = {
    "appstudio_restore_version": "puts back a saved version of a project on this PC; what was current is kept as a version first",
    "git_restore_stash": "applies a stash to the local working tree; nothing in Fabric or the remote changes",
    "notebook_restore": "brings a notebook back from Recently deleted on this PC",
    "notebook_version_restore": "puts a saved version's cells back; the notebook as it was is kept first as a version",
}

# Read-only calls (audit.READ_ONLY) named like an action or taking a go-ahead, each with why it changes
# nothing. They are never audited, so fails_closed never applies to them: a change must not hide here.
PREVIEWS: dict[str, str] = {
    "appstudio_publish_check": "what publishing would do; appstudio_publish makes the change",
    "cp_adopt_preview": "the manifest adopting would write; cp_adopt writes it",
    "cp_upgrade_plan": "what upgrading would do; cp_upgrade makes the change",
    "databricks_bundle_plan": "databricks bundle plan; changes nothing in Databricks (allow_commands lets the plan run the bundle's own scripts)",
    "databricks_bundle_validate": "databricks bundle validate; changes nothing in Databricks",
    "meta_promote_check": "what promoting would do; meta_promote makes the change",
    "meta_promote_impact": "what promoting would affect; meta_promote makes the change",
    "meta_rollback_preview": "what rolling back would do; meta_rollback makes the change",
    "notebook_cleanup_apply": "returns the tidied cells; nothing is saved, the app puts them in the notebook",
    "notebook_delete_check": "what deleting would touch; notebook_delete makes the change",
    "notebook_formats_import_check": "what importing would create; nothing is written",
    "notebook_import_check": "what importing would create; nothing is written",
    "notebook_publish_check": "what publishing would do; publish_notebook makes the change",
    "pipeline_import": "reads a pipeline definition; stores nothing",
    "pipeline_run_check": "what a local pipeline run would do; pipeline_run_start runs it (allow_uncontained is checked there too)",
    "wh_project_deploy_plan": "the statements a deploy would run; nothing runs",
    "xray_adopt_plan": "the entities adopting would make; xray_adopt makes them",
}

_NAMED_ACTION = re.compile(r"(^|_)(prod|approve|approval|confirm|decide|accept|acknowledge|typed|promote|rollback|"
                           r"restore|deploy|revoke|grant|danger)(_|$)")
_NAMED_CHANGE = re.compile(r"(^|_)(apply|confirm|approve|decide|accept|acknowledge|deploy|publish|promote|rollback|"
                           r"restore|revoke|grant|grants|delete|remove|drop|overwrite|replace|upgrade|adopt|share|import)(_|$)")
_GATES = audit.GUARD_PARAMS | set(audit._PROD_PARAMS)


def _params(method: str) -> list[str]:
    return [p for p in inspect.signature(getattr(Service, method)).parameters if p != "self"]


def _gates(method: str) -> list[str]:
    return [] if ACTIONS[method] == ALWAYS else ACTIONS[method].split()


def _found() -> set[str]:
    """Every audited RPC method that is in FAIL_CLOSED, takes a go-ahead or an environment, or is named
    like an approval, a confirmation or a Prod change."""
    return {m for m in server.METHODS if audit.audited(m)
            and (m in audit.FAIL_CLOSED or set(_params(m)) & _GATES or _NAMED_ACTION.search(m))}


def test_every_prod_approve_confirm_and_typed_name_action_is_listed():
    unlisted = sorted(_found() - set(ACTIONS) - set(NOT_ACTIONS))
    assert not unlisted, ("A new Prod, approve, confirm or typed-name action: list it in ACTIONS with what makes it "
                          f"fail closed (add it to audit.FAIL_CLOSED if nothing does), or say in NOT_ACTIONS why not: {unlisted}")


def test_the_lists_have_no_stale_names():
    found = _found()
    assert sorted(set(ACTIONS) - found) == [], "listed in ACTIONS but no longer such an action on the RPC surface"
    assert sorted(set(NOT_ACTIONS) - found) == [], "listed in NOT_ACTIONS but no longer found by the scan"
    assert not set(ACTIONS) & set(NOT_ACTIONS)
    assert all(len(why) >= 20 for why in (*NOT_ACTIONS.values(), *PREVIEWS.values()))


def test_every_listed_action_fails_closed_on_its_go_ahead():
    for method, gate in ACTIONS.items():
        assert audit.audited(method), method
        if gate == ALWAYS:
            assert method in audit.FAIL_CLOSED and audit.fails_closed(method, {}), method
            continue
        assert method not in audit.FAIL_CLOSED, f"{method} is in FAIL_CLOSED: list it as always"
        # every go-ahead it takes is listed, so a new one on an existing action is reviewed too
        assert sorted(_gates(method)) == sorted(set(_params(method)) & _GATES), method
        for name in _gates(method):
            assert audit.fails_closed(method, {name: "prod" if name in audit._PROD_PARAMS else True}), (method, name)


def test_every_name_in_fail_closed_is_listed_as_always():
    assert sorted(m for m in audit.FAIL_CLOSED if ACTIONS.get(m) != ALWAYS) == []


def test_no_unlisted_change_hides_among_the_read_only_calls():
    previews = {m for m in server.METHODS if not audit.audited(m)
                and (_NAMED_CHANGE.search(m) or set(_params(m)) & audit.GUARD_PARAMS)}
    assert sorted(previews - set(PREVIEWS)) == [], "a read-only call named like a change: say in PREVIEWS why it changes nothing"
    assert sorted(set(PREVIEWS) - previews) == [], "listed in PREVIEWS but no longer a read-only call the scan finds"
    assert not audit.FAIL_CLOSED & audit.READ_ONLY


def test_actions_added_since_the_last_fail_closed_review_are_covered():
    """The calls added after PR #3068: the KratosForge Environment's changes, the semantic view's save
    and the OpenLineage export."""
    assert {"cp_create", "cp_adopt", "cp_upgrade", "meta_model_semantic_view_save", "meta_openlineage_export"} <= set(ACTIONS)
    for method in ("cp_create", "cp_adopt", "cp_upgrade"):
        assert audit.fails_closed(method, {"token": "t", "confirmed": True})
        assert audit.fails_closed(method, {"token": "t", "confirm_name": "Sales Prod"})
    assert audit.fails_closed("meta_model_semantic_view_save", {"view_name": "v", "text": "x", "confirmed": True})
    assert audit.fails_closed("meta_openlineage_export", {"env": "prod"})


# ---- behaviour: through server.handle, with the audit file unwritable


def _value(kinds: tuple[type, ...] | None, gate: str = "") -> Any:
    """A value that fits the parameter's type: a go-ahead (or Prod) for ``gate``, a plain one otherwise."""
    kinds = kinds or (str,)
    if gate in audit._PROD_PARAMS:
        return "prod" if str in kinds else ["prod"] if list in kinds else {"env": "prod"}
    plain = {str: "Sales Prod" if gate else "x", bool: bool(gate), int: 1, float: 1.0,
             list: ["r-1"] if gate else [], dict: {"env": "dev"}, type(None): None}
    if gate and bool in kinds:
        return True
    return next(plain[k] for k in kinds if k in plain)


def _calls(method: str) -> list[dict[str, Any]]:
    """One call per go-ahead the method takes (one with only its required parameters when it is always
    protected), each with that go-ahead given."""
    func = getattr(Service, method)
    hints = typing.get_type_hints(func)
    required = [p for p in inspect.signature(func).parameters.values() if p.name != "self" and p.default is p.empty]
    base = {p.name: _value(rpc_params._kinds(hints.get(p.name, Any))) for p in required}
    if ACTIONS[method] == ALWAYS:
        return [base]
    return [base | {g: _value(rpc_params._kinds(hints.get(g, Any)), g)} for g in _gates(method)]


class Broken:
    """An audit log that can't be written (a full disk, a locked file)."""

    def record(self, *a, **k):
        raise OSError("disk full")


@pytest.fixture
def service(tmp_path, monkeypatch):
    """A Service whose listed actions only note that they ran, keeping their real signatures so the
    RPC parameter check still runs."""
    ran: list[str] = []
    for method in ACTIONS:
        real = getattr(Service, method)

        @functools.wraps(real)
        def stand_in(self, *a, _m=method, **k):
            ran.append(_m)
            return {}
        monkeypatch.setattr(Service, method, stand_in)
    svc = Service(client_id="x", root=tmp_path)
    svc.ran = ran  # type: ignore[attr-defined]
    return svc


def test_no_listed_action_runs_when_its_started_line_cant_be_written(service):
    service._audit_log = Broken()
    refused = 0
    for method in sorted(ACTIONS):
        for i, params in enumerate(_calls(method)):
            assert audit.fails_closed(method, params), (method, params)
            reply = server.handle(service, json.dumps({"id": i, "method": method, "params": params}))
            assert "audit log couldn't be written, so this didn't run (OSError: disk full)" in reply.get("error", ""), \
                (method, params, reply)
            refused += 1
    assert service.ran == [] and refused > len(ACTIONS)


def test_every_listed_action_writes_its_started_line_before_it_runs(service, monkeypatch):
    real = service.audit.record
    monkeypatch.setattr(service.audit, "record",
                        lambda action, outcome, *a, **k: service.ran.append(f"{action}:{outcome}") or real(action, outcome, *a, **k))
    for method in sorted(ACTIONS):
        for i, params in enumerate(_calls(method)):
            service.ran.clear()
            reply = server.handle(service, json.dumps({"id": i, "method": method, "params": params}))
            assert reply.get("result") == {}, (method, params, reply)
            assert service.ran == [f"{method}:started", method, f"{method}:ok"], (method, params)
