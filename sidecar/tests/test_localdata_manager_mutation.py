"""Mutation-testing pins for localdata/manager (docs/quality/mutation-testing.md, batch 5)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import manager as M


@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.fixture
def pid(local):
    return local.create("Mgr")["id"]


class _Clock:
    """manager's own ``time``: only the module's name is replaced, never the real clock."""

    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now


def test_a_folder_already_added_is_refused(local, pid, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.csv").write_text("x\n1\n", encoding="utf-8")
    local.add_source(pid, str(data))
    with pytest.raises(ValueError, match="is already a folder of Mgr"):
        local.add_source(pid, str(data))
    assert len(local.project(pid).sources) == 1


def test_a_folder_whose_first_look_fails_is_taken_back_off(local, pid, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    engine = local.engine(pid)

    def refresh(path):
        raise RuntimeError("couldn't read it")
    monkeypatch.setattr(engine, "refresh", refresh)
    with pytest.raises(RuntimeError, match="couldn't read it"):
        local.add_source(pid, str(data))
    assert engine.project.sources == []


def test_the_status_of_an_unknown_query_says_so(local):
    with pytest.raises(KeyError, match="isn't running and has no result waiting"):
        local.status("nope")


def test_elapsed_is_seconds_since_the_start_to_a_tenth(local, monkeypatch):
    local._queries["q"] = {"query_id": "q", "project": "p", "state": "running", "started_at": 100.0}
    monkeypatch.setattr(M, "time", _Clock(102.26))
    out = local.status("q")
    assert out == {"query_id": "q", "project": "p", "state": "running", "elapsed": 2.3}
    assert "q" in local._queries  # a running query isn't collected


def test_cancelling_an_unknown_or_finished_query_cancels_nothing(local):
    assert local.cancel("nope") == {"cancelled": False}
    local._queries["q"] = {"query_id": "q", "project": "p", "state": "done", "started_at": 1.0}
    assert local.cancel("q") == {"cancelled": False}


def test_a_finished_result_is_kept_ten_minutes_then_swept(local, monkeypatch):
    assert M._KEEP_FINISHED == 600
    for qid, finished in (("old", 1000.0), ("edge", 1400.0), ("new", 1900.0)):
        local._queries[qid] = {"query_id": qid, "state": "done", "started_at": 0.0, "finished_at": finished}
    local._queries["running"] = {"query_id": "running", "state": "running", "started_at": 0.0}
    monkeypatch.setattr(M, "time", _Clock(2000.0))
    local._sweep()
    assert sorted(local._queries) == ["edge", "new", "running"]  # 600 s exactly is still kept
    monkeypatch.setattr(M, "time", _Clock(2000.5))
    local._sweep()
    assert sorted(local._queries) == ["new", "running"]


def test_a_background_query_runs_on_its_own_named_daemon_thread(local, pid, monkeypatch):
    made = []

    class Thread:
        def __init__(self, target, name, daemon):
            made.append((name, daemon))
            self.target = target

        def start(self):
            self.target()  # run it here, so the test sees the result at once
    monkeypatch.setattr(M, "threading", SimpleNamespace(Thread=Thread, Lock=threading.Lock))
    started = local.start(pid, "SELECT 1 AS a")
    qid = started["query_id"]
    assert started == {"query_id": qid, "state": "running"}
    assert made == [(f"local query {qid[:8]}", True)]
    done = local.status(qid)
    assert done["state"] == "done" and done["result"]["rows"] == [[1]]
    assert qid not in local._queries  # collected


def test_a_failed_background_query_reports_its_error(local, pid, monkeypatch):
    class Thread:
        def __init__(self, target, name, daemon):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(M, "threading", SimpleNamespace(Thread=Thread, Lock=threading.Lock))
    qid = local.start(pid, "SELECT * FROM no_such_table")["query_id"]
    out = local.status(qid)
    assert out["state"] == "error" and "no_such_table" in out["error"]
    assert abs(time.time() - out["finished_at"]) < 60
