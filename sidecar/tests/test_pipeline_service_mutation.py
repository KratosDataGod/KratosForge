"""Pipelines on this PC (pipeline_service.py): what mutation batch 99 found the other tests let through.
A refusal's counts, the inactive flag, who gets notebook and lakehouse helpers and with what defaults,
starting, saving, finishing and cancelling a run, the run list's order, and how far down a repository
pipeline folders are found. Local only: no network, and nothing reaches Fabric."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import pipeline_service as ps
from fabric_desktop.orchestration.definition import DefinitionError

from test_pipeline_runs import Svc, act, finish, pipeline, saved, setvar, wait


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("local pipeline runs must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


SIMPLE = pipeline(setvar("Set", "v", "x"), variables={"v": {"type": "String"}})


# ---- reading a definition

def test_a_definition_that_cant_be_read_counts_one_error_only(tmp_path):
    out = Svc(tmp_path).pipeline_import(text="{not json")
    assert out["counts"] == {"error": 1, "warning": 0, "info": 0}


def test_only_an_inactive_activity_is_marked_and_import_lists_no_expressions(tmp_path):
    off = setvar("Off", "v", "y")
    off["state"] = "Inactive"
    out = Svc(tmp_path).pipeline_import(text=pipeline(setvar("On", "v", "x"), off, variables={"v": {"type": "String"}}))
    rows = {r["name"]: r for r in out["activities"]}
    assert "inactive" not in rows["On"] and rows["Off"]["inactive"] is True
    assert all("expressions" not in r and "type_properties" not in r for r in rows.values())


# ---- helpers a run gets

class Stop(DefinitionError):
    def __init__(self):
        super().__init__("stopped by the test", "bad_request")


def _record(monkeypatch, name):
    calls = []

    def make(*args, **kwargs):
        calls.append((args, kwargs))
        raise Stop()

    monkeypatch.setattr(ps, name, make)
    return calls


def _with(tmp_path, *attrs):
    svc = Svc(tmp_path)
    for a in attrs:
        setattr(svc, a, object())
    return svc


def test_notebook_steps_get_the_notebook_runner_only_from_the_engine(tmp_path, monkeypatch):
    calls = _record(monkeypatch, "LocalNotebooks")
    engine = _with(tmp_path, "_validate_kernel", "notebooks")
    assert engine.pipeline_run_check(text=SIMPLE)["ok"] is True  # no notebooks named
    assert calls == []
    for half in (_with(tmp_path, "_validate_kernel"), _with(tmp_path, "notebooks")):
        assert half.pipeline_run_check(text=SIMPLE, notebooks={"NB": "nb"})["ok"] is True
    assert calls == []
    out = engine.pipeline_run_check(text=SIMPLE, notebooks={"NB": "nb"})
    assert out["problems"][0]["message"] == "stopped by the test"
    (args, kwargs), = calls
    assert args[0] is engine and args[2:] == ({"NB": "nb"}, False, False) and kwargs == {}


def test_a_started_runs_notebooks_stay_contained_and_their_outputs_go(tmp_path, monkeypatch):
    calls = _record(monkeypatch, "LocalNotebooks")
    _with(tmp_path, "_validate_kernel", "notebooks").pipeline_run_start(text=SIMPLE, notebooks={})
    (args, _), = calls
    assert args[3:] == (False, False)


def test_lakehouse_reads_and_sql_projects_need_the_engine_and_a_request(tmp_path, monkeypatch):
    calls = _record(monkeypatch, "LocalData")
    engine = _with(tmp_path, "_proof_location")
    assert engine.pipeline_run_check(text=SIMPLE)["ok"] is True
    assert engine.pipeline_run_start(text=SIMPLE)["ok"] is True
    assert Svc(tmp_path).pipeline_run_check(text=SIMPLE, read_lakehouses=True)["ok"] is True
    assert calls == []
    assert engine.pipeline_run_check(text=SIMPLE, sql_projects={"S": "p"})["ok"] is False
    assert engine.pipeline_run_check(text=SIMPLE, read_lakehouses=True)["ok"] is False
    assert [c[1]["lakehouse_reads"] for c in calls] == [False, True]
    assert [c[1]["sql_projects"] for c in calls] == [{"S": "p"}, None]


def test_a_run_runs_four_activities_at_once_unless_told(tmp_path, monkeypatch):
    seen = []
    real = ps.PipelineRun

    def make(*args, **kwargs):
        seen.append(kwargs.get("max_parallel"))
        return real(*args, **kwargs)

    monkeypatch.setattr(ps, "PipelineRun", make)
    svc = Svc(tmp_path)
    finish(svc, svc.pipeline_run_start(text=SIMPLE)["run_id"])
    assert seen == [4]


# ---- starting, saving and finishing a run

def test_a_run_that_cant_start_says_why_and_saves_nothing(tmp_path):
    svc = Svc(tmp_path)
    out = svc.pipeline_run_start(text=pipeline(act("Copy", "Copy")))
    assert out["ok"] is False and out["problems"] and set(out) == {"ok", "problems"}
    assert svc.pipeline_runs()["runs"] == []


def test_a_run_with_no_notebooks_or_data_starts_without_a_sign_in(tmp_path):
    svc = Svc(tmp_path)
    svc.auth = SimpleNamespace(storage_token=lambda: pytest.fail("no lakehouse to sign in for"))
    out = svc.pipeline_run_start(text=SIMPLE)
    assert out["ok"] is True and out["state"] == "running"
    assert finish(svc, out["run_id"])["state"] == "succeeded"


def test_a_run_whose_notebooks_read_a_lakehouse_signs_in_first(tmp_path, monkeypatch):
    class Notebooks:
        def __init__(self, *args):
            pass

        def lakehouses(self, acts):
            return ["lh"]

        def stop(self):
            pass

        def finish(self):
            pass

        def notes(self):
            return []

    monkeypatch.setattr(ps, "LocalNotebooks", Notebooks)
    svc = _with(tmp_path, "_validate_kernel", "notebooks")
    signs = []
    svc.auth = SimpleNamespace(storage_token=lambda: signs.append(1))
    finish(svc, svc.pipeline_run_start(text=SIMPLE, notebooks={})["run_id"])
    assert signs == [1]


def test_a_runs_thread_is_a_daemon(tmp_path, monkeypatch):
    made = []
    real = threading.Thread

    def thread(*args, **kwargs):
        t = real(*args, **kwargs)
        made.append(t)
        return t

    monkeypatch.setattr(ps.threading, "Thread", thread)
    svc = Svc(tmp_path)
    run_id = svc.pipeline_run_start(text=SIMPLE)["run_id"]
    finish(svc, run_id)
    mine = [t for t in made if t.name == f"pipeline-run-{run_id[:8]}"]
    assert [t.daemon for t in mine] == [True]


def test_a_finished_run_is_audited_with_how_it_ended(tmp_path):
    svc = Svc(tmp_path)
    lines = []
    svc._audit_finished = lambda *a: lines.append(a)
    run_id = svc.pipeline_run_start(text=pipeline(wait("W", 1)))["run_id"]
    record = saved(tmp_path, run_id)
    end = time.monotonic() + 10
    while not lines and time.monotonic() < end:
        time.sleep(0.02)
    [(action, outcome, seconds, detail)] = lines
    assert (action, outcome) == ("pipeline_run_finished", "")
    assert seconds == record["seconds"] and seconds > 0
    assert detail == {"run_id": run_id, "pipeline": "p", "state": "succeeded"}


def test_a_running_run_is_saved_at_most_twice_a_second_but_its_end_at_once(tmp_path):
    svc = Svc(tmp_path)
    run_id = svc.pipeline_run_start(text=SIMPLE)["run_id"]
    finish(svc, run_id)
    saved(tmp_path, run_id)
    store, live = svc._runs()
    save = live[run_id].on_change
    writes = []
    store.save = lambda record: writes.append(record["state"])

    def r(state):
        return SimpleNamespace(state=state, to_json=lambda: {"run_id": run_id, "state": state})

    time.sleep(ps._SAVE_EVERY + 0.1)
    save(r("running"))
    save(r("running"))
    save(r("pending"))
    save(r("succeeded"))
    assert writes == ["running", "succeeded"]


# ---- cancel

def test_cancel_of_a_run_that_isnt_going_says_its_state(tmp_path):
    svc = Svc(tmp_path)
    assert svc.pipeline_run_cancel("nope") == {"cancelled": False, "state": "unknown"}
    run_id = svc.pipeline_run_start(text=SIMPLE)["run_id"]
    finish(svc, run_id)
    assert svc.pipeline_run_cancel(run_id) == {"cancelled": False, "state": "succeeded"}


# ---- listing

def test_runs_are_listed_newest_first(tmp_path):
    svc = Svc(tmp_path)
    store, _ = svc._runs()
    for rid, started in (("a", "2026-11-01T00:00:00"), ("b", "2026-11-03T00:00:00"), ("c", "2026-11-02T00:00:00")):
        store.save({"run_id": rid, "pipeline": "p", "state": "succeeded", "started": started})
    assert [r["run_id"] for r in svc.pipeline_runs()["runs"]] == ["b", "c", "a"]


def _folder(path, name):
    path.mkdir(parents=True)
    (path / "pipeline-content.json").write_text(pipeline(setvar("S", "v", "x"), name=name), encoding="utf-8")


def test_pipelines_are_found_four_folders_down_and_no_further(tmp_path):
    _folder(tmp_path / "a" / "b" / "c" / "d" / "deep.DataPipeline", "deep")
    _folder(tmp_path / "a" / "b" / "c" / "d" / "e" / "deeper.DataPipeline", "deeper")
    _folder(tmp_path / "top.DataPipeline", "top")
    assert [p.name for p in ps._pipeline_folders(tmp_path)] == ["deep.DataPipeline", "top.DataPipeline"]


def test_a_linked_folder_is_not_followed(tmp_path):
    _folder(tmp_path / "elsewhere" / "x.DataPipeline", "x")
    root = tmp_path / "repo"
    root.mkdir()
    try:
        os.symlink(tmp_path / "elsewhere", root / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this PC can't make a folder link")
    assert list(ps._pipeline_folders(root)) == []


def test_the_list_names_a_repository_by_its_folder_and_reads_two_hundred_runs(tmp_path, monkeypatch):
    repo = tmp_path / "MyRepo"
    _folder(repo / "a.DataPipeline", "a")
    svc = Svc(tmp_path / "app")
    svc.git_repos = lambda: {"repos": [{"exists": True, "path": str(repo)}]}
    limits = []
    real = Svc.pipeline_runs

    def runs(self, limit=50):
        limits.append(limit)
        return real(self, limit)

    monkeypatch.setattr(Svc, "pipeline_runs", runs)
    [row] = svc.pipeline_list()["pipelines"]
    assert row["repo"] == "MyRepo" and row["name"] == "a" and row["last_run"] is None
    assert limits == [200]


def test_a_record_without_values_keeps_its_parameter_names_only(tmp_path):
    record = {"run_id": "r", "parameters": {"b": 1, "a": 2}, "variables": {"v": 1}, "return_value": 3,
              "activities": [{"name": "x", "output": 1, "child_run": {}, "error": {"errorCode": "E", "message": "m"}}]}
    out = ps._without_values(record)
    assert out == {"run_id": "r", "parameters": ["a", "b"],
                   "activities": [{"name": "x", "error": {"errorCode": "E"}}]}
    assert json.dumps(out)
