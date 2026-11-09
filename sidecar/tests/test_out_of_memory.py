"""A run that runs this PC out of memory stops cleanly: nothing is half-written, and it says so.

Measured (local-performance/out-of-memory): when an allocation fails, Rust (Polars, delta-rs)
aborts the whole engine, and an OS out of memory kills it. Either way no exception reaches the run,
the table keeps its last version, and before this the run left no trace in the run log. A run now
leaves a note while it is under way, and the next run of the table records a run whose process
ended as failed. A MemoryError the run does see gets a plain message.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import polars as pl
import pyarrow as pa
import pytest
from test_large_tables import DAY1, DAY2, Setup, rows
from test_write_failures import ids

from fabric_desktop.meta import engine as engine_mod

TESTS = Path(__file__).parent


def log(s: Setup) -> list[dict]:
    return s.store.meta_where("run_log", pl.col("entity") == "bronze.events").to_dicts()


def running(s: Setup) -> list[Path]:
    folder = s.store.root / "_running"
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def ended_pid() -> int:
    """The PID of a process that has ended."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_a_run_that_ends_its_process_mid_run_is_recorded_as_failed_by_the_next_one(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run()
    s.source(rows([10, 11], DAY2))
    # The run's process ends during the write, as running out of memory ends it: no exception, no finally.
    script = textwrap.dedent(f"""
        import os, sys
        sys.path[:0] = [{str(TESTS.parent)!r}, {str(TESTS)!r}]
        import test_large_tables as t
        t.init_project = t.entity = lambda *a, **k: None
        s = t.Setup(__import__("pathlib").Path({str(tmp_path)!r}), True)
        for name in ("write", "write_stream", "merge_insert", "upsert"):
            setattr(s.store, name, lambda *a, **k: os._exit(3))
        s.engine(t.DAY2).run("bronze.events")
    """)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300)
    assert done.returncode == 3, done.stderr
    assert len(running(s)) == 1 and ids(s) == [0, 1, 2]  # nothing written; the note is all that is left
    stopped = json.loads(running(s)[0].read_text())
    assert [r["status"] for r in log(s)] == ["ok"]  # before: the run was never seen again

    res = s.run(day=DAY2)
    assert ids(s) == [0, 1, 2, 10, 11] and running(s) == []
    assert any(st.name == "record" and stopped["run_id"] in st.detail for st in res.steps)
    failed = [r for r in log(s) if r["run_id"] == stopped["run_id"]]
    assert len(failed) == 1 and failed[0]["status"] == "failed"
    assert failed[0]["started_at"] == stopped["started_at"] and failed[0]["delta_version"] is None
    assert failed[0]["message"].startswith("Stopped: the engine ended during this run, so nothing was written")
    assert "ran out of memory" in failed[0]["message"] and "batch_by" in failed[0]["message"]
    s.run(day=DAY2)
    assert len([r for r in log(s) if r["run_id"] == stopped["run_id"]]) == 1  # recorded once


def plant(s: Setup, run_id: str, pid: int, entity: str = "bronze.events") -> Path:
    folder = s.store.root / "_running"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{run_id}.json"
    path.write_text(json.dumps({"run_id": run_id, "entity": entity, "started_at": "2026-11-09T18:00:00+00:00",
                                "pid": pid, "load_date": DAY2.isoformat(), "backfill": "", "batch_id": ""}))
    return path


def test_a_run_whose_process_is_still_running_or_another_tables_is_left_alone(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    live = plant(s, "r-20261109-180000-aaaaaaaaaaaa", os.getpid())
    other = plant(s, "r-20261109-180000-bbbbbbbbbbbb", ended_pid(), entity="bronze.amounts")
    s.run()
    assert live.exists() and other.exists() and [r["status"] for r in log(s)] == ["ok"]


def test_a_stopped_run_that_had_committed_is_left_to_the_unlogged_record(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    res = s.run()
    note = plant(s, res.run_id, ended_pid())  # it committed and was logged: nothing more to record
    s.run(day=DAY2)
    assert not note.exists() and [r["status"] for r in log(s)] == ["ok", "ok"]


def test_a_finished_run_leaves_no_note_even_when_it_fails(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run()
    s.store.write = lambda *a, **k: (_ for _ in ()).throw(ValueError("bad"))
    s.store.merge_insert = s.store.upsert = s.store.write_stream = s.store.write
    s.source(rows([10], DAY2))
    assert s.engine(DAY2).run("bronze.events").status == "failed"
    assert running(s) == []


@pytest.mark.parametrize("exc", [MemoryError(), pa.ArrowMemoryError("malloc of size 1048576 failed"),
                                 RuntimeError("Out of Memory Error: could not allocate block of size 256 KiB")])
def test_running_out_of_memory_in_the_run_says_so_and_changes_nothing(tmp_path, exc):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run()
    s.source(rows([10, 11], DAY2))
    real = {n: getattr(s.store, n) for n in ("write", "write_stream", "merge_insert", "upsert")}

    def fail(*a, **k):
        raise exc
    for n in real:
        setattr(s.store, n, fail)
    res = s.engine(DAY2).run("bronze.events")
    assert res.status == "failed" and ids(s) == [0, 1, 2]
    assert res.message.startswith("This PC ran out of memory during the run. The table was not changed. Close")
    assert any(st.name == "error" for st in res.steps)  # the raw error is kept
    for n, f in real.items():
        setattr(s.store, n, f)
    assert s.run(day=DAY2).status == "ok" and ids(s) == [0, 1, 2, 10, 11]


def test_what_counts_as_out_of_memory():
    assert engine_mod._out_of_memory(MemoryError())
    assert engine_mod._out_of_memory(pa.ArrowMemoryError("x"))
    try:
        try:
            raise MemoryError
        except MemoryError as inner:
            raise RuntimeError("the transform failed") from inner
    except RuntimeError as outer:
        assert engine_mod._out_of_memory(outer)
    # DuckDB's full temp drive is a disk, not memory (disk_full.py)
    assert not engine_mod._out_of_memory(RuntimeError(
        "Out of Memory Error: failed to offload data block of size 256 KiB (max_temp_directory_size)"))
    assert not engine_mod._out_of_memory(ValueError("memory_guard must be true or false"))
