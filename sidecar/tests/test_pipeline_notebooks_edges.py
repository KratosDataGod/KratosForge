"""Notebook steps in local pipeline runs: the edges the notebook tests don't reach. A fake Service and
kernel stand in for the clean kernel and the lakehouse; nothing runs a notebook or reaches a tenant."""

import threading
from types import SimpleNamespace

import pytest

from fabric_desktop import pipeline_notebooks as pn
from fabric_desktop.orchestration.adapters import ActivityFailed
from fabric_desktop.orchestration.definition import Activity

A = Activity(name="Clean", type="TridentNotebook", path="$.a[0]", raw={"typeProperties": {"notebookId": "nb-1"}})


class Notebooks:
    def __init__(self, nb=None, error=None):
        self.nb, self.error = nb or {"cells": [{"type": "code", "source": "x = 1"}]}, error

    def load(self, name):
        if self.error:
            raise self.error
        return self.nb


class Kernel:
    limit_hit = ""

    def __init__(self):
        self.closed = False

    def start(self, source):
        return {"status": "running", "source": source}

    def alive(self):
        return True

    def close(self):
        self.closed = True


def svc(**kw):
    return SimpleNamespace(notebooks=Notebooks(**kw), _validate_kernel=lambda ref: Kernel())


def runner(s, **kw):
    return pn.LocalNotebooks(s, "r1", {"Clean": "clean"}, **kw)


def test_a_cell_that_isnt_contained_is_named_before_the_run(monkeypatch):
    monkeypatch.setattr(pn.validate, "run_plan", lambda cells, lh: {
        "ok": False, "blockers": [{"cell": 2, "text": "writes outside the lakehouse"}], "uncontained": []})
    problems = runner(svc()).check(A)
    assert problems[0]["code"] == "notebook_not_contained" and "cell 2 writes outside" in problems[0]["message"]


def test_a_notebook_that_cant_be_opened_adds_no_lakehouse():
    assert runner(svc(error=OSError("gone"))).lakehouses([A]) == []


def test_a_refused_notebook_fails_its_step():
    with pytest.raises(ActivityFailed, match="can't be opened") as raised:
        runner(svc(error=LookupError("clean"))).run(A, {}, lambda: False, None)
    assert raised.value.error["errorCode"] == "LocalNotebookRefused"


def test_a_step_cancelled_while_waiting_for_a_kernel_never_starts_one():
    r = runner(svc())
    r._kernels = threading.BoundedSemaphore(1)
    r._kernels.acquire()
    with pytest.raises(ActivityFailed, match="Cancelled") as raised:
        r.run(A, {}, lambda: True, None)
    assert raised.value.error["errorCode"] == "LocalCancelled"


def test_a_cell_cancelled_while_it_runs_fails_the_step_and_closes_the_kernel(monkeypatch):
    kernels = []

    def kernel(ref):
        kernels.append(Kernel())
        return kernels[-1]

    s = svc()
    s._validate_kernel = kernel
    def wait(k, snap, stop):
        assert stop()  # the person's cancel is what the wait sees
        return {"status": "cancelled"}, []

    monkeypatch.setattr(pn.notebook_cells, "wait", wait)
    with pytest.raises(ActivityFailed, match="Cancelled"):
        runner(s).run(A, {}, lambda: True, None)
    assert kernels[0].closed


def test_a_cell_stopped_by_a_limit_says_which(monkeypatch):
    k = Kernel()
    k.limit_hit = "memory"
    monkeypatch.setattr(pn.notebook_cells, "wait", lambda k, snap, stop: ({"status": "error"}, []))
    with pytest.raises(ActivityFailed, match="stopped by the memory limit") as raised:
        runner(svc())._cell(k, "x", lambda: False, None, "clean", 3)
    assert raised.value.error["errorCode"] == "LocalNotebookFailed"


def test_an_exit_cell_with_no_mark_means_the_notebook_didnt_exit(monkeypatch):
    monkeypatch.setattr(pn.notebook_cells, "wait",
                        lambda k, snap, stop: ({"status": "ok"}, [{"type": "stream", "text": "nothing here"}]))
    assert runner(svc())._exit(Kernel(), lambda: False, None, "clean", 1) is None


def test_a_run_folder_that_cant_be_deleted_goes_in_the_notes():
    def refuse(ref, folder):
        raise PermissionError("locked")

    s = svc()
    s._proof_delete_folder = refuse
    r = runner(s)
    r._lakes["lh"] = {"ref": {"name": "LH_Silver"}, "pins": {}, "flat": False, "written": set()}
    r.finish()
    r.finish()  # only the first call deletes anything
    assert r._after == [(f"The folder {r.folder} in LH_Silver couldn't be deleted (PermissionError); if it's "
                         "there, delete it in Fabric.")]
