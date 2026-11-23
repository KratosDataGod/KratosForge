"""The predeployment risk check (risk_service.py): what mutation batch 99 found the other tests let through.
Thresholds at zero, the capacity reading's fallbacks and message lengths, the facts gathered for each
kind of workload, which runs count, the unknown result when the check itself fails, and the
acknowledgements file. A stand-in service supplies the facts; nothing reads Fabric."""

from __future__ import annotations

import json
import types

import pytest

from fabric_desktop import risk_service as rs

from tests.test_risk_service_edges import WS, Svc


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(rs.log, "warning", lambda *a, **k: None)


# ---- settings

def test_a_zero_threshold_written_as_text_and_zero_failed_runs_are_accepted():
    cfg = Svc().set_risk_settings(warn_run_cu_hours="0.0", warn_failed_runs=0)
    assert cfg["warn_run_cu_hours"] is None and cfg["warn_failed_runs"] == 0  # 0 CU hours: no warning


def test_a_zero_threshold_written_as_text_turns_the_warning_off():
    assert Svc().set_risk_settings(warn_run_cu_hours="0")["warn_run_cu_hours"] is None


# ---- capacity

def test_a_failed_environment_reading_is_cut_to_two_hundred_characters():
    reads = []
    Svc(health=RuntimeError("x" * 300))._risk_capacity_read(WS, "Sales", False, reads)
    assert reads == [f"The environments' capacity reading failed ({'x' * 200}); the workspace was read instead"]


def test_only_the_environment_of_this_workspace_is_its_reading():
    health = {"environments": [{"env": "prod", "workspace_id": "other"}, {"env": "test"}]}
    assert Svc(health=health)._risk_capacity_read(WS, "Sales", False, []) is None  # signed out: no direct read


# ---- usage

def _usage_svc(monkeypatch, reconcile):
    svc = Svc()
    calls = {}

    def fabric_runs(limit, refresh):
        calls["runs"] = (limit, refresh)
        return []

    svc.fabric_runs = fabric_runs
    svc._cost_settings = lambda: {}
    monkeypatch.setattr(rs.costs_mod, "activities", lambda *a: [])

    def usage(days, fabric, rows):
        calls["days"] = days
        return reconcile()

    svc._usage_reconcile = usage
    return svc, calls


def test_the_cu_index_reads_five_hundred_runs_without_a_refresh_and_ninety_days_of_usage(monkeypatch):
    svc, calls = _usage_svc(monkeypatch, lambda: {"by_run": [{"job_id": "j", "measured": {"cu": 1}}, {"job_id": "k"}]})
    measured, est, problem = svc._risk_cu_index()
    assert calls == {"runs": (500, False), "days": 90}
    assert measured == {"j": {"cu": 1}} and est == {} and problem == ""


def test_unreadable_usage_is_cut_to_two_hundred_characters(monkeypatch):
    def broken():
        raise OSError("y" * 300)

    svc, _ = _usage_svc(monkeypatch, broken)
    assert svc._risk_cu_index()[2] == f"Imported usage couldn't be read ({'y' * 200}), so nothing is measured."


# ---- the record a Fabric step reads

def test_a_fabric_step_without_a_workspace_name_reads_as_blank():
    svc = Svc()
    svc.notebooks = types.SimpleNamespace(load=lambda name: {"lakehouse": {"workspace_id": WS}})
    w = svc._risk_record("notebook:nb", rs.FABRIC_STEP)
    assert w["deployments"][0]["location"] == {"kind": "fabric", "workspace_id": WS, "workspace_name": ""}


# ---- facts

def _facts_svc(**attrs):
    svc = Svc()
    svc._wl_token = lambda scopes: None
    svc.capacity_calls = []
    svc.cu_calls = []

    def capacity(ws, name, refresh, reads, memo=None):
        svc.capacity_calls.append((ws, name, refresh))
        return {"status": "running"}

    def cu(runs, memo=None):
        svc.cu_calls.append(runs)
        return [], [], ""

    svc._risk_capacity = capacity
    svc._risk_cu = cu
    for k, v in attrs.items():
        setattr(svc, k, v)
    return svc


def _w(kind="pipeline", runs=(), where="fabric", **dep):
    location = {"kind": "fabric", "workspace_id": WS, "workspace_name": "Sales"} if where == "fabric" else {"kind": "this_pc"}
    return {"id": f"{kind}:x", "name": "x", "kind": kind, "runs": list(runs),
            "deployments": [{"env": "dev", "actions": ["run", "run_in_fabric"], "location": location} | dep]}


def test_plain_facts_have_blank_defaults():
    facts = _facts_svc()._risk_facts(_w(where="this_pc"), "run", "dev", False)
    assert facts["env_label"] == "" and facts["definition_errors"] == [] and facts["schedules"] == []
    assert facts["needs_sign_in"] is False and "capacity" not in facts


def test_a_data_mesh_entity_needs_a_sign_in_unless_its_tables_are_on_this_pc():
    svc = _facts_svc(meta_preflight=lambda name, env: {"bytes": 1})
    assert svc._risk_facts(_w("meta_entity", where="this_pc"), "run", "dev", False)["needs_sign_in"] is True
    w = _w("meta_entity", where="this_pc", tables_in="This PC only")
    assert svc._risk_facts(w, "run", "dev", False)["needs_sign_in"] is False


def test_a_fabric_run_reads_its_workspaces_capacity():
    svc = _facts_svc()
    svc._risk_facts(_w(), "run_in_fabric", "dev", True)
    assert svc.capacity_calls == [(WS, "Sales", True)]


def test_only_runs_where_and_in_the_environment_this_action_runs_count():
    runs = [{"id": 1, "where": "fabric", "env": "dev"}, {"id": 2, "where": "fabric", "env": "prod"},
            {"id": 3, "where": "fabric"}, {"id": 4, "where": "this_pc", "env": "dev"}]
    svc = _facts_svc()
    facts = svc._risk_facts(_w(runs=runs), "run_in_fabric", "dev", False)
    assert [r["id"] for r in facts["runs"]] == [1, 3]
    assert [[r["id"] for r in c] for c in svc.cu_calls] == [[1, 3]]
    local = svc._risk_facts(_w(runs=runs, where="this_pc", env=None), "run", None, False)
    assert [r["id"] for r in local["runs"]] == [4] and "measured" not in local
    assert len(svc.cu_calls) == 1


def test_a_notebook_without_cells_is_checked_as_empty(monkeypatch):
    seen = []
    monkeypatch.setattr(rs, "compute_check", lambda cells, lakehouse, **k: seen.append(cells) or {"ok": True})
    svc = _facts_svc(notebooks=types.SimpleNamespace(load=lambda name: {}), _checks=lambda name: {})
    assert svc._risk_facts(_w("notebook", where="this_pc"), "run", "dev", False)["compatibility"] == {"ok": True}
    assert seen == [[]]


def test_a_data_mesh_entitys_input_is_read_for_a_run_only_in_dev_by_default():
    asked = []
    svc = _facts_svc(meta_preflight=lambda name, env: asked.append(env) or {"bytes": 1})
    facts = svc._risk_facts(_w("meta_entity", where="this_pc", env=None), "run", None, False)
    assert facts["input"] == {"bytes": 1} and "Delta log of the source (input size)" in facts["reads"]
    assert "input" not in svc._risk_facts(_w("meta_entity"), "run_in_fabric", "dev", False)
    assert "input" not in svc._risk_facts(_w(where="this_pc"), "run", "dev", False)
    assert asked == ["dev"]


def test_an_input_that_cant_be_read_is_cut_to_three_hundred_characters():
    def broken(name, env):
        raise RuntimeError("z" * 400)

    facts = _facts_svc(meta_preflight=broken)._risk_facts(_w("meta_entity", where="this_pc"), "run", "dev", False)
    assert facts["input"] == {"error": "z" * 300}


# ---- what the app calls

def test_the_check_refuses_actions_that_start_no_work():
    with pytest.raises(ValueError, match="cancel isn't assessed"):
        Svc().risk_check("pipeline:x", "cancel")


def test_the_check_reads_without_a_refresh_unless_asked(monkeypatch):
    svc = Svc()
    seen = []
    svc._risk_record = lambda id, action: {}
    svc._risk_facts = lambda w, action, env, refresh, memo=None: seen.append(refresh) or {}
    monkeypatch.setattr(rs.risk, "analyze", lambda facts, settings: {"result": "pass"})
    svc.risk_check("pipeline:x", "run")
    svc._risk_or_unknown(lambda: {}, "run", None, None)
    assert seen == [False, False]


def test_a_check_that_fails_is_unknown_and_lets_the_run_go():
    def broken():
        raise RuntimeError("w" * 400)

    out = Svc()._risk_or_unknown(broken, "run", None, None)
    assert out == {"result": rs.risk.UNKNOWN, "label": rs.risk.LABELS[rs.risk.UNKNOWN], "findings": [],
                   "can_proceed": True, "needs_acknowledgement": False, "enforced": False,
                   "summary": ("The risk check couldn't run: " + "w" * 400)[:300]}


# ---- acknowledgements

def test_an_acknowledgement_without_an_env_says_blank_and_cuts_a_long_note():
    svc = Svc()
    svc._wl_user = lambda: "me"
    current = {"workload_id": "w", "workload": "W", "action": "run", "env": None, "result": "warn",
               "findings": [{"title": "Slow", "outcome": rs.risk.WARNING}, {"title": "Fine", "outcome": "pass"}]}
    record = svc._risk_ack_record(current, "rid", "n" * 600)
    assert record["env"] == "" and record["note"] == "n" * 500 and record["warnings"] == ["Slow"]


def test_acknowledgements_go_into_a_new_folder_and_fifty_are_read_back(tmp_path):
    svc = Svc()
    svc.root = tmp_path / "a" / "b"
    for i in range(55):
        svc._risk_ack_write({"i": i})
    out = svc.risk_acknowledgements()
    assert len(out) == 50 and out[0] == {"i": 54} and out[-1] == {"i": 5}
    assert json.loads((svc.root / rs._ACKS).read_text(encoding="utf-8").splitlines()[0]) == {"i": 0}


def test_placement_advice_reads_twenty_receipts_and_ten_side_by_side_runs(monkeypatch):
    svc = Svc()
    limits = {}
    svc.notebooks = types.SimpleNamespace(load=lambda name: {"cells": [], "lakehouse": None})
    svc.receipts_list = lambda name, limit: limits.setdefault("receipts", limit) and []
    svc.side_by_side_list = lambda name, limit: limits.setdefault("side", limit) and []
    svc._checks = lambda name: {}
    monkeypatch.setattr(rs, "compute_check", lambda cells, lh, **k: {})
    monkeypatch.setattr(rs.advisor, "advise", lambda *a: {})
    svc.placement_advice("nb")
    assert limits == {"receipts": 20, "side": 10}
