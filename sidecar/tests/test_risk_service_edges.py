"""The predeployment risk check's edges (risk_service.py): settings refused, capacity readings that
fail or aren't granted, imported usage that can't be read, and a notebook that can't be loaded. A
stand-in service supplies the facts; nothing reads Fabric."""

import types

import pytest

from fabric_desktop import risk_service as rs
from fabric_desktop.fabric_api import FabricError

WS = "ws-1"


class Svc(rs.RiskMixin):
    def __init__(self, health=None, clients=None):
        self.settings, self._health, self._clients = {}, health, clients or {}

    def _settings(self):
        return self.settings

    def _save_settings(self, **kw):
        self.settings.update(kw)

    def capacity_health(self, refresh=False):
        if isinstance(self._health, Exception):
            raise self._health
        return self._health or {"environments": []}

    def _silent_client(self, scopes):
        return self._clients.get(tuple(scopes))


def test_negative_thresholds_are_refused():
    svc = Svc()
    with pytest.raises(ValueError, match="CU hours threshold can't be negative"):
        svc.set_risk_settings(warn_run_cu_hours=-1)
    with pytest.raises(ValueError, match="warn failed runs can't be negative"):
        svc.set_risk_settings(warn_failed_runs=-2)
    assert svc.set_risk_settings(warn_run_cu_hours=0)["warn_run_cu_hours"] is None
    assert svc.set_risk_settings(warn_failed_runs="3", schedule_window_minutes=0)["warn_failed_runs"] == 3


def test_a_failed_environment_reading_falls_back_to_no_reading_when_signed_out(monkeypatch):
    warned = []
    monkeypatch.setattr(rs.log, "warning", lambda *a, **k: warned.append(a))
    reads = []
    assert Svc(health=RuntimeError("capacity reading broke"))._risk_capacity_read(WS, "Sales", False, reads) is None
    assert warned and reads == ["The environments' capacity reading failed (capacity reading broke); the workspace was read instead"]


def test_a_refreshed_environment_reading_says_what_it_read():
    health = {"environments": [{"env": "dev", "workspace_id": WS, "observed_at": None}]}
    reads = []
    cap = Svc(health=health)._risk_capacity_read(WS, "Sales", True, reads)
    assert cap["env"] == "dev"
    assert reads == ["Capacity of the environments (GET /workspaces/{id}, GET /capacities)"]


def _workspace_svc(cap_client, monkeypatch):
    seen = {}

    def observe_env(env, ws, name, get_workspace, caps, cap_error, now):
        seen.update(caps=caps, cap_error=cap_error)
        return {"workspace_id": ws, "observed_at": None}
    monkeypatch.setattr(rs.capacity_mod, "observe_env", observe_env)
    fabric = types.SimpleNamespace(get_workspace=lambda ws: {})
    clients = {tuple(rs.FABRIC_SCOPES): fabric}
    if cap_client is not None:
        clients[tuple(rs.CAPACITY_SCOPES)] = cap_client
    return Svc(clients=clients), seen


def test_a_workspace_outside_the_environments_is_read_with_its_capacities(monkeypatch):
    caps = types.SimpleNamespace(list_capacities=lambda: [{"id": "CAP-1", "sku": "F2"}, {"name": "no id"}])
    svc, seen = _workspace_svc(caps, monkeypatch)
    reads = []
    svc._risk_capacity_read(WS, "Other", False, reads)
    assert seen == {"caps": {"cap-1": {"id": "CAP-1", "sku": "F2"}}, "cap_error": None}
    assert reads == ["Capacity of the workspace (GET /workspaces/{id}, GET /capacities)"]


def test_capacities_without_the_scope_read_as_not_granted(monkeypatch):
    svc, seen = _workspace_svc(None, monkeypatch)
    reads = []
    svc._risk_capacity_read(WS, "Other", False, reads)
    assert seen["cap_error"] == "scope_not_granted" and reads == ["Capacity of the workspace (GET /workspaces/{id})"]


@pytest.mark.parametrize("status, expected", [(403, "scope_not_granted"), (404, "scope_not_granted")])
def test_a_refused_capacity_list_reads_as_not_granted(monkeypatch, status, expected):
    def refuse():
        raise FabricError("refused", status=status)
    svc, seen = _workspace_svc(types.SimpleNamespace(list_capacities=refuse), monkeypatch)
    svc._risk_capacity_read(WS, "Other", False, [])
    assert seen["cap_error"] == expected


def test_a_capacity_list_that_fails_otherwise_keeps_its_class(monkeypatch):
    def busy():
        raise FabricError("busy", status=500)
    monkeypatch.setattr(rs.capacity_mod, "classify_error", lambda exc: "unavailable")
    svc, seen = _workspace_svc(types.SimpleNamespace(list_capacities=busy), monkeypatch)
    svc._risk_capacity_read(WS, "Other", False, [])
    assert seen["cap_error"] == "unavailable"


def test_unreadable_usage_imports_still_give_estimates(monkeypatch):
    warned = []
    monkeypatch.setattr(rs.log, "warning", lambda *a, **k: warned.append(a))
    svc = Svc()
    svc.fabric_runs = lambda limit, refresh: []
    svc._cost_settings = lambda: {}
    monkeypatch.setattr(rs.costs_mod, "activities", lambda *a: [
        {"where": "fabric", "fabric_job_cu": {"run_id": "j1", "cu_hours": 0.5}},
        {"where": "fabric", "fabric_job_cu": {}}, {"where": "this_pc", "fabric_job_cu": {"run_id": "x"}}])

    def broken(*a):
        raise OSError("usage export locked")
    svc._usage_reconcile = broken
    measured, est, problem = svc._risk_cu_index()
    assert measured == {} and est == {"j1": {"run_id": "j1", "cu_hours": 0.5}}
    assert problem == "Imported usage couldn't be read (usage export locked), so nothing is measured."
    assert warned


def test_only_a_notebook_has_a_fabric_step():
    with pytest.raises(ValueError, match="Only a notebook has a Fabric step"):
        Svc()._risk_record("pipeline:daily.json", rs.FABRIC_STEP)


def test_an_action_the_workload_doesnt_support_here_is_refused():
    w = {"id": "x", "name": "Orders", "deployments": [{"env": "dev", "actions": ["run"], "location": {"kind": "this_pc"}}]}
    with pytest.raises(ValueError, match="Orders doesn't support that here"):
        Svc()._risk_facts(w, "run_in_fabric", "dev", False)


def test_a_notebook_that_cant_be_loaded_has_no_compatibility_facts():
    svc = Svc()
    svc._wl_token = lambda scopes: None

    def gone(name):
        raise OSError("notebook file missing")
    svc.notebooks = types.SimpleNamespace(load=gone)
    w = {"id": "notebook:nb", "name": "nb", "kind": "notebook", "runs": [],
         "deployments": [{"env": None, "actions": ["run"], "location": {"kind": "this_pc"}}]}
    facts = svc._risk_facts(w, "run", None, False)
    assert facts["compatibility"] is None and facts["where"] == "this_pc"


def test_a_notebook_that_loads_gets_a_compatibility_check(monkeypatch):
    svc = Svc()
    svc._wl_token = lambda scopes: "tok"
    svc._checks = lambda name: {}
    svc.notebooks = types.SimpleNamespace(load=lambda name: {"cells": [], "lakehouse": None})
    monkeypatch.setattr(rs, "compute_check", lambda cells, lakehouse, **k: {"ok": True})
    w = {"id": "notebook:nb", "name": "nb", "kind": "notebook", "runs": [],
         "deployments": [{"env": None, "actions": ["run"], "location": {"kind": "this_pc"}}]}
    facts = svc._risk_facts(w, "run", None, False)
    assert facts["compatibility"] == {"ok": True} and facts["signed_in"] is True


def test_the_run_dialog_skips_actions_that_start_no_work():
    assert Svc()._risk_for_preflight({}, "cancel", None) is None


def test_no_acknowledgements_file_reads_as_none(tmp_path):
    svc = Svc()
    svc.root = tmp_path
    assert svc.risk_acknowledgements() == []
    svc._risk_ack_write({"id": "a"})
    svc._risk_ack_write({"id": "b"})
    assert [r["id"] for r in svc.risk_acknowledgements(limit=1)] == ["b"]


def test_placement_advice_reads_valid_proofs_and_finished_side_by_side_runs(monkeypatch):
    svc = Svc()
    svc.notebooks = types.SimpleNamespace(load=lambda name: {"cells": [{"source": "x = 1"}], "lakehouse": None})
    svc.receipts_list = lambda name, limit: [{"id": "r1", "valid": True, "kind": "proof"},
                                             {"id": "r2", "valid": False, "kind": "proof"},
                                             {"id": "r3", "valid": True, "kind": "verify"}]
    svc.receipt_get = lambda id: {"id": id}
    svc.side_by_side_list = lambda name, limit: [{"state": "done", "id": "s1"}, {"state": "failed"}]
    svc._checks = lambda name: {}
    monkeypatch.setattr(rs, "compute_check", lambda cells, lh, **k: {"ok": True})
    seen = []
    monkeypatch.setattr(rs.advisor, "advise", lambda *a: seen.append(a) or {"where": "this_pc"})
    assert svc.placement_advice("nb") == {"where": "this_pc"}
    name, revision, compat, proofs, done = seen[0]
    assert name == "nb" and compat == {"ok": True} and proofs == [{"id": "r1"}] and done == [{"state": "done", "id": "s1"}]
