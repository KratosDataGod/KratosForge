"""Validate notebook's service (validate_service.py): the clean run's edges the end-to-end tests don't reach.
Cancel between cells or while a cell starts, a cell stopped by a limit, a kernel that can't start, a
sandbox that can't be deleted, a result that can't be saved or put together, and the status, cancel and
save calls on runs that aren't there. The kernel is a stand-in, so no process starts; nothing reaches
OneLake."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import jsonfile, known_folders, validate
from fabric_desktop import validate_service as vs

RID = "validate-20261111-120000-abc123"
LH = {"workspace_id": "ws1", "lakehouse_id": "lh1", "name": "LH", "workspace_name": "Data"}


class Kernel:
    """Answers each started cell with the next snapshot; ``on_start`` runs as a cell starts."""

    def __init__(self, snaps, alive=True, limit_hit=None, writes=None, on_start=None):
        self.snaps, self._alive, self.limit_hit = list(snaps), alive, limit_hit
        self.writes, self.on_start = writes, on_start
        self.started, self.stopped, self.closed, self.bound = [], 0, False, None

    def start(self, source):
        self.started.append(source)
        if self.on_start:
            self.on_start()
        return self.snaps.pop(0)

    def poll(self, version, timeout):  # pragma: no cover - every snapshot here is final
        raise AssertionError("not polled")

    def alive(self):
        return self._alive

    def stop(self):
        self.stopped += 1

    def close(self):
        self.closed = True

    def bind_proof(self, proof, side, root, options):
        self.bound = (proof, side, root)

    def proof_log(self):
        return {"writes": self.writes or {}}


class Bare(vs.ValidateMixin):
    def __init__(self, root, kernel=None, delete_error=None):
        self.root, self.kernel, self.delete_error = root, kernel, delete_error
        self.deleted, self.observed = [], []

    def _validate_kernel(self, lakehouse_ref):
        if isinstance(self.kernel, Exception):
            raise self.kernel
        return self.kernel

    def _resources_dir(self, name):
        return self.root / "resources" / name

    def _onelake_writes(self, name):
        return []

    def _schemas_enabled(self, ws, lh):
        return True

    def _proof_tables(self, ref):
        return type("T", (), {"versions": lambda self: {"dbo.orders": 3}})()

    def _proof_location(self, ref):
        return "abfss://lake", {}

    def _proof_delete_folder(self, ref, rel):
        self.deleted.append(rel)
        if self.delete_error:
            raise self.delete_error

    def _progress_observe(self, record):
        self.observed.append(record["id"])
        return []


def cell(source, n=1):
    return {"type": "code", "source": source, "cell_id": f"c{n}", "meta": {}}


def record(cells, rid=RID, sandbox=False):
    return {"id": rid, "name": "Orders", "state": "running", "started_at": vs._now(), "finished_at": None,
            "message": "", "lakehouse": {}, "runs_on": "pc", "code": {}, "cells_total": len(cells), "cell": None,
            "cells": [], "run": {"status": "running", "seconds": None, "failed_cell": None, "error": None,
                                 "stopped_by": None, "sandbox_tables": None},
            "findings": [], "counts": {}, "tables": [], "uncontained": [], "limits": validate.LIMITS,
            "verdict": None, "summary": "", "sandbox": "x" if sandbox else "", "environment": None,
            "_cancel": False, "_kernel": None}


def run(svc, cells, ref=None, rid=RID):
    rec = record(cells, rid)
    svc._validations()[rec["id"]] = rec
    svc._validate_run(rec, cells, list(enumerate(cells, 1)), ref)
    return rec


OK = {"status": "ok", "seconds": 0.1, "outputs": []}


def test_cancel_between_cells_stops_before_the_next_one_starts(tmp_path):
    cells = [cell("x = 1", 1), cell("y = 2", 2)]
    svc = Bare(tmp_path)
    svc.kernel = Kernel([OK, OK], on_start=lambda: svc._validations()[RID].update(_cancel=True))
    rec = run(svc, cells)
    assert svc.kernel.started == ["x = 1"]
    assert rec["run"]["status"] == "cancelled" and rec["state"] == "cancelled" and rec["verdict"] is None
    assert rec["summary"] == "Cancelled. Nothing was written to the lakehouse's tables."
    assert svc.kernel.closed and rec["_kernel"] is None


def test_cancel_while_a_cell_starts_stops_the_kernel_and_keeps_the_cells_time(tmp_path):
    svc = Bare(tmp_path)
    svc.kernel = Kernel([{"status": "running", "outputs": []}],
                        on_start=lambda: svc._validations()[RID].update(_cancel=True))
    rec = run(svc, [cell("import time; time.sleep(60)")])
    assert svc.kernel.stopped == 1
    assert rec["cells"] == [{"cell": 1, "cell_id": "c1", "status": "cancelled", "seconds": 0}]
    assert rec["run"]["status"] == "cancelled" and rec["run"]["failed_cell"] is None


@pytest.mark.parametrize("limit, says", [("memory", "the notebook memory limit"), ("disk", "the disk limit")])
def test_a_cell_stopped_by_a_limit_says_which(tmp_path, limit, says):
    err = {"type": "error", "ename": "MemoryError", "evalue": "token=abc.def.ghi",
           "traceback": ["Traceback (most recent call last):", "  cell 1", "MemoryError"]}
    svc = Bare(tmp_path, Kernel([OK, {"status": "error", "seconds": 2.0, "outputs": [err]}], limit_hit=limit))
    rec = run(svc, [cell("x = 1", 1), cell("big = [0] * 10**12", 2)])
    r = rec["run"]
    assert r["status"] == "failed" and r["failed_cell"] == 2 and r["stopped_by"] == says
    assert r["error"]["ename"] == "MemoryError"
    assert r["error"]["traceback"] == "Traceback (most recent call last):\n  cell 1\nMemoryError"
    assert rec["summary"].startswith("The clean run stopped at cell 2 after 1 cell succeeded.")
    assert rec["state"] == "done"


def test_a_kernel_that_cant_start_is_an_error_finding_with_secrets_removed(tmp_path):
    svc = Bare(tmp_path, RuntimeError("couldn't start: password=hunter2"))
    rec = run(svc, [cell("x = 1")])
    assert rec["run"]["status"] == "error" and rec["state"] == "done"
    assert rec["message"].startswith("RuntimeError: couldn't start") and "hunter2" not in rec["message"]
    assert rec["summary"] == "The clean run couldn't finish."
    [finding] = [f for f in rec["findings"] if f["code"] == "run_error"]
    assert finding["severity"] == "error" and finding["title"] == "The clean run couldn't finish"
    assert finding["text"] == rec["message"] and finding["found_by"] == ["run"]


def test_a_sandbox_that_cant_be_deleted_is_named_so_the_person_can(tmp_path):
    kernel = Kernel([OK], writes={"dbo.big": 1})
    svc = Bare(tmp_path, kernel, delete_error=PermissionError("403"))
    rec = run(svc, [cell("x = 1")], ref=LH)
    assert kernel.bound[0] == {"id": RID, "pins": {"dbo.orders": 3}, "flat": False, "seed": 0}
    assert rec["run"]["sandbox_tables"] == ["dbo.big"]
    assert svc.deleted == [f"{vs.PROOF_ROOT}/{RID}"]
    assert rec["message"] == (f"The sandbox folder {vs.PROOF_ROOT}/{RID} couldn't be deleted (PermissionError); "
                              "if it's there, delete it in Fabric.")
    assert rec["state"] == "done"


def test_a_kernel_that_ended_cant_list_its_sandbox_tables_so_the_folder_is_still_deleted(tmp_path):
    svc = Bare(tmp_path, Kernel([OK], alive=False))
    rec = run(svc, [cell("x = 1")], ref=LH)
    assert rec["run"]["sandbox_tables"] is None
    assert svc.deleted == [f"{vs.PROOF_ROOT}/{RID}"]


def test_a_sandbox_is_never_deleted_for_an_id_that_isnt_a_validations(tmp_path):
    svc = Bare(tmp_path, Kernel([OK], writes={"dbo.big": 1}))
    run(svc, [cell("x = 1")], ref=LH, rid="validate-../../Tables")
    assert svc.deleted == []


def test_a_result_that_cant_be_saved_on_this_pc_still_finishes(tmp_path, monkeypatch):
    def full(path, text):
        raise OSError("No space left on device")

    monkeypatch.setattr(jsonfile, "write_text", full)
    svc = Bare(tmp_path, Kernel([OK]))
    rec = run(svc, [cell("x = 1")])
    assert rec["message"] == "The result couldn't be saved on this PC: No space left on device"
    assert rec["state"] == "done" and svc.observed == [RID]


def test_a_result_that_cant_be_put_together_never_stays_running(tmp_path, monkeypatch):
    def broken(cells, run, findings):
        raise KeyError("seconds")

    monkeypatch.setattr(validate, "run_findings", broken)
    rec = run(Bare(tmp_path, Kernel([OK])), [cell("x = 1")])
    assert rec["state"] == "failed" and rec["verdict"] is None and rec["finished_at"]
    assert rec["message"] == "The result couldn't be put together: KeyError: 'seconds'"


def test_status_cancel_and_save_on_runs_that_arent_there_or_arent_done(tmp_path, monkeypatch):
    svc = Bare(tmp_path)
    with pytest.raises(LookupError, match="No validation nope"):
        svc.notebook_validate_status("nope")
    assert svc.notebook_validate_cancel("nope") == {"cancelled": False}
    rec = record([cell("x = 1")])
    svc._validations()[RID] = rec
    with pytest.raises(RuntimeError, match="still running"):
        svc.notebook_validate_save(RID)
    rec["state"] = "done"
    assert svc.notebook_validate_cancel(RID) == {"cancelled": False}
    monkeypatch.setattr(known_folders, "downloads", lambda: tmp_path / "Downloads")
    with pytest.raises(ValueError, match="markdown or json"):
        svc.notebook_validate_save(RID, format="pdf")
    saved = svc.notebook_validate_save(RID, format="json")
    assert saved["path"].endswith(".json") and json.loads(open(saved["path"], encoding="utf-8").read())["id"] == RID


def test_a_profile_that_cant_take_the_validation_leaves_its_result_alone(tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(vs._log, "warning", lambda *a: warned.append(a))
    svc = Bare(tmp_path, Kernel([OK]))

    def refuse(record):
        raise ValueError("profile file is torn")

    svc._progress_observe = refuse
    rec = run(svc, [cell("x = 1")])
    assert rec["state"] == "done" and rec["verdict"] is not None and rec["message"] == ""
    assert "earned" not in rec
    assert warned and "profile file is torn" in warned[0][1]


def test_the_code_check_answers_at_once_with_a_verdict_and_runs_nothing(tmp_path):
    svc = Bare(tmp_path, kernel=AssertionError("no kernel for a check"))
    svc._fabric_observed = lambda: {}
    svc._checks = lambda name: {"load": None, "resources": None}
    out = svc.notebook_validate_check("Orders", [cell("x = 1", 1), cell("print(y)", 2)], runs_on="local")
    assert out["name"] == "Orders" and out["code"] and out["verdict"]
    assert any(f["cell"] == 2 and "y" in f["title"] + f["text"] for f in out["findings"])
    assert svc._validations() == {}
