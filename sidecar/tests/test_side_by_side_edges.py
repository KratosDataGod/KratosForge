"""Side by side: the edges the end-to-end runs don't reach. A torn or unreadable history file,
refusals before a run starts, Cancel at each stage, a Fabric status that drops or never ends, and
reading OneLake tables (with a stand-in for storage)."""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fabric_desktop import jsonfile
from fabric_desktop import side_by_side as sbs
from fabric_desktop import side_by_side_service as sbss
from fabric_desktop.side_by_side_service import OneLakeTables, SideBySideMixin


class Bare(SideBySideMixin):
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def bare(tmp_path):
    return Bare(tmp_path / "app")


def record(**over: Any) -> dict[str, Any]:
    r = {"id": "r1", "name": "NB", "state": "local", "started_at": "2026-11-07T10:00:00+00:00",
         "finished_at": None, "message": "", "_cancel": False,
         "local": {"status": "pending", "seconds": None, "cell": None, "cells_total": 1, "cells": {}},
         "fabric": {"status": "pending", "engine": "python", "vcores": 4}, "tables": [], "verdict": None, "speed": {}}
    r.update(over)
    return r


# ---- small helpers

def test_parse_reads_utc_and_naive_times_and_refuses_text():
    assert sbss._parse("2026-11-07T10:00:00Z") == dt.datetime(2026, 11, 7, 10, tzinfo=dt.timezone.utc)
    assert sbss._parse("2026-11-07T10:00:00").tzinfo is dt.timezone.utc  # no zone: taken as UTC
    assert sbss._parse("not a time") is None
    assert sbss._parse(None) is None


def test_copy_tries_again_while_the_run_changes_the_record_then_says_busy(monkeypatch):
    real = json.dumps
    tries = []

    def flaky(obj, **kw):
        tries.append(1)
        if len(tries) < 3:
            raise RuntimeError("dictionary changed size during iteration")
        return real(obj, **kw)

    monkeypatch.setattr(sbss.time, "sleep", lambda s: None)
    monkeypatch.setattr(sbss.json, "dumps", flaky)
    assert sbss._copy({"id": "r1", "_kernel": object()}) == {"id": "r1"}
    assert len(tries) == 3

    def always(obj, **kw):
        raise RuntimeError("changed size")

    monkeypatch.setattr(sbss.json, "dumps", always)
    with pytest.raises(RuntimeError, match="busy"):
        sbss._copy({"id": "r1"})


# ---- the saved comparisons

def test_saved_list_is_empty_without_a_file(bare):
    assert bare._sbs_saved() == []


def test_a_file_that_isnt_utf8_is_set_aside_and_started_again(bare):
    bare.side_by_side_file.write_bytes(b"\xff\xfe\x00 not text")
    assert bare._sbs_saved() == []
    assert not bare.side_by_side_file.exists()
    assert len(list(bare.root.glob("side_by_side.jsonl.corrupt-*"))) == 1


def test_a_torn_line_is_dropped_and_the_file_repaired_with_a_copy_kept(bare):
    good = json.dumps({"id": "a", "started_at": "1"})
    bare.side_by_side_file.write_text(f"{good}\n{{\"id\": \"b\", \"sta\n", encoding="utf-8")
    assert bare._sbs_saved() == [{"id": "a", "started_at": "1"}]
    assert bare.side_by_side_file.read_text(encoding="utf-8") == good + "\n"
    copies = list(bare.root.glob("side_by_side.jsonl.corrupt-*"))
    assert len(copies) == 1 and '"b"' in copies[0].read_text(encoding="utf-8")


def test_a_repair_that_cant_write_keeps_the_file_and_isnt_retried_until_it_changes(bare, monkeypatch, caplog):
    path = bare.side_by_side_file
    path.write_text('{"id": "a"}\n{torn\n', encoding="utf-8")
    calls = []

    def refuse(p, *a, **kw):
        calls.append(p)
        raise OSError("disk full")

    monkeypatch.setattr(jsonfile, "set_aside", refuse)
    assert bare._sbs_saved() == [{"id": "a"}]
    assert bare._sbs_saved() == [{"id": "a"}]  # the same torn file: no second try
    assert len(calls) == 1 and "could not be repaired" in caplog.text
    assert path.read_text(encoding="utf-8") == '{"id": "a"}\n{torn\n'  # left in place
    path.write_text('{"id": "a"}\n{"id": "b"}\n{torn again\n', encoding="utf-8")
    assert [r["id"] for r in bare._sbs_saved()] == ["a", "b"]
    assert len(calls) == 2  # it changed: tried again


def test_a_repair_of_a_file_that_went_away_does_nothing(bare):
    bare._sbs_repair([{"id": "a"}])  # nothing to stat
    assert not bare.side_by_side_file.exists()


def test_save_drops_cell_outputs_and_private_fields_and_replaces_the_same_id(bare):
    r = record(_kernel=object())
    r["local"]["cells"] = {"1": {"outputs": ["big"]}}
    bare._sbs_save(r)
    r["state"] = "done"
    bare._sbs_save(r)
    rows = bare._sbs_saved()
    assert len(rows) == 1 and rows[0]["state"] == "done"
    assert "cells" not in rows[0]["local"] and "_kernel" not in rows[0] and "_cancel" not in rows[0]


def test_status_of_an_unknown_run_says_so_and_a_saved_one_is_found(bare):
    with pytest.raises(LookupError, match="No side-by-side run nope"):
        bare.side_by_side_status("nope")
    bare._sbs_save(record(state="done"))
    assert bare.side_by_side_status("r1")["state"] == "done"


def test_list_puts_running_runs_over_saved_ones_and_filters_by_notebook(bare):
    bare._sbs_save(record(id="old", started_at="2026-11-01"))
    bare._sbs_save(record(id="other", name="Other", started_at="2026-11-02"))
    live = record(id="old", state="fabric", started_at="2026-11-01")
    live["local"]["cells"] = {"1": {"outputs": []}}
    bare._sbs_state()["old"] = live
    rows = bare.side_by_side_list()
    assert [r["id"] for r in rows] == ["other", "old"]
    assert rows[1]["state"] == "fabric" and "cells" not in rows[1]["local"]
    assert [r["id"] for r in bare.side_by_side_list(name="Other")] == ["other"]
    assert len(bare.side_by_side_list(limit=1)) == 1


# ---- cores

def test_a_cores_choice_that_cant_be_read_falls_back_to_the_default(bare):
    def missing(name, key):
        raise OSError("not saved yet")

    bare.notebooks = SimpleNamespace(extra=missing)
    assert bare._sbs_cores_choice("NB") == sbs.DEFAULT_CORES


@pytest.mark.parametrize("bad", [True, 0, "half", 10 ** 6])
def test_cores_set_refuses_anything_but_all_match_or_a_count_this_pc_has(bare, bad):
    bare.notebooks = SimpleNamespace(set_extra=lambda *a: pytest.fail("saved a bad choice"))
    with pytest.raises(ValueError, match="Cores must be"):
        bare.side_by_side_cores_set("NB", [], bad)


# ---- refusals before anything runs

class Starter(Bare):
    def __init__(self, root: Path, replay_live: bool = False):
        super().__init__(root)
        self.live = replay_live

    def _replay_refuse(self, name, doing):
        pass

    def _replay_live(self):
        return self.live


LAKEHOUSE = {"workspace_id": "w", "lakehouse_id": "l", "name": "LH"}


def test_start_refuses_while_another_run_is_going(tmp_path):
    s = Starter(tmp_path)
    s._sbs_state()["x"] = record(state="checking")
    with pytest.raises(RuntimeError, match="already going"):
        s.side_by_side_start("NB", [], LAKEHOUSE)


def test_start_refuses_while_a_replay_has_the_kernel(tmp_path):
    with pytest.raises(RuntimeError, match="replay is using the kernel"):
        Starter(tmp_path, replay_live=True).side_by_side_start("NB", [], LAKEHOUSE)


def test_start_needs_a_lakehouse(tmp_path):
    with pytest.raises(ValueError, match="Attach a lakehouse first"):
        Starter(tmp_path).side_by_side_start("NB", [], None)


def test_start_refuses_a_notebook_with_a_fabric_step(tmp_path, monkeypatch):
    monkeypatch.setattr(sbss, "is_step", lambda c: c.get("step") is True)
    cells = [{"type": "code", "source": "x = 1"}, {"type": "code", "source": "", "step": True}]
    with pytest.raises(ValueError, match="Cell 2"):
        Starter(tmp_path).side_by_side_start("NB", cells, LAKEHOUSE)


# ---- Cancel

class FakeKernel:
    def __init__(self):
        self.stopped = self.interrupted = False

    def stop(self):
        self.stopped = True

    def interrupt(self):
        self.interrupted = True


def test_cancel_of_a_finished_or_unknown_run_does_nothing(bare):
    assert bare.side_by_side_cancel("nope") == {"cancelled": False}
    bare._sbs_state()["r1"] = record(state="done")
    assert bare.side_by_side_cancel("r1") == {"cancelled": False}


def test_cancel_ends_a_capped_runs_own_process(bare):
    k = FakeKernel()
    bare._sbs_state()["r1"] = record(_kernel=k)
    assert bare.side_by_side_cancel("r1") == {"cancelled": True}
    assert k.stopped and bare._sbs_state()["r1"]["_cancel"]


def test_cancel_interrupts_the_notebooks_own_kernel(bare):
    k = FakeKernel()
    bare.sessions = SimpleNamespace(kernel=lambda name: k)
    bare._sbs_state()["r1"] = record()
    bare.side_by_side_cancel("r1")
    assert k.interrupted and not k.stopped


def test_cancel_in_fabric_says_when_fabric_refuses(bare):
    def refuse(ws, item, job):
        raise ConnectionError("no route")

    bare.fabric_jobs = SimpleNamespace(cancel_job=refuse)
    r = record(state="fabric")
    r["fabric"].update(job_id="j", item_id="i", workspace_id="w")
    bare._sbs_state()["r1"] = r
    assert bare.side_by_side_cancel("r1") == {"cancelled": True}
    assert r["message"] == "Couldn't cancel the Fabric run: no route"


def test_cancel_in_fabric_before_the_job_started_only_marks_it(bare):
    bare.fabric_jobs = SimpleNamespace(cancel_job=lambda *a: pytest.fail("no job to cancel"))
    bare._sbs_state()["r1"] = record(state="fabric")
    assert bare.side_by_side_cancel("r1") == {"cancelled": True}


# ---- the run's thread

class Tables:
    def versions(self):
        return {}


class Runner(Bare):
    """Runs _sbs_run with the local and Fabric halves replaced, so each stage can be cancelled."""

    def __init__(self, root, cancel_at=None, fail=None):
        super().__init__(root)
        self.cancel_at, self.fail, self.stages = cancel_at, fail, []

    def _sbs_tables(self, ref):
        return Tables()

    def _sbs_local(self, rec, cells, code, ref):
        self.stages.append("local")
        rec["local"]["status"] = "ok"
        if self.cancel_at == "local":
            rec["_cancel"] = True

    def _sbs_fabric(self, rec, cells, ref, compute, ws):
        self.stages.append("fabric")
        if self.fail:
            raise self.fail
        rec["fabric"]["status"] = "failed"
        if self.cancel_at == "fabric":
            rec["_cancel"] = True


@pytest.mark.parametrize("at, stages", [("local", ["local"]), ("fabric", ["local", "fabric"])])
def test_cancel_during_a_stage_stops_the_run_there_and_keeps_it(tmp_path, at, stages):
    s = Runner(tmp_path / "app", cancel_at=at)
    r = record()
    s._sbs_run(r, [], [], LAKEHOUSE, None, {})
    assert s.stages == stages
    assert (r["state"], r["message"]) == ("cancelled", "Cancelled.") and r["finished_at"]
    assert s._sbs_saved()[0]["state"] == "cancelled"


def test_an_error_in_a_stage_is_reported_on_the_panel(tmp_path):
    s = Runner(tmp_path / "app", fail=PermissionError("no access to the workspace"))
    r = record()
    s._sbs_run(r, [], [], LAKEHOUSE, None, {})
    assert r["state"] == "failed" and r["message"] == "PermissionError: no access to the workspace"


def test_one_failed_side_gives_a_verdict_and_no_speed(tmp_path):
    s = Runner(tmp_path / "app")
    r = record()
    s._sbs_run(r, [], [], LAKEHOUSE, None, {})
    assert r["state"] == "done" and r["verdict"] is not None and r["speed"] == {}


def test_the_real_tables_reader_is_the_attached_lakehouse_in_onelake(bare):
    bare._schemas_enabled = lambda ws, lh: (ws, lh) == ("w", "l")
    bare.auth = SimpleNamespace(storage_token=lambda: "t")
    t = bare._sbs_tables(LAKEHOUSE)
    assert isinstance(t, OneLakeTables) and t.schemas and (t.workspace_id, t.lakehouse_id) == ("w", "l")


class CellKernel:
    """Answers start/poll as a kernel does: one 'running' poll, then the finished cell."""

    def __init__(self, finished: dict[str, Any], on_start=None):
        self.finished, self.on_start, self.started = finished, on_start, []
        self.polls = 0

    def start(self, source):
        self.started.append(source)
        self.polls = 0
        if self.on_start:
            self.on_start()

    def poll(self, version, timeout):
        self.polls += 1
        if self.polls == 1:
            return {"status": "running", "seconds": 0.1, "version": 1, "outputs": None}
        if self.polls == 2:
            return {"status": "running", "seconds": 0.2, "version": 2, "outputs": [{"type": "stream", "text": "so far"}]}
        return dict(self.finished, seconds=0.3)


class CellsHost(Bare):
    def _resources_dir(self, name):
        return self.root / "res"

    def _onelake_writes(self, name):
        return None

    def _saved_inputs(self, name):
        return None


def test_cells_keep_onelake_upload_figures_and_the_error_of_a_failed_cell(tmp_path):
    host = CellsHost(tmp_path / "app")
    k = CellKernel({"status": "error", "outputs": [{"type": "error", "ename": "KeyError", "evalue": "'id'"}],
                    "onelake": {"wait_seconds": 0.1, "upload_seconds": 0.2, "tables": 1, "rows": 5, "bytes": 0, "skipped": 0}})
    r = record()
    r["_suffixes"] = ["x = 1"]
    cells = [{"source": "print(1)"}, {"source": "print(2)"}]
    host._sbs_cells(r, k, cells, [0, 1])
    local = r["local"]
    assert k.started == ["print(1)\nx = 1"]  # the Parameters bar's line, then it stopped at the failure
    assert local["status"] == "failed" and local["failed_cell"] == 1 and local["message"] == "KeyError: 'id'"
    assert local["cells"]["0"]["onelake"]["rows"] == 5 and local["upload"]["mb_per_second"] is None


def test_a_cancel_before_the_next_cell_stops_there(tmp_path):
    host = CellsHost(tmp_path / "app")
    r = record()
    k = CellKernel({"status": "ok", "outputs": []}, on_start=lambda: r.update(_cancel=True))
    host._sbs_cells(r, k, [{"source": "a"}, {"source": "b"}], [0, 1])
    assert k.started == ["a"] and r["local"]["status"] == "cancelled"


def test_a_cell_stopped_by_cancel_counts_as_cancelled_not_failed(tmp_path):
    host = CellsHost(tmp_path / "app")
    r = record()
    k = CellKernel({"status": "error", "outputs": []}, on_start=lambda: r.update(_cancel=True))
    host._sbs_cells(r, k, [{"source": "a"}], [0])
    assert r["local"]["status"] == "cancelled" and "message" not in r["local"]


# ---- the Fabric half

class FabricHost(Bare):
    def __init__(self, root, job_instance):
        super().__init__(root)
        self.fabric = SimpleNamespace(job_instance=job_instance)
        self.fabric_jobs = SimpleNamespace(run_item_job=lambda *a: "job1")
        self.auth = SimpleNamespace(storage_token=lambda: "t")

    def _publish(self, *a, **kw):
        return {"workspace_id": "w", "id": "nb", "url": "u", "workspace_name": "WS"}

    def _log_fabric_run(self, entry):
        pass

    def _cost_settings(self):
        return {"python_cu": 1, "spark_cu": 2, "price_per_cu_hour": 1, "currency": "USD"}


class NoTiming:
    def __init__(self, token):
        pass

    def read_bytes(self, *a):
        raise FileNotFoundError("timing file")


def test_a_dropped_status_check_is_asked_again_and_a_missing_timing_file_loses_only_the_split(tmp_path, monkeypatch):
    monkeypatch.setattr(sbss, "POLL_SECONDS", 0)
    monkeypatch.setattr(sbss, "FileStore", NoTiming)
    answers = iter([ConnectionError("reset"), {"status": "Failed", "failureReason": "out of memory",
                                               "startTimeUtc": "2026-11-07T10:00:00Z", "endTimeUtc": "2026-11-07T10:00:30Z"}])

    def job_instance(ws, item, job):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    host = FabricHost(tmp_path / "app", job_instance)
    r = record()
    host._sbs_fabric(r, [], LAKEHOUSE, None, {})
    f = r["fabric"]
    assert f["status"] == "failed" and f["message"] == "out of memory" and f["job_status"] == "Failed"
    assert f["run_seconds"] is None and "start_seconds" not in f
    assert f["job_seconds"] == 30 and f["cu_seconds"] == 30.0 and f["currency"] == "USD"


def test_a_fabric_run_past_the_time_limit_stops_waiting_as_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(sbss, "POLL_SECONDS", 0)
    monkeypatch.setattr(sbss, "MAX_FABRIC_SECONDS", 0.05)
    monkeypatch.setattr(sbss, "FileStore", NoTiming)
    host = FabricHost(tmp_path / "app", lambda *a: {"status": "InProgress"})
    r = record()
    host._sbs_fabric(r, [], LAKEHOUSE, None, {})
    f = r["fabric"]
    assert f["status"] == "failed" and f["job_status"] == "InProgress"
    assert f["job_seconds"] is None and "cost" not in f


# ---- OneLake tables

class FakeStore:
    def __init__(self, tree: dict[str, list[dict[str, Any]]]):
        self.tree = tree

    def list(self, ws, lh, path):
        return self.tree[path]


def entry(name, path=None, is_dir=True):
    return {"name": name, "path": path or name, "is_dir": is_dir}


def test_onelake_table_names_with_and_without_schemas(monkeypatch):
    plain = OneLakeTables("w", "l", False, lambda: "t")
    plain.files = FakeStore({"Tables": [entry("sales"), entry("readme.txt", is_dir=False)]})
    assert plain._names() == ["dbo.sales"]
    assert plain._uri("dbo.sales").endswith("/l/Tables/sales") and plain._uri("dbo.sales").startswith("abfss://w@")

    schemas = OneLakeTables("w", "l", True, lambda: "t")
    schemas.files = FakeStore({"Tables": [entry("bronze", "Tables/bronze"), entry("gold", "Tables/gold")],
                               "Tables/bronze": [entry("raw")], "Tables/gold": [entry("fact"), entry("x", is_dir=False)]})
    assert schemas._names() == ["bronze.raw", "gold.fact"]
    assert schemas._uri("gold.fact").endswith("/l/Tables/gold/fact")


def test_onelake_versions_skip_folders_that_arent_delta_tables(monkeypatch):
    opened: list[tuple[str, Any, dict]] = []
    lock = threading.Lock()

    class FakeDelta:
        def __init__(self, uri, version=None, storage_options=None):
            with lock:
                opened.append((uri, version, storage_options))
            if uri.endswith("/notdelta"):
                raise OSError("no _delta_log")
            self.uri = uri

        def version(self):
            return 3

        def history(self, limit):
            return [{"version": 3, "limit": limit}]

        def to_pyarrow_dataset(self):
            return "dataset"

    monkeypatch.setattr(sbss, "DeltaTable", FakeDelta)
    monkeypatch.setattr(sbss, "storage_options", lambda token: {"token": token})
    t = OneLakeTables("w", "l", False, lambda: "tok")
    t.files = FakeStore({"Tables": [entry("sales"), entry("notdelta")]})
    assert t.versions() == {"dbo.sales": 3}
    assert t.history("dbo.sales") == [{"version": 3, "limit": 200}]
    assert t.dataset("dbo.sales", 2) == "dataset"
    assert any(u.endswith("/l/Tables/sales") and v == 2 and o == {"token": "tok"} for u, v, o in opened)
