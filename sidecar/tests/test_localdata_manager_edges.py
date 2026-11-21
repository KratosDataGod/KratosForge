"""The local engines' manager: adding a folder that is already there, a refresh that fails, and background
queries that are unknown, finished or swept. Files on this PC only."""

from __future__ import annotations

import time

import pytest

from fabric_desktop.localdata import manager as mgr
from fabric_desktop.localdata.manager import LocalData


@pytest.fixture
def local(tmp_path):
    ld = LocalData(tmp_path / "local-data")
    pid = ld.create("Edges")["id"]
    yield ld, pid
    ld.close_all()


def test_a_folder_already_in_the_project_is_refused(local, tmp_path):
    ld, pid = local
    data = tmp_path / "data"
    data.mkdir()
    ld.add_source(pid, str(data))
    with pytest.raises(ValueError, match="is already a folder of Edges"):
        ld.add_source(pid, str(data))


def test_a_refresh_that_fails_leaves_the_folder_out(local, tmp_path, monkeypatch):
    ld, pid = local
    data = tmp_path / "data"
    data.mkdir()
    engine = ld.engine(pid)

    def broken(path):
        raise OSError("the folder went away")

    monkeypatch.setattr(engine, "refresh", broken)
    with pytest.raises(OSError, match="went away"):
        ld.add_source(pid, str(data))
    assert engine.project.sources == []


def test_an_unknown_query_has_no_status_and_cant_be_cancelled(local):
    ld, _ = local
    with pytest.raises(KeyError, match="results are kept for 10 minutes"):
        ld.status("nope")
    assert ld.cancel("nope") == {"cancelled": False}
    ld._queries["done"] = {"query_id": "done", "project": "p", "state": "done", "started_at": time.time()}
    assert ld.cancel("done") == {"cancelled": False}


def test_a_finished_result_nobody_collected_is_swept_after_ten_minutes(local, monkeypatch):
    ld, _ = local
    now = time.time()
    ld._queries["old"] = {"state": "done", "started_at": now - 900, "finished_at": now - mgr._KEEP_FINISHED - 1}
    ld._queries["new"] = {"state": "done", "started_at": now, "finished_at": now}
    ld._queries["running"] = {"state": "running", "started_at": now - 900}
    ld._sweep()
    assert set(ld._queries) == {"new", "running"}
