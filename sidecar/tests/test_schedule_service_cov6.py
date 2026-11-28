"""Schedules on this PC and in Fabric (schedule_service.py), the paths the other tests leave: a metadata
schedule's tables checked against the project, a notebook's runs found by its Fabric item and a refresh with
nothing unfinished, a scheduled pipeline run, a Prod run whose outcome the audit log can't take, a scheduled
notebook with a Fabric step and progress, every workspace's schedules, and changing one schedule of several.
Nothing reaches Fabric, Teams or Task Scheduler; the kernel is a stand-in."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from fabric_desktop import schedule_service
from fabric_desktop.service import Service
from test_schedules import svc  # noqa: F401 - svc is a fixture

DAILY = {"kind": "daily", "times": ["06:00"]}
REFUSED_KIND = "^A schedule runs a notebook, a metadata environment or a pipeline$"


def test_a_metadata_schedule_names_only_the_projects_tables(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "_project", lambda: SimpleNamespace(entities={"Orders": {}, "Lines": {}}), raising=False)
    with pytest.raises(ValueError, match="^Not in the project: Notes, Items$"):
        svc.add_pc_schedule({"kind": "meta", "env": "dev", "entities": ["Orders", "Notes", "Items"]}, DAILY)
    assert svc.pc_schedules.listing() == []
    out = svc.add_pc_schedule({"kind": "meta", "env": "dev", "entities": ["Orders"]}, DAILY)
    assert out["schedule"]["target"]["entities"] == ["Orders"]
    assert [s["id"] for s in svc.pc_schedules.listing()] == [out["schedule"]["id"]]


def _fabric_run(svc, **row):
    svc._log_fabric_run({"kind": "Notebook run", "workspace_id": "ws1", "status": "Completed",
                         "started_at": "2026-10-20T07:00:00+00:00", "finished_at": "2026-10-20T07:00:02Z", **row})


def test_a_published_notebooks_fabric_runs_are_found_by_its_item(svc, monkeypatch):  # noqa: F811
    svc.notebooks.save("Daily sales", [{"type": "code", "source": "x = 1"}])
    _fabric_run(svc, name="Daily sales", item_id="it-now", job_id="j1")
    _fabric_run(svc, name="Old name", item_id="it-now", job_id="j2", kind="Scheduled item run")
    _fabric_run(svc, name="Daily sales", item_id="it-other", job_id="j3")  # same name, another item
    _fabric_run(svc, name="Daily sales", item_id="it-now", job_id="j4", kind="Pipeline run")
    monkeypatch.setattr(Service, "workload_store", property(lambda self: SimpleNamespace(
        links=lambda: {"Daily sales": {"item_id": "it-now", "workspace_id": "ws1"}})))
    calls = []
    real = svc.fabric_runs

    def recorded(limit=50, refresh=True, job_ids=None):
        calls.append((limit, refresh, job_ids))
        return real(limit, refresh=False)
    svc.fabric_runs = recorded
    runs = svc.notebook_runs("Daily sales", refresh=True)
    assert sorted(r["job_id"] for r in runs) == ["j1", "j2"]
    assert all(r["source"] == "fabric" for r in runs)
    assert calls == [(500, False, None)], "nothing unfinished: Fabric isn't asked again"


def _recorder(fail_on):
    lines = []

    def record(action, outcome, seconds, params, **kw):
        if outcome in fail_on:
            raise OSError("disk full")
        lines.append((action, outcome, kw))
    return SimpleNamespace(record=record, lines=lines)


def test_a_scheduled_pipeline_run_is_recorded(svc, monkeypatch):  # noqa: F811
    sid = svc.pc_schedules.add({"kind": "pipeline", "path": "C:/repo/Load.DataPipeline"}, DAILY)["id"]
    seen = []

    def run(target):
        seen.append(target)
        return {"status": "ok", "message": "Load succeeded: 2 activities", "run_id": "r1"}
    monkeypatch.setattr(svc, "_run_pipeline_here", run, raising=False)
    row = svc.run_schedule(sid)
    assert (row["status"], row["message"], row["run_id"]) == ("ok", "Load succeeded: 2 activities", "r1")
    assert [(t["kind"], t["path"]) for t in seen] == [("pipeline", "C:/repo/Load.DataPipeline")]
    assert svc.pc_schedules.runs()[0]["run_id"] == "r1"


def test_a_prod_runs_outcome_the_audit_log_cant_take_only_warns(svc, monkeypatch, caplog):  # noqa: F811
    sid = svc.pc_schedules.add({"kind": "meta", "env": "prod"}, DAILY)["id"]
    monkeypatch.setattr(svc, "_run_meta_here", lambda env, names: {"status": "ok", "message": "3 tables"}, raising=False)
    rec = _recorder(fail_on={"ok", "error"})
    svc._audit_log = rec
    with caplog.at_level(logging.WARNING, logger=schedule_service._log.name):
        row = svc.run_schedule(sid)
    assert row["status"] == "ok" and row["message"] == "3 tables"
    assert [line[1] for line in rec.lines] == ["started"]
    assert "A Prod schedule's outcome couldn't be written to the audit log: OSError" in caplog.text
    assert svc.pc_schedules.runs()[0]["status"] == "ok"


class _Kernel:
    def __init__(self, fail_on=None):
        self.ran, self.fail_on = [], fail_on
        self.resources_dir = self.onelake_writes = None

    def run(self, source):
        self.ran.append(source)
        if source == self.fail_on:
            return {"status": "error", "outputs": [{"type": "stream", "text": "x"},
                                                   {"type": "error", "ename": "NameError", "evalue": "y"}]}
        return {"status": "ok", "outputs": []}


def _notebook_here(svc, monkeypatch, cells, kernel):
    svc.notebooks.save("Night", cells)
    monkeypatch.setattr(svc, "_attach", lambda ref, notebook=None: None)
    svc.kernel = kernel


def test_a_scheduled_notebook_skips_its_fabric_step_and_reports_progress(svc, monkeypatch):  # noqa: F811
    kernel = _Kernel()
    _notebook_here(svc, monkeypatch, [
        {"type": "code", "source": "a = 1"},
        {"type": "code", "source": "frozen = 1", "meta": {"frozen": True}},
        {"type": "markdown", "source": "# Notes"},
        {"type": "code", "source": "b = 2"},
        {"type": "code", "source": "spark_only()", "meta": {"fabric_step": True}}], kernel)
    progress = []
    out = svc._run_notebook_here("Night", progress=lambda i, n: progress.append((i, n)))
    assert out == {"status": "ok", "message": "2 cells ran, 1 frozen cell skipped, the Fabric step skipped", "cells": 2}
    assert kernel.ran == ["a = 1", "b = 2"]
    assert progress == [(1, 2), (2, 2)]


def test_a_scheduled_notebook_that_fails_names_the_cell_and_error(svc, monkeypatch):  # noqa: F811
    kernel = _Kernel(fail_on="b = boom")
    _notebook_here(svc, monkeypatch, [{"type": "code", "source": "a = 1"}, {"type": "code", "source": "b = boom"},
                                      {"type": "code", "source": "c = 3"}], kernel)
    out = svc._run_notebook_here("Night")
    assert out == {"status": "failed", "message": "Cell 2 of 3 failed: NameError: y", "cells": 2}
    assert kernel.ran == ["a = 1", "b = boom"]


class _Client:
    def __init__(self, token_fn):
        self.token = token_fn()

    def list_workspaces(self):
        return [{"id": "ws1", "displayName": "Sales"}, {"id": "ws2", "displayName": "Ops"}]


def test_every_workspaces_schedules_lists_every_workspace(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(schedule_service, "FabricClient", _Client)
    assert svc._schedule_workspaces(True, "tok") == [{"id": "ws1", "name": "Sales"}, {"id": "ws2", "name": "Ops"}]


def test_changing_one_fabric_schedule_changes_only_its_row(svc, monkeypatch):  # noqa: F811
    calls = []

    class Jobs:
        def update_schedule(self, *args):
            calls.append(args)
            return {"id": args[3]}
    monkeypatch.setattr(Service, "fabric_jobs", property(lambda self: Jobs()))
    old = {"type": "Daily", "times": ["01:00"]}
    svc._fabric_schedules = {"items": [{"id": "s0", "configuration": dict(old), "text": "before"},
                                       {"id": "s1", "configuration": dict(old), "text": "before"}]}
    assert svc.update_fabric_schedule("ws", "it", "RunNotebook", "s1", {"kind": "daily", "times": ["06:00"]}, True) == {"id": "s1"}
    s0, s1 = svc._fabric_schedules["items"]
    assert (s0["configuration"], s0["text"]) == (old, "before")
    assert s1["configuration"]["times"] == ["06:00"] and s1["text"] != "before"
    assert calls[0][:5] == ("ws", "it", "RunNotebook", "s1", True)


def test_a_pipeline_schedule_keeps_the_checked_target_and_another_kind_is_refused(svc, monkeypatch):  # noqa: F811
    checked = {"kind": "pipeline", "path": "C:/repo/Load.DataPipeline", "parameters": {"day": "1"}}
    monkeypatch.setattr(svc, "_pipeline_schedule_target", lambda target: checked, raising=False)
    out = svc.add_pc_schedule({"kind": "pipeline", "path": "C:/repo/Load.DataPipeline"}, DAILY)
    assert out["schedule"]["target"]["parameters"] == {"day": "1"}
    with pytest.raises(ValueError, match=REFUSED_KIND):
        svc.add_pc_schedule({"kind": "report", "name": "Sales"}, DAILY)
    assert len(svc.pc_schedules.listing()) == 1


def test_the_metadata_projects_workspaces_are_listed_with_the_pinned_ones(svc, monkeypatch):  # noqa: F811
    class Client(_Client):
        def list_workspaces(self):
            return super().list_workspaces() + [{"id": "ws3", "displayName": "Finance"}]
    monkeypatch.setattr(schedule_service, "FabricClient", Client)
    monkeypatch.setattr(svc, "workspace_marks", lambda: {"pins": ["ws3"]}, raising=False)
    monkeypatch.setattr(svc, "_project", lambda: SimpleNamespace(environments={
        "dev": {"workspace_id": "ws1"}, "local": {}, "gone": {"workspace_id": "ws-deleted"}}), raising=False)
    got = sorted(svc._schedule_workspaces(False, "tok"), key=lambda w: w["id"])
    assert got == [{"id": "ws1", "name": "Sales"}, {"id": "ws3", "name": "Finance"}]
