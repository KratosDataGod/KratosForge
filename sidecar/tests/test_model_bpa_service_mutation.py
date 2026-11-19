"""Best practices check service (model_bpa_service.py): what the mutation run (batch 125,
docs/quality/mutation-testing.md) showed no test pinned. Which runs are this model's checks and how they
are asked for, the risk check's two ways, a missing LH_Meta, the missing model's message, findings kept
on this PC, the request id, which Fabric errors republish the notebook, and the notebook's id."""

from __future__ import annotations

import datetime as dt
import json
import re

import pytest
from test_model_bpa import svc  # noqa: F401 - the service fixture with Fabric stood in for

from fabric_desktop import model_bpa_service
from fabric_desktop.errors import NotSetUp
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.meta.store import OneLakeStore

T = {"env": "test", "layer": "gold", "name": "SM_Gold", "model_id": "m1", "workspace_id": "ws-test",
     "workspace_name": "Sales Test", "meta_lakehouse_id": "id-meta"}


def test_now_is_utc_to_the_second():
    stamp = dt.datetime.fromisoformat(model_bpa_service._now())
    assert stamp.utcoffset() == dt.timedelta(0) and stamp.microsecond == 0


def test_only_this_models_checks_are_its_runs_asked_for_without_a_refresh(svc, monkeypatch):  # noqa: F811
    asked = []
    runs = [{"kind": "Best practices check", "model_id": "m1", "job_id": "j1", "request_id": "r1", "status": "Completed"},
            {"kind": "Best practices check", "model_id": "m2", "job_id": "j2", "request_id": "r2"},
            {"kind": "Notebook", "model_id": "m1", "job_id": "j3", "request_id": "r3"}]
    monkeypatch.setattr(svc, "fabric_runs", lambda n, **kw: asked.append((n, kw)) or runs)
    assert [r["job_id"] for r in svc._bpa_runs("m1")] == ["j1"]
    assert asked == [(200, {"refresh": False})]
    w = svc._bpa_workload(T)
    assert w["id"] == "model_bpa:ws-test:m1" and w["name"] == "Best practices check of SM_Gold"
    assert [r["job_id"] for r in w["runs"]] == ["j1"]


def test_a_running_check_is_asked_of_fabric_by_its_job(svc, monkeypatch):  # noqa: F811
    asked = []
    run = {"kind": "Best practices check", "model_id": "m1", "job_id": "j1", "request_id": "r1", "status": "InProgress"}

    def runs(n, **kw):
        asked.append((n, kw))
        return [run] if "refresh" in kw else [dict(run, status="Completed")]
    monkeypatch.setattr(svc, "fabric_runs", runs)
    assert svc._bpa_newest("m1")["status"] == "Completed"
    assert asked == [(200, {"refresh": False}), (200, {"job_ids": ["j1"]})]


def test_the_risk_check_is_refreshed_only_when_asked(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "_bpa_risk", type(svc)._bpa_risk.__get__(svc))
    monkeypatch.setattr(svc, "fabric_runs", lambda n, **kw: [])
    facts = []
    monkeypatch.setattr(svc, "_risk_facts", lambda w, action, env, refresh: facts.append((w["id"], action, env, refresh)) or "facts")
    monkeypatch.setattr(svc, "_risk_settings", lambda: "settings")
    monkeypatch.setattr(model_bpa_service.risk, "analyze", lambda f, s: {"analyzed": (f, s)})
    monkeypatch.setattr(svc, "_risk_or_unknown", lambda record, action, env, memo: {"cached": (record()["id"], action, env, memo)})
    assert svc._bpa_risk(T, refresh=True) == {"analyzed": ("facts", "settings")}
    assert facts == [("model_bpa:ws-test:m1", "run_in_fabric", "test", True)]
    assert svc._bpa_risk(T) == {"cached": ("model_bpa:ws-test:m1", "run_in_fabric", "test", None)}


def test_an_environment_without_lh_meta_isnt_set_up(svc, monkeypatch, tmp_path):  # noqa: F811
    store = OneLakeStore("test", "ws-test", {"LH_Gold": "id-gold"}, lambda: "tok", tmp_path / "cache2")
    monkeypatch.setattr(svc, "_store", lambda p, env, create=False: store)
    with pytest.raises(NotSetUp, match="LH_Meta doesn't exist in the test workspace"):
        svc.model_bpa_status("test")


@pytest.mark.parametrize("cfg, where", [({"workspace_name": "Sales Test"}, "Sales Test"), ({}, "the workspace")])
def test_a_missing_model_names_the_workspace(svc, monkeypatch, cfg, where):  # noqa: F811
    monkeypatch.setattr(svc.fake, "list_semantic_models", lambda ws: [])
    monkeypatch.setattr(svc, "_env_cfg", lambda project, env: cfg)
    with pytest.raises(LookupError, match=f"SM_Gold isn't in {where} yet"):
        svc.model_bpa_status("test")


def _record(request_id, n):
    return {"request_id": request_id, "finished_at": "2026-10-22T17:05:00+00:00", "tables": True,
            "rows": [{"Category": "Performance", "Rule Name": f"Rule {i}", "Severity": "⚠️", "Object Type": "Model",
                      "Object Name": "Model", "Description": "d", "URL": ""} for i in range(n)]}


def test_findings_kept_on_this_pc_arent_read_from_onelake_again(svc):  # noqa: F811
    started = svc.model_bpa_check("test")
    svc.fake.status = "Completed"
    key = ("ws-test", "id-meta", f"Files/_fabric_desktop/results/{started['request_id']}.json")
    svc.files[key] = json.dumps(_record(started["request_id"], 1)).encode()
    assert svc.model_bpa_status("test")["last"]["counts"]["Warning"] == 1
    svc.files[key] = json.dumps(_record(started["request_id"], 3)).encode()
    assert svc.model_bpa_status("test")["last"]["counts"]["Warning"] == 1


def test_a_request_id_is_the_time_and_four_hex_digits(svc):  # noqa: F811
    started = svc.model_bpa_check("test")
    assert re.fullmatch(r"bpa-\d{8}-\d{6}-[0-9a-f]{4}", started["request_id"])


@pytest.mark.parametrize("status, code", [(404, "Gone"), (None, "EntityNotFound"), (400, "ItemNotFound")])
def test_a_404_or_a_not_found_code_publishes_the_notebook_again(svc, status, code):  # noqa: F811
    real = svc.fake.run_item_job
    published = []

    def run(ws, item, job_type, data=None):
        if item == "nb1":
            raise FabricError("not found", code=code, status=status)
        return real(ws, item, job_type, data)
    svc.fake.run_item_job = run
    svc.fake.save_notebook = lambda ws, name, ipynb: published.append(name) or {"id": "nb1" if not published[1:] else "nb2"}
    started = svc.model_bpa_check("test")
    assert started["job_id"] and len(published) == 2


def test_acknowledging_names_the_workload_or_else_the_model(svc, monkeypatch):  # noqa: F811
    written = []
    monkeypatch.setattr(svc, "_risk_ack_write", written.append)
    current = {"id": "w1", "result": "warning", "findings": []}
    svc._bpa_acknowledge(T, current)
    svc._bpa_acknowledge(T, dict(current, workload="Checking SM_Gold"))
    assert [r["workload"] for r in written] == ["SM_Gold", "Checking SM_Gold"]
    assert dt.datetime.fromisoformat(written[0]["at"]).utcoffset() == dt.timedelta(0)


def test_the_notebook_id_is_fabrics_answer_or_else_found_by_name(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "_notebook_item", lambda name, ws: {"id": "found"})
    assert svc._bpa_notebook(T, fresh=True) == "nb1"
    svc.fake.save_notebook = lambda ws, name, ipynb: {}
    assert svc._bpa_notebook(T, fresh=True) == "found"
    monkeypatch.setattr(svc, "_notebook_item", lambda name, ws: None)
    with pytest.raises(LookupError, match="didn't return its id"):
        svc._bpa_notebook(T, fresh=True)
