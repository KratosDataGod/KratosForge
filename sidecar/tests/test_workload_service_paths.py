"""Workloads (workload_service.py) when something goes wrong or is unusual: sources that can't be read,
a cancel, actions settled from run records, and audit lines the platform doesn't take. The happy
paths are in test_workloads.py; this file uses its fixtures and fakes."""

import types

import pytest

from fabric_desktop import workload_service
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.workloads import model

from test_workloads import (  # noqa: F401 - svc, project and no_capacity_read are fixtures
    FakeFabric, everything, no_capacity_read, project, scan, signed_in, svc,
)


class Running(FakeFabric):
    def list_job_instances(self, ws, item):
        return [{"id": "j1", "status": "InProgress", "startTimeUtc": "2026-11-06T09:00:00Z"}]


def _running_pipeline(svc, monkeypatch):
    signed_in(svc)
    scan(svc, monkeypatch)
    monkeypatch.setattr(workload_service, "FabricClient", Running)
    svc._log_fabric_run({"kind": "Scheduled item run", "name": "pl_load_erp", "workspace_id": "ws1",
                         "workspace_name": "Sales Dev", "item_id": "pl1", "job_id": "j1", "status": "InProgress"})
    return model.fabric_id("ws1", "pl1")


# ---- who is signed in, without prompting

def test_an_auth_that_cant_answer_reads_as_signed_out(svc):  # noqa: F811
    def boom(*_):
        raise RuntimeError("no client id yet")
    svc._auth = types.SimpleNamespace(signed_in_user=boom, silent_token=boom)
    assert svc._wl_user() == "" and svc._wl_token(["x"]) is None


# ---- sources that can't be read hide nothing else

def test_a_broken_project_file_is_an_error_source_not_a_failure(svc, project, monkeypatch):  # noqa: F811
    def broken():
        raise ValueError("project.toml: bad TOML at line 3")
    monkeypatch.setattr(svc, "_project", broken)
    records, obs = svc._wl_meta(False, {}, [], "")
    assert records == [] and obs["state"] == "error" and "bad TOML" in obs["detail"]


def test_a_damaged_notebook_is_listed_without_its_settings(svc, monkeypatch):  # noqa: F811
    svc.notebooks.save("Good", [{"type": "code", "source": "x = 1"}])
    svc.notebooks.save("Torn", [{"type": "code", "source": "y = 1"}])
    real = svc.notebooks.load

    def load(name):
        if name == "Torn":
            raise ValueError("not JSON")
        return real(name)
    monkeypatch.setattr(svc.notebooks, "load", load)
    by = {n["name"]: n for n in svc._wl_notebooks()}
    assert set(by["Torn"]) == {"name", "modified"} and "cells" in by["Good"]


def test_workspaces_that_cant_be_listed_end_the_scan_with_the_reason(svc, monkeypatch):  # noqa: F811
    def refused(*_):
        raise FabricError("GET /workspaces failed with 401: Unauthorized")
    monkeypatch.setattr(svc, "_schedule_workspaces", refused)
    out = svc._wl_scan_items("tok", client=FakeFabric())
    assert out["pending"] is False and out["state"] == "unauthorized" and out["error"]


def test_a_pipelines_history_says_why_it_couldnt_be_read(svc, monkeypatch):  # noqa: F811
    signed_in(svc)
    scan(svc, monkeypatch)
    w = svc._wl_record(model.fabric_id("ws1", "pl1"))
    signed_in(svc, token=None)
    runs, obs = svc._wl_fabric_history(w)
    assert obs["state"] == "signed_out" and runs == list(w["runs"])

    class Down(FakeFabric):
        def list_job_instances(self, ws, item):
            raise FabricError("GET /jobs failed with 503: Service Unavailable")
    signed_in(svc)
    monkeypatch.setattr(workload_service, "FabricClient", Down)
    runs, obs = svc._wl_fabric_history(w)
    assert obs["state"] != "live" and obs["detail"] and runs == list(w["runs"])


def test_a_notebook_link_that_cant_be_recorded_never_fails_the_publish(svc, monkeypatch, caplog):  # noqa: F811
    def locked(*a, **k):
        raise OSError("database is locked")
    monkeypatch.setattr(svc.workload_store, "record_link", locked)
    with caplog.at_level("WARNING"):
        svc._record_notebook_item("Daily load", "ws1", "nb1")
    assert "Couldn't record notebook 'Daily load'" in caplog.text


def test_the_timeline_has_actions_and_a_definition_file_that_is_gone(svc, tmp_path):  # noqa: F811
    w = {"runs": [], "deployments": [], "definition": {"path": str(tmp_path / "gone.ipynb"), "modified": 1.0},
         "assignment_history": []}
    actions = [{"at": "2026-11-06T09:00:00+00:00", "action": "run_item", "status": "succeeded", "by": ""}]
    events = svc._wl_timeline(w, actions)
    assert [e["kind"] for e in events] == ["action"]  # no "Definition changed" from a file that isn't there
    assert events[0]["text"].endswith("by you · succeeded")
    (tmp_path / "here.ipynb").write_text("{}", encoding="utf-8")
    w["definition"]["path"] = str(tmp_path / "here.ipynb")
    assert {e["kind"] for e in svc._wl_timeline(w, [])} == {"definition"}


def test_consumers_come_from_the_platform_catalog_when_there_is_one(svc, monkeypatch):  # noqa: F811
    w = {"definition": {"project": "sales"}, "dependencies": {"products": [{"product": "orders"}]}}
    assert svc._wl_consumers({"dependencies": {}}) == []

    def no_platform():
        raise LookupError("no platform yet")
    monkeypatch.setattr(svc, "platform_products", no_platform, raising=False)
    assert svc._wl_consumers(w) == []
    monkeypatch.setattr(svc, "platform_products", lambda: {"products": [
        {"id": "sales.orders", "name": "orders", "consumers": ["finance", {"project": "ops"}, {"x": 1}]},
        {"id": "other.thing", "name": "thing", "consumers": ["nobody"]}]}, raising=False)
    assert [c["consumer"] for c in svc._wl_consumers(w)] == ["finance", "ops", "{'x': 1}"]


# ---- cancel

def test_a_running_fabric_job_can_be_cancelled_and_settles_when_it_stops(svc, monkeypatch, no_capacity_read):  # noqa: F811
    wid = _running_pipeline(svc, monkeypatch)
    w = svc._wl_record(wid)
    [cancel] = [a for a in svc._wl_available(w) if a["action"] == "cancel"]
    assert cancel["job_id"] == "j1"
    pre = svc.workload_preflight(wid, "cancel", assess=False)
    assert pre["allowed"] and pre["target"] == "the running job of pl_load_erp" and "Work it already committed" in pre["consequence"]
    asked = []
    monkeypatch.setattr(svc, "cancel_fabric_run", lambda ws, item, job: asked.append((ws, item, job)) or {"cancelled": job})
    out = svc.workload_act(wid, "cancel", job_id="j1")
    assert asked == [("ws1", "pl1", "j1")] and out["action"]["status"] == "submitted"
    runs = svc._read_fabric_runs()
    runs[-1]["status"] = "Cancelled"
    svc._write_fabric_runs(runs)
    svc._wl_reconcile(svc._read_fabric_runs(), [])
    done = svc.workload_actions(wid)[0]
    assert done["status"] == "succeeded" and done["detail"] == "The job stopped."


def test_a_cancel_that_was_too_late_is_recorded_as_failed(svc, monkeypatch, no_capacity_read):  # noqa: F811
    wid = _running_pipeline(svc, monkeypatch)
    monkeypatch.setattr(svc, "cancel_fabric_run", lambda ws, item, job: {})
    svc.workload_act(wid, "cancel", job_id="j1")
    runs = svc._read_fabric_runs()
    runs[-1]["status"] = "Completed"
    svc._write_fabric_runs(runs)
    svc._wl_reconcile(svc._read_fabric_runs(), [])
    done = svc.workload_actions(wid)[0]
    assert done["status"] == "failed" and "before the cancel took effect" in done["detail"]


@pytest.mark.parametrize("job_id", ["", "j-old", "someone-elses"])
def test_a_cancel_names_a_running_job_of_this_workload(svc, monkeypatch, no_capacity_read, job_id):  # noqa: F811
    # The cancel went to Fabric with whatever job id it was given, an empty one included.
    wid = _running_pipeline(svc, monkeypatch)
    monkeypatch.setattr(svc, "cancel_fabric_run", lambda *a: pytest.fail("asked Fabric to cancel"))
    with pytest.raises(PermissionError, match="isn't a running job of pl_load_erp"):
        svc.workload_act(wid, "cancel", job_id=job_id)
    assert svc.workload_actions(wid)[0]["status"] == "failed"


# ---- a schedule run now, settled from this PC's run records

def test_a_schedule_run_settles_from_the_manual_run_that_followed_it(svc):  # noqa: F811
    store = svc.workload_store
    rec = store.start_action("nb:x", "run_schedule", "", {"target": "t", "where": "This PC"}, "you", "")
    store.update_action(rec["id"], ref={"schedule_id": "s1"})
    at = rec["at"][:19]
    pc_runs = [{"schedule_id": "s1", "trigger": "manual", "started": "2000-01-01T00:00:00+00:00", "status": "ok"},
               {"schedule_id": "s1", "trigger": "schedule", "started": at + "+00:00", "status": "ok"},
               {"schedule_id": "s1", "trigger": "manual", "started": at + "+00:00", "status": "error",
                "message": "ModuleNotFoundError"}]
    svc._wl_reconcile([], pc_runs[:2])
    assert store.actions("nb:x")[0]["status"] == "submitted"  # an older run, or one Task Scheduler started
    svc._wl_reconcile([], pc_runs)
    done = store.actions("nb:x")[0]
    assert done["status"] == "failed" and done["detail"] == "ModuleNotFoundError"


def test_a_schedule_run_needs_its_schedule(svc, project, monkeypatch):  # noqa: F811
    w = {"name": "n", "kind": "notebook", "definition": {}, "deployments": [], "runs": []}
    monkeypatch.setattr(svc, "workload_preflight", lambda *a, **k: {"allowed": True, "target": "t", "where": "This PC",
                                                                   "identity": "you"})
    monkeypatch.setattr(svc, "_wl_record", lambda id: w)
    with pytest.raises(ValueError, match="Say which schedule to run"):
        svc.workload_act("nb:n", "run_schedule")
    with pytest.raises(PermissionError, match="That schedule doesn't run n"):
        svc.workload_act("nb:n", "run_schedule", schedule_id="other")
    with pytest.raises(ValueError, match="unknown action"):
        svc.workload_act("nb:n", "explode")
    assert [a["status"] for a in svc.workload_actions("nb:n")] == ["failed"] * 3


# ---- the platform's audit lines

def test_an_audit_line_that_cant_be_kept_never_fails_the_action(svc, monkeypatch, caplog):  # noqa: F811
    def full(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(svc.workload_store, "audit_queue", full)
    with caplog.at_level("WARNING"):
        svc._wl_audit("nb:x", "run", "submitted", {"name": "x"})
    assert "Couldn't keep a workload audit line: OSError: disk full" in caplog.text


def test_audit_lines_wait_while_signed_out_and_while_the_platform_cant_be_reached(svc, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setattr(svc, "_platform_config", lambda: {workload_service.CATALOG_ENV: {
        "functions_id": "fn", "workspace_id": "ws"}}, raising=False)
    signed_in(svc, token=None)
    platform = svc._wl_audit_platform()
    with pytest.raises(PermissionError, match="Signed out"):
        platform._token()  # asked only when a line is sent: never a sign-in prompt
    signed_in(svc, token="tok")
    assert platform._token() == "tok" and (platform.workspace_id, platform.functions_id) == ("ws", "fn")
    svc.workload_store.audit_queue("k1", "nb:x", "run", "submitted", {"name": "x"})

    def unreachable():
        raise ConnectionError("no network")
    monkeypatch.setattr(svc, "_wl_audit_platform", unreachable)
    with caplog.at_level("WARNING"):
        assert svc._wl_audit_send() == 0
    assert "Couldn't reach the platform for workload audit lines" in caplog.text
    assert len(svc.workload_store.audit_waiting(10)) == 1  # still waiting, in order
    svc._wl_audit_lock.acquire()
    try:
        assert svc._wl_audit_send() == 0  # one send at a time
    finally:
        svc._wl_audit_lock.release()


def test_why_the_platform_didnt_take_a_line_is_said_in_words():
    assert "Finish or repair once" in workload_service._audit_problem(LookupError("log_workload_action"))
    assert workload_service._audit_problem(ValueError("x")) == "ValueError: x"


def test_rows_that_cant_be_converted_are_dropped_with_a_warning(caplog):
    class Broken:
        def to_dicts(self):
            raise TypeError("not a frame")
    with caplog.at_level("WARNING"):
        assert workload_service._rows(Broken()) == []
    assert "Couldn't convert rows" in caplog.text
