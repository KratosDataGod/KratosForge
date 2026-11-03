"""A notebook's Runs panel: this PC's scheduled runs and the Fabric runs this app started, in one list."""

import pytest

from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.service import Service

from test_schedules import FakeScheduler


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(Service, "pc_schedules", property(lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe")))
    s = Service(root=tmp_path / "app")
    s.notebooks.save("Daily sales", [{"type": "code", "source": "x = 1"}], None)
    s.notebooks.save("Other", [{"type": "code", "source": "y = 1"}], None)
    return s


def _schedule(svc, name):
    return svc.add_pc_schedule({"kind": "notebook", "name": name}, {"kind": "daily", "times": ["06:00"]})["schedule"]["id"]


def _fabric(svc, **record):
    return svc._log_fabric_run({"kind": "Notebook run", "name": "Daily sales", "workspace_id": "ws1",
                                "workspace_name": "Sales", "item_id": "it1", "job_id": "j1",
                                "url": "https://app.fabric.microsoft.com/groups/ws1/synapsenotebooks/it1"} | record)


def test_both_sources_for_this_notebook_newest_first(svc):
    mine, other = _schedule(svc, "Daily sales"), _schedule(svc, "Other")
    meta = svc.pc_schedules.add({"kind": "meta", "env": "dev"}, {"kind": "daily", "times": ["06:00"]})["id"]
    log = svc.pc_schedules.log_run
    log({"schedule_id": mine, "label": "Daily sales", "trigger": "schedule", "started": "2026-10-20T06:00:00+00:00",
         "ended": "2026-10-20T06:00:04+00:00", "seconds": 4.2, "status": "ok", "message": "1 cell ran", "cells": 1})
    log({"schedule_id": mine, "label": "Daily sales", "trigger": "manual", "started": "2026-10-20T08:00:00+00:00",
         "seconds": 1.0, "status": "failed", "message": "Cell 1 of 1 failed: NameError: x", "alert_error": "no Teams"})
    log({"schedule_id": other, "label": "Other", "trigger": "schedule", "started": "2026-10-20T09:00:00+00:00", "status": "ok"})
    log({"schedule_id": meta, "label": "Daily sales", "trigger": "schedule", "started": "2026-10-20T09:30:00+00:00", "status": "ok"})
    log({"schedule_id": "gone", "label": "Daily sales", "trigger": "schedule", "started": "2026-10-19T06:00:00+00:00", "status": "ok"})
    _fabric(svc, started_at="2026-10-20T07:00:00+00:00", finished_at="2026-10-20T07:02:30Z", status="Completed")
    _fabric(svc, name="Other", started_at="2026-10-20T07:30:00+00:00")
    runs = svc.notebook_runs("Daily sales")
    assert [(r["source"], r["trigger"], r["started"][11:16]) for r in runs] == [
        ("pc", "manual", "08:00"), ("fabric", "fabric", "07:00"), ("pc", "schedule", "06:00"), ("pc", "schedule", "06:00")]
    assert runs[0]["message"].startswith("Cell 1 of 1 failed") and runs[0]["alert_error"] == "no Teams"
    assert runs[1]["seconds"] == 150.0 and runs[1]["workspace_name"] == "Sales" and runs[1]["url"].endswith("/it1")
    assert runs[3]["schedule_id"] == "gone", "a removed schedule's runs still show"
    assert len(svc.notebook_runs("Daily sales", limit=2)) == 2


def test_run_now_on_a_fabric_schedule_counts_only_for_this_notebooks_item(svc):
    _fabric(svc, started_at="2026-10-20T07:00:00+00:00")
    svc._log_fabric_run({"kind": "Scheduled item run", "name": "Daily sales", "workspace_id": "ws1", "item_id": "it1",
                         "job_id": "j2", "started_at": "2026-10-20T08:00:00+00:00"})
    svc._log_fabric_run({"kind": "Scheduled item run", "name": "Daily sales", "workspace_id": "ws1", "item_id": "pipe",
                         "job_id": "j3", "started_at": "2026-10-20T09:00:00+00:00"})  # a pipeline with the same name
    svc._log_fabric_run({"kind": "Table maintenance", "name": "Daily sales", "started_at": "2026-10-20T10:00:00+00:00"})
    runs = svc.notebook_runs("Daily sales")
    assert [r["job_id"] for r in runs] == ["j2", "j1"]
    assert runs[0]["trigger"] == "run_now"
    assert runs[0]["url"] == "https://app.fabric.microsoft.com/groups/ws1/synapsenotebooks/it1?experience=fabric-developer"


def test_opening_the_list_asks_fabric_nothing_and_refresh_reads_job_status(svc, monkeypatch):
    _fabric(svc, started_at="2026-10-20T07:00:00+00:00")
    asked = []

    class Fabric:
        def job_instance(self, ws, item, job):
            asked.append((ws, item, job))
            return {"status": "Failed", "endTimeUtc": "2026-10-20T07:01:00Z", "failureReason": {"message": "Out of memory"}}

    monkeypatch.setattr(Service, "_quiet_fabric", lambda self: Fabric())
    assert svc.notebook_runs("Daily sales")[0]["status"] == "NotStarted"
    assert asked == []
    run = svc.notebook_runs("Daily sales", refresh=True)[0]
    assert asked == [("ws1", "it1", "j1")]
    assert (run["status"], run["message"], run["seconds"]) == ("Failed", "Out of memory", 60.0)


def test_no_runs_is_an_empty_list(svc):
    assert svc.notebook_runs("Daily sales") == []


def test_fabric_runs_follow_the_notebooks_item_when_it_is_known(svc):
    svc.workload_store.record_link("Daily sales", "ws1", "it1", "Sales")
    _fabric(svc, name="Sales v1", job_id="old", started_at="2026-10-19T07:00:00+00:00")  # before a rename
    _fabric(svc, workspace_id="ws2", item_id="it9", job_id="other", started_at="2026-10-20T07:00:00+00:00")  # same name, elsewhere
    svc._log_fabric_run({"kind": "Scheduled item run", "name": "Daily sales", "workspace_id": "ws1", "item_id": "it1",
                         "job_id": "now", "started_at": "2026-10-20T08:00:00+00:00"})
    assert [r["job_id"] for r in svc.notebook_runs("Daily sales")] == ["now", "old"]


def test_without_a_known_item_runs_match_by_name_in_the_publish_workspace(svc):
    svc.notebooks.save("Daily sales", [{"type": "code", "source": "x = 1"}], None,
                       workspace={"workspace_id": "ws1", "workspace_name": "Sales"})
    _fabric(svc, job_id="here", started_at="2026-10-20T07:00:00+00:00")
    _fabric(svc, workspace_id="ws2", item_id="it9", job_id="there", started_at="2026-10-20T08:00:00+00:00")
    assert [r["job_id"] for r in svc.notebook_runs("Daily sales")] == ["here"]


def test_refresh_status_asks_only_about_this_notebooks_open_runs(svc, monkeypatch):
    _fabric(svc, started_at="2026-10-20T07:00:00+00:00")
    _fabric(svc, name="Other", item_id="it2", job_id="j2", started_at="2026-10-20T07:30:00+00:00")
    _fabric(svc, job_id="j3", status="Completed", started_at="2026-10-20T06:00:00+00:00")
    asked = []

    class Fabric:
        def job_instance(self, ws, item, job):
            asked.append(job)
            return {"status": "Completed"}

    monkeypatch.setattr(Service, "_quiet_fabric", lambda self: Fabric())
    svc.notebook_runs("Daily sales", refresh=True)
    assert asked == ["j1"]
    svc.notebook_runs("Daily sales", refresh=True)
    assert asked == ["j1"], "nothing still going: Fabric isn't asked"
    assert svc.fabric_runs(10, refresh=False)[1]["status"] == "NotStarted", "the other notebook's run is untouched"


def test_a_failure_reason_from_fabric_is_kept_as_one_line_without_a_token(svc, monkeypatch):
    _fabric(svc, started_at="2026-10-20T07:00:00+00:00")
    token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXNpZ25hdHVyZQ"

    class Fabric:
        def job_instance(self, ws, item, job):
            return {"status": "Failed", "failureReason": {"message": f"Out of memory\nBearer {token}\n" + "z" * 3000}}

    monkeypatch.setattr(Service, "_quiet_fabric", lambda self: Fabric())
    message = svc.notebook_runs("Daily sales", refresh=True)[0]["message"]
    assert message.startswith("Out of memory Bearer [removed]") and "\n" not in message and len(message) <= 1000
