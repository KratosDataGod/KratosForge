"""Notebook steps of local pipeline runs (pipeline_notebooks.py): what mutation testing (batch 85,
docs/quality/mutation-testing.md) showed no test checked. A fake Service and kernel stand in for the
clean kernel and the lakehouse; nothing runs a notebook or reaches a tenant."""

from types import SimpleNamespace

import pytest

from fabric_desktop import pipeline_notebooks as pn
from fabric_desktop.orchestration.adapters import ActivityFailed
from fabric_desktop.orchestration.definition import Activity

A = Activity(name="Clean", type="TridentNotebook", path="$.a[0]", raw={"typeProperties": {"notebookId": "nb-1"}})


class Kernel:
    limit_hit = ""

    def __init__(self, alive=True):
        self.closed, self.stopped, self._alive = False, 0, alive

    def start(self, source):
        return {"status": "running"}

    def alive(self):
        return self._alive

    def stop(self):
        self.stopped += 1

    def close(self):
        self.closed = True


def svc():
    nb = {"cells": [{"type": "code", "source": "x = 1"}]}
    return SimpleNamespace(notebooks=SimpleNamespace(load=lambda name: nb), _validate_kernel=lambda ref: Kernel())


def test_only_text_names_map_activities_to_notebooks():
    r = pn.LocalNotebooks(svc(), "r1", {"Clean": "clean", "Bad": 5, 3: "three"})
    assert r.map == {"Clean": "clean"}


def test_two_notebook_steps_run_at_once_and_a_third_waits():
    r = pn.LocalNotebooks(svc(), "r1", {})
    assert pn.MAX_KERNELS == 2
    assert r._kernels.acquire(blocking=False) and r._kernels.acquire(blocking=False)
    assert not r._kernels.acquire(blocking=False)


def test_an_uncontained_cell_needs_allow_uncontained(monkeypatch):
    monkeypatch.setattr(pn.validate, "run_plan", lambda cells, lh: {
        "ok": True, "blockers": [], "uncontained": [{"cell": 1, "text": "calls the network"}]})
    [problem] = pn.LocalNotebooks(svc(), "r1", {"Clean": "clean"}).check(A)
    assert problem["code"] == "notebook_uncontained" and "Confirm with allow_uncontained" in problem["message"]
    assert pn.LocalNotebooks(svc(), "r1", {"Clean": "clean"}, allow_uncontained=True).check(A) == []


def test_a_step_reports_its_seconds_to_the_millisecond(monkeypatch):
    ticks = iter([100.0, 101.23456])
    monkeypatch.setattr(pn.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(pn.notebook_cells, "wait", lambda k, snap, stop: ({"status": "ok"}, []))
    out = pn.LocalNotebooks(svc(), "r1", {"Clean": "clean"}).run(A, {}, lambda: False, None)
    assert out["seconds"] == 1.235 and out["cells_run"] == 1 and "sandbox" not in out


def test_stop_stops_only_the_kernels_still_alive():
    r = pn.LocalNotebooks(svc(), "r1", {})
    live, gone = Kernel(), Kernel(alive=False)
    r._live.update({live, gone})
    r.stop()
    assert live.stopped == 1 and gone.stopped == 0


def test_a_run_with_no_steps_and_no_lakehouse_has_only_its_after_notes():
    r = pn.LocalNotebooks(svc(), "r1", {})
    assert r.notes() == []
    r._after.append("kept for later")
    assert r.notes() == ["kept for later"]


def test_notes_name_a_lakehouse_by_its_name_or_else_its_id():
    r = pn.LocalNotebooks(svc(), "r1", {}, keep_outputs=True)
    r._lakes["id-1"] = {"ref": {"name": "LH_Silver", "lakehouse_id": "id-1"}, "written": set()}
    r._lakes["id-2"] = {"ref": {"lakehouse_id": "id-2"}, "written": set()}
    [note] = r.notes()
    assert "tables in LH_Silver, id-2 at the versions" in note and "id-1" not in note
    assert f"kept in {r.folder}" in note


def test_an_unmapped_step_names_the_notebook_id_it_runs():
    [problem] = pn.LocalNotebooks(svc(), "r1", {}).check(A)
    assert problem["code"] == "notebook_unmapped" and "Clean runs notebook nb-1;" in problem["message"]
    bare = Activity(name="Bare", type="TridentNotebook", path="$.a[1]", raw={"typeProperties": {}})
    assert "runs notebook (no notebookId);" in pn.LocalNotebooks(svc(), "r1", {}).check(bare)[0]["message"]


def test_notes_count_the_steps_that_ran_without_a_lakehouse():
    r = pn.LocalNotebooks(svc(), "r1", {})
    r._ran = 1
    assert r.notes()[0].startswith("Ran on this PC; 1 notebook step ran in a fresh local kernel")
    r._ran = 2
    assert r.notes()[0].startswith("Ran on this PC; 2 notebook steps ran in a fresh local kernel")


def stopper(monkeypatch, now):
    """notebook_cells.wait asks stop() once, as it does while a cell runs, then reports the cell ok."""
    asked = []
    monkeypatch.setattr(pn.time, "monotonic", lambda: now)
    monkeypatch.setattr(pn, "awake_monotonic", lambda: now)  # the deadline's clock

    def wait(k, snap, stop):
        asked.append(stop())
        return {"status": "ok"}, []
    monkeypatch.setattr(pn.notebook_cells, "wait", wait)
    return asked


def test_a_cell_with_no_deadline_never_times_out(monkeypatch):
    asked = stopper(monkeypatch, 50.0)
    pn.LocalNotebooks(svc(), "r1", {})._cell(Kernel(), "x", lambda: False, None, "clean", 1)
    assert asked == [False]


def test_a_cell_times_out_only_after_its_deadline(monkeypatch):
    asked = stopper(monkeypatch, 50.0)
    pn.LocalNotebooks(svc(), "r1", {})._cell(Kernel(), "x", lambda: False, 50.0, "clean", 1)  # at it: not yet
    assert asked == [False]
    with pytest.raises(ActivityFailed, match="policy timeout"):
        pn.LocalNotebooks(svc(), "r1", {})._cell(Kernel(), "x", lambda: False, 49.9, "clean", 1)


def failing(monkeypatch, outputs):
    monkeypatch.setattr(pn.notebook_cells, "wait", lambda k, snap, stop: ({"status": "error"}, outputs))


def test_a_failed_cell_names_the_error_and_its_value(monkeypatch):
    failing(monkeypatch, [{"type": "error", "ename": "ValueError", "evalue": "bad row"}])
    with pytest.raises(ActivityFailed) as raised:
        pn.LocalNotebooks(svc(), "r1", {})._cell(Kernel(), "x", lambda: False, None, "clean", 4)
    assert str(raised.value) == "Notebook 'clean' failed at cell 4: ValueError: bad row"
    assert raised.value.output == {"notebook": "clean", "failed_cell": 4}
    failing(monkeypatch, [])
    with pytest.raises(ActivityFailed) as raised:
        pn.LocalNotebooks(svc(), "r1", {})._cell(Kernel(), "x", lambda: False, None, "clean", 4)
    assert str(raised.value) == "Notebook 'clean' failed at cell 4: error"


def test_a_failed_cells_message_is_cut_at_2000_characters(monkeypatch):
    failing(monkeypatch, [{"type": "error", "ename": "ValueError", "evalue": "x " * 3000}])
    with pytest.raises(ActivityFailed) as raised:
        pn.LocalNotebooks(svc(), "r1", {})._cell(Kernel(), "x", lambda: False, None, "clean", 1)
    assert len(str(raised.value)) == 2000


class LakeKernel(Kernel):
    def __init__(self):
        super().__init__()
        self.bound = None

    def bind_proof(self, proof, where, root, options):
        self.bound = (proof, where, root, options)

    def proof_log(self):
        return {"writes": {"dbo.out": 1}}


def test_a_step_on_a_lakehouse_reads_pinned_versions_and_writes_to_the_runs_folder(monkeypatch):
    ref = {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Silver"}
    k = LakeKernel()
    s = svc()
    s.notebooks = SimpleNamespace(load=lambda name: {"cells": [{"type": "code", "source": "x = 1"}], "lakehouse": ref})
    s._validate_kernel = lambda r: k
    s._schemas_enabled = lambda ws, lh: True
    s._proof_tables = lambda r: SimpleNamespace(versions=lambda: {"dbo.orders": 7})
    s._proof_location = lambda r: ("abfss://root", {"timeout": 30})
    monkeypatch.setattr(pn.notebook_cells, "wait", lambda kk, snap, stop: ({"status": "ok"}, []))
    r = pn.LocalNotebooks(s, "r1", {"Clean": "clean"})
    out = r.run(A, {}, lambda: False, None)
    assert k.bound == ({"id": "pipeline-r1", "pins": {"dbo.orders": 7}, "flat": False, "seed": 0, "seeded": []},
                       "pc", "abfss://root", {"timeout": 30})
    assert out["tables_written"] == ["dbo.out"] and out["sandbox"] == f"{r.folder}/pc"
