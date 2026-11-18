"""validate_service.py's limits and record keeping, pinned so that changes to them fail a test (mutation
testing, batch 88): how many validations are kept and in what order, the traceback cut, the clean
kernel's lakehouse context and when it is sent again, starting without a lakehouse, cancel, a run's
time and summary, and the report's file. The kernel is a stand-in, so no process starts; nothing
reaches OneLake.
"""

from __future__ import annotations

import json

import pytest
from test_validate_service_edges import LH, OK, RID, Bare, Kernel, cell, record, run

from fabric_desktop import jsonfile, known_folders, lakehouse
from fabric_desktop import validate_service as vs


@pytest.fixture(autouse=True)
def private_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _rid(n):
    return f"validate-20261111-{n:06d}-abc123"


# ---- what is kept on this PC

def test_the_newest_hundred_validations_are_kept(tmp_path):
    svc = Bare(tmp_path)
    for n in range(101):
        svc._validation_keep({"id": _rid(n), "name": "NB"})
    rows = svc._validations_saved()
    assert len(rows) == 100 and rows[0]["id"] == _rid(1) and rows[-1]["id"] == _rid(100)


def test_keeping_a_validation_again_replaces_it_and_the_file_is_indented_json(tmp_path):
    svc = Bare(tmp_path)
    svc._validation_keep({"id": _rid(1), "name": "NB", "verdict": None})
    svc._validation_keep({"id": _rid(1), "name": "NB", "verdict": "ok", "_kernel": object()})
    rows = [{"id": _rid(1), "name": "NB", "verdict": "ok"}]
    assert svc._validations_saved() == rows
    assert svc._validations_file().read_text(encoding="utf-8") == json.dumps(rows, indent=1)


def test_status_finds_a_saved_validation_by_its_own_id(tmp_path):
    svc = Bare(tmp_path)
    for n in (1, 2, 3):
        svc._validation_keep({"id": _rid(n), "name": f"NB{n}"})
    assert svc.notebook_validate_status(_rid(2))["name"] == "NB2"


def test_the_list_is_newest_first_and_twenty_by_default(tmp_path):
    svc = Bare(tmp_path)
    rows = [{"id": _rid(n), "name": "NB", "started_at": f"2026-11-{(n * 7) % 25 + 1:02d}T00:00:00+00:00"}
            for n in range(21)]
    jsonfile.write_text(svc._validations_file(), json.dumps(rows))
    out = svc.notebook_validate_list()
    assert len(out) == 20
    starts = [r["started_at"] for r in out]
    assert starts == sorted((r["started_at"] for r in rows), reverse=True)[:20]


def test_a_validation_with_no_start_time_is_listed_last(tmp_path):
    svc = Bare(tmp_path)
    rows = [{"id": _rid(1), "name": "NB"}, {"id": _rid(2), "name": "NB", "started_at": "2026-11-01T00:00:00+00:00"}]
    jsonfile.write_text(svc._validations_file(), json.dumps(rows))
    assert [r["id"] for r in svc.notebook_validate_list()] == [_rid(2), _rid(1)]


# ---- the failed cell's error

def test_a_failed_cells_traceback_keeps_its_last_4000_characters():
    trace = "a" * 1000 + "".join(f"{n:04d}" for n in range(1000))
    out = vs._error([{"type": "error", "ename": "E", "evalue": "v", "traceback": trace}])
    assert out["traceback"] == trace[-4000:] and len(out["traceback"]) == 4000


@pytest.mark.parametrize("value, runs_on", [("pc", "pc"), ("local", "pc"), ("fabric", "fabric"),
                                             ("default", None), (None, None), ("", None)])
def test_runs_on_reads_the_notebooks_setting(value, runs_on):
    assert vs._runs_on(value) == runs_on


# ---- the clean kernel

def test_the_clean_kernel_is_told_its_lakehouse_and_sent_it_again_only_when_it_changes(tmp_path, monkeypatch):
    kernel = vs._CleanKernel({**LH, "workspace_name": "Data"}, False, tmp_path)
    assert kernel._context["workspace_name"] == "Data" and kernel._context["schemas_enabled"] is False
    sent = []
    kernel._request = lambda what, **kw: sent.append((what, kw))
    monkeypatch.setattr(lakehouse, "write_guard", None)
    kernel._sync_lakehouse()
    kernel._sync_lakehouse()
    assert sent == [("attach", {"context": kernel._context, "guard": False})]
    monkeypatch.setattr(lakehouse, "write_guard", lambda *a: None)
    kernel._sync_lakehouse()
    assert sent[1] == ("attach", {"context": kernel._context, "guard": True})


def test_a_clean_kernel_with_no_workspace_name_gets_an_empty_one(tmp_path):
    kernel = vs._CleanKernel({k: v for k, v in LH.items() if k != "workspace_name"}, True, tmp_path)
    assert kernel._context["workspace_name"] == ""


# ---- starting and cancelling

class Startable(Bare):
    def __init__(self, root):
        super().__init__(root)
        self.signed_in = 0
        outer = self

        class Auth:
            def storage_token(self):
                outer.signed_in += 1

        self.auth = Auth()

    def _fabric_observed(self):
        return {}

    def _checks(self, name):
        return {"load": None, "resources": None}


@pytest.fixture
def threads(monkeypatch):
    made = []

    class Thread:
        def __init__(self, **kw):
            made.append(kw)

        def start(self):
            pass

    monkeypatch.setattr(vs.threading, "Thread", Thread)
    return made


def test_a_start_with_no_lakehouse_doesnt_sign_in_and_the_run_is_a_daemon_thread(tmp_path, threads):
    svc = Startable(tmp_path)
    out = svc.notebook_validate_start("NB", [cell("x = 1")])
    assert out["state"] == "running" and svc.signed_in == 0
    assert threads[0]["daemon"] is True


def test_a_start_with_a_lakehouse_signs_in_first(tmp_path, threads):
    svc = Startable(tmp_path)
    svc.notebook_validate_start("NB", [cell("x = 1")], LH)
    assert svc.signed_in == 1


def test_cancel_before_the_kernel_starts_is_still_a_cancel(tmp_path):
    svc = Bare(tmp_path)
    svc._validations()[RID] = record([cell("x = 1")])
    assert svc.notebook_validate_cancel(RID) == {"cancelled": True}
    assert svc._validations()[RID]["_cancel"] is True


def test_cancel_stops_a_running_kernel(tmp_path):
    svc = Bare(tmp_path)
    rec = record([cell("x = 1")])
    rec["_kernel"] = kernel = Kernel([])
    svc._validations()[RID] = rec
    assert svc.notebook_validate_cancel(RID) == {"cancelled": True}
    assert kernel.stopped == 1


# ---- the run

def test_a_run_with_no_lakehouse_has_no_sandbox_and_deletes_nothing(tmp_path):
    svc = Bare(tmp_path, Kernel([OK]))
    rec = run(svc, [cell("x = 1")])
    assert rec["run"]["sandbox_tables"] is None and svc.deleted == [] and svc.kernel.bound is None


def test_a_cancelled_cells_own_time_is_kept(tmp_path):
    svc = Bare(tmp_path, Kernel([{"status": "cancelled", "seconds": 1.5, "outputs": []}]))
    rec = run(svc, [cell("x = 1")])
    assert rec["cells"][0]["seconds"] == 1.5


def test_a_finished_cell_with_no_time_keeps_none(tmp_path):
    svc = Bare(tmp_path, Kernel([{"status": "ok", "seconds": None, "outputs": []}]))
    rec = run(svc, [cell("x = 1")])
    assert rec["cells"][0]["seconds"] is None


def test_the_runs_time_is_the_seconds_between_start_and_end_to_the_millisecond(tmp_path, monkeypatch):
    ticks = iter([100.0])
    monkeypatch.setattr(vs.time, "monotonic", lambda: next(ticks, 101.23456))
    rec = run(Bare(tmp_path, Kernel([OK])), [cell("x = 1")])
    assert rec["run"]["seconds"] == 1.235


def test_a_run_that_took_no_time_says_zero_seconds(tmp_path, monkeypatch):
    monkeypatch.setattr(vs.time, "monotonic", lambda: 50.0)
    rec = run(Bare(tmp_path, Kernel([OK])), [cell("x = 1")])
    assert rec["run"]["seconds"] == 0.0
    assert rec["summary"].startswith("Every code cell ran from the top in a fresh kernel on this PC, in 0.0 s.")


def test_the_summary_counts_the_cells_that_succeeded_before_the_failure(tmp_path):
    err = {"status": "error", "seconds": 0.1, "outputs": [{"type": "error", "ename": "E", "evalue": "", "traceback": ""}]}
    rec = run(Bare(tmp_path, Kernel([OK, OK, err])), [cell("a = 1", 1), cell("b = 2", 2), cell("c", 3)])
    assert rec["summary"].startswith("The clean run stopped at cell 3 after 2 cells succeeded.")


def test_the_environment_lists_what_the_code_cells_import_and_not_the_markdown(tmp_path):
    cells = [cell("import numpy", 1), {"type": "markdown", "source": "import pytest", "cell_id": "m", "meta": {}}]
    svc = Bare(tmp_path, Kernel([OK]))
    rec = record(cells)
    svc._validations()[rec["id"]] = rec
    svc._validate_run(rec, cells, [(1, cells[0])], None)
    assert list(rec["environment"]["packages"]) == ["numpy"]


# ---- the report

def test_the_saved_reports_name_has_the_validations_date_and_time_and_its_json_is_indented(tmp_path, monkeypatch):
    monkeypatch.setattr(known_folders, "downloads", lambda: tmp_path / "Downloads")
    svc = Bare(tmp_path)
    rec = record([cell("x = 1")])
    rec["state"] = "done"
    svc._validations()[RID] = rec
    saved = svc.notebook_validate_save(RID, format="json")
    assert saved["path"].endswith("validate-Orders-20261111-120000.json")
    text = open(saved["path"], encoding="utf-8").read()
    assert text == json.dumps(svc.notebook_validate_status(RID), indent=1)


def test_cleanup_plan_and_apply_return_what_cleanup_gives(tmp_path):
    svc = Bare(tmp_path)
    cells = [cell("import os\nx = 1")]
    plan = svc.notebook_cleanup_plan(cells)
    assert "code" in plan and set(vs.cleanup.plan(cells)) <= set(plan)
    assert svc.notebook_cleanup_apply(cells, []) == vs.cleanup.apply(cells, [])
