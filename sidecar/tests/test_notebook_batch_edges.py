"""Running several notebooks from Home: the edges the main tests don't reach. A run log that can't be
read, too many notebooks, live cell progress, a run that ends without a result or can't start, the
heartbeat, a notebook whose run raises, cleaning up old batches, and Run in Fabric from Home."""

from __future__ import annotations

import json
import os
import time

import pytest

from fabric_desktop import jsonfile
from fabric_desktop import notebook_batch_service as nbs
from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.service import Service

from test_schedules import FakeScheduler

CELLS = [{"type": "code", "source": "a = 1"}]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(Service, "pc_schedules", property(lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe")))
    monkeypatch.setattr(nbs.subprocess, "Popen", lambda args, **kw: None)
    s = Service(root=tmp_path / "app")
    s.notebooks.save("A", CELLS)
    s.notebooks.save("B", CELLS)
    return s


def test_details_leave_last_run_empty_when_the_run_log_cant_be_read(svc, monkeypatch, caplog):
    def broken(name, limit=1):
        raise OSError("log locked")

    monkeypatch.setattr(svc, "notebook_runs", broken)
    [row] = svc.notebook_details(["A"])
    assert row["last_run"] is None and "Couldn't read the runs of 'A'" in caplog.text


def test_too_many_notebooks_at_once_is_refused(svc, monkeypatch):
    monkeypatch.setattr(nbs, "MAX_NOTEBOOKS", 1)
    with pytest.raises(ValueError, match="Up to 1 notebooks"):
        svc.notebook_batch_start(["A", "B"])


def test_a_running_notebook_shows_its_cell_and_a_bad_progress_file_is_ignored(svc):
    batch = svc.notebook_batch_start(["A", "B"])
    path = svc._batch_file(batch["id"])
    saved = jsonfile.read_json(path, None)
    saved["items"][0]["status"] = saved["items"][1]["status"] = "running"
    jsonfile.write_json(path, saved)
    jsonfile.write_text(svc._batch_dir() / f"{batch['id']}.0.progress", json.dumps({"cell": 2, "cells": 5}))
    jsonfile.write_text(svc._batch_dir() / f"{batch['id']}.1.progress", "[1, 2]")
    items = svc.notebook_batch(batch["id"])["items"]
    assert (items[0]["cell"], items[0]["cells"]) == (2, 5)
    assert "cell" not in items[1]


def test_a_batch_that_isnt_on_this_pc_is_refused_for_the_background_run(svc):
    with pytest.raises(LookupError, match="isn't on this PC"):
        svc.run_notebook_batch("0123456789ab")
    with pytest.raises(LookupError, match="isn't part of this run"):
        svc.run_batch_notebook("0123456789ab", 0)
    batch = svc.notebook_batch_start(["A"])
    with pytest.raises(LookupError, match="isn't part of this run"):
        svc.run_batch_notebook(batch["id"], 1)


def test_a_run_without_a_result_line_or_that_cant_start_fails_only_its_own_notebook(svc, monkeypatch):
    batch = svc.notebook_batch_start(["A", "B"], parallel=1)

    def run(args, root, stop):
        if args[-1] == "0":
            return "Traceback ...\n", 3, False
        raise FileNotFoundError("python.exe")

    monkeypatch.setattr(nbs, "_run_notebook_process", run)
    out = svc.run_notebook_batch(batch["id"])
    a, b = out["items"]
    assert out["state"] == "done"
    assert (a["status"], a["message"]) == ("failed", "The run stopped without a result (exit code 3)")
    assert b["status"] == "failed" and "python.exe" in b["message"]


def test_the_heartbeat_saves_while_notebooks_run(svc, monkeypatch):
    monkeypatch.setattr(nbs, "HEARTBEAT_SECONDS", 0.01)
    batch = svc.notebook_batch_start(["A"])
    beats = []
    real = jsonfile.write_json

    def counting(path, data, *a, **kw):
        beats.append(data.get("state"))
        return real(path, data, *a, **kw)

    monkeypatch.setattr(nbs.jsonfile, "write_json", counting)

    def slow(args, root, stop):
        time.sleep(0.2)
        return '{"status": "ok", "message": "1 cell ran"}\n', 0, False

    monkeypatch.setattr(nbs, "_run_notebook_process", slow)
    out = svc.run_notebook_batch(batch["id"])
    assert out["items"][0]["status"] == "ok"
    # running, the item running, at least one beat, the item done, done
    assert beats.count("running") >= 3 and beats[-1] == "done"


def test_a_notebook_whose_run_raises_is_logged_as_failed(svc, monkeypatch):
    batch = svc.notebook_batch_start(["A"])

    def broken(name, progress):
        raise RuntimeError("kernel died")

    monkeypatch.setattr(svc, "_run_notebook_here", broken)
    row = svc.run_batch_notebook(batch["id"], 0)
    assert (row["status"], row["message"], row["trigger"]) == ("failed", "RuntimeError: kernel died", "home")
    assert svc.pc_schedules.runs()[0]["status"] == "failed"


def test_old_batches_are_forgotten_and_an_unreadable_one_is_skipped(svc, monkeypatch):
    svc._forget_old_batches()  # no folder yet: nothing to do
    batch = svc.notebook_batch_start(["A"])
    d = svc._batch_dir()
    old = d / "aaaaaaaaaaaa.json"
    old.write_text("{}", encoding="utf-8")
    week_ago = time.time() - 8 * 86400
    os.utime(old, (week_ago, week_ago))
    stuck = d / "bbbbbbbbbbbb.json"
    stuck.write_text("{}", encoding="utf-8")
    os.utime(stuck, (week_ago, week_ago))
    real = type(stuck).unlink

    def unlink(self, *a, **kw):
        if self.name == stuck.name:
            raise PermissionError("in use")
        return real(self, *a, **kw)

    monkeypatch.setattr(type(stuck), "unlink", unlink)
    svc._forget_old_batches()
    assert not old.exists() and stuck.exists() and svc._batch_file(batch["id"]).exists()


def test_run_in_fabric_from_home_publishes_the_saved_notebook(svc, monkeypatch):
    lh = {"workspace_id": "w1", "lakehouse_id": "l1", "name": "LH"}
    svc.notebooks.save("C", CELLS, lakehouse=lh, compute="fabric")
    called = {}

    def run(name, cells, lakehouse, compute, workspace, replace_fabric_changes=False):
        called.update(name=name, cells=cells, lakehouse=lakehouse, compute=compute, replace=replace_fabric_changes)
        return {"job_id": "j"}

    monkeypatch.setattr(svc, "run_notebook_in_fabric", run)
    assert svc.notebook_batch_fabric("C", replace_fabric_changes=True) == {"job_id": "j"}
    assert called["name"] == "C" and called["lakehouse"]["lakehouse_id"] == "l1" and called["replace"] is True
    assert [c["source"] for c in called["cells"]] == ["a = 1"]
