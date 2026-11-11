"""The SQL page worker (sql_worker.py): the memory watch that stops a run past its share, an empty batch in
the result, and how the engine's pipe is moved off stdin. Run in this process with the OS calls stood in
for, so no real file descriptor, thread or exit is touched. Nothing reaches OneLake."""

from __future__ import annotations

import json
import os
import queue
import sys
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from fabric_desktop import kernel_proc, lakehouse, sql_worker
from tests.test_sql_worker_inprocess import job


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(lakehouse, "_attached", lakehouse._attached)
    monkeypatch.setattr(lakehouse, "open_table", lakehouse.open_table)
    monkeypatch.setattr(sql_worker, "_replies", queue.Queue())
    monkeypatch.setattr(sql_worker, "_asked", {})


def said(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_a_run_with_a_memory_cap_starts_the_watch_before_duckdb_opens(tmp_path, capsys, monkeypatch):
    held = []
    monkeypatch.setattr(sql_worker, "_hold_to", lambda cap, j: held.append((cap, j["memory_bound"])))
    sql_worker.run(job(tmp_path, "SELECT 1 AS n", memory_cap_mb="1536", memory_bound="free"))
    assert held == [(1536, "free")]
    assert said(capsys)[-1]["rows"] == 1


def test_an_empty_batch_is_not_written_or_counted(tmp_path, capsys, monkeypatch):
    from fabric_desktop import duckdb_rows
    schema = pa.schema([("n", pa.int64())])

    class Reader:
        def __init__(self):
            self.schema = schema

        def __iter__(self):
            yield pa.record_batch([pa.array([], pa.int64())], schema=schema)
            yield pa.record_batch([pa.array([7, 8], pa.int64())], schema=schema)

    monkeypatch.setattr(duckdb_rows, "arrow_reader", lambda result, rows: Reader())
    sql_worker.run(job(tmp_path, "SELECT 1 AS n"))
    done = said(capsys)[-1]
    assert done["rows"] == 2 and done["batches"] == [2]
    assert ipc.open_file(str(tmp_path / "out.arrow")).read_all().column("n").to_pylist() == [7, 8]


class Exited(Exception):
    pass


@pytest.fixture
def watch(monkeypatch):
    """Run the memory watch in this thread, with each reading from ``used`` and the exit raising."""
    def start(used, cap_mb, **job_fields):
        readings = iter(used)
        sleeps = []

        class Thread:
            def __init__(self, target, daemon, name):
                assert daemon and name == "memory-watch"
                self.target = target

            def start(self):
                self.target()

        def exit_(code):
            raise Exited(code)

        monkeypatch.setattr(kernel_proc, "memory_used", lambda pid: next(readings))
        monkeypatch.setattr(sql_worker, "threading", SimpleNamespace(Thread=Thread))
        monkeypatch.setattr(sql_worker, "time", SimpleNamespace(sleep=sleeps.append))
        monkeypatch.setattr(sql_worker.os, "_exit", exit_)
        with pytest.raises(Exited) as ended:
            sql_worker._hold_to(cap_mb, job_fields)
        return ended.value.args[0], sleeps
    return start


def test_the_watch_waits_out_unknown_and_in_share_readings_and_stops_past_the_share(watch, capsys):
    code, sleeps = watch([None, 512 << 20, 1024 << 20, (1024 << 20) + 1], 1024)
    assert code == 3
    assert sleeps == [sql_worker.MEMORY_WATCH_SECONDS] * 4
    [error] = said(capsys)
    assert error["event"] == "error" and error["kind"] == "memory"
    assert error["message"].startswith("Stopped: the query needed more than 1.0 GB of memory; ")
    assert "memory share set in Settings › SQL" in error["message"]


def test_a_cap_set_by_free_memory_says_what_the_pc_had_free(watch, capsys):
    watch([3 << 30], 2048, memory_bound="free", free_mb=6144)
    message = said(capsys)[0]["message"]
    assert "more than 2.0 GB" in message
    assert "this PC had 6.0 GB free when it started, and the query may use 2.0 GB of it" in message
    assert "Close other apps" in message


def test_free_memory_that_was_not_reported_reads_as_none_free(watch, capsys):
    watch([3 << 30], 2048, memory_bound="free", free_mb=None)
    assert "this PC had 0.0 GB free" in said(capsys)[0]["message"]


class FakeOS:
    """The os calls _take_stdin makes, recorded instead of done."""
    devnull = os.devnull
    O_RDONLY = os.O_RDONLY

    def __init__(self):
        self.calls = []

    def dup(self, fd):
        self.calls.append(("dup", fd))
        return 41

    def fdopen(self, fd, mode, encoding):
        self.calls.append(("fdopen", fd, mode, encoding))
        return "engine pipe"

    def open(self, path, flags):
        self.calls.append(("open", path, flags))
        return 42

    def dup2(self, fd, to):
        self.calls.append(("dup2", fd, to))

    def close(self, fd):
        self.calls.append(("close", fd))


@pytest.fixture
def stdin_kept(monkeypatch):
    """Whatever stdin the test sets back to pytest's own afterwards."""
    monkeypatch.setattr(sys, "stdin", sys.stdin)


def test_the_engines_pipe_is_moved_off_stdin_and_stdin_becomes_the_null_device(monkeypatch, stdin_kept):
    fake = FakeOS()
    monkeypatch.setattr(sql_worker, "os", fake)
    monkeypatch.setattr(sql_worker.sys, "platform", "linux")
    engine = sql_worker._take_stdin()
    assert engine == "engine pipe"
    assert fake.calls == [("dup", 0), ("fdopen", 41, "r", "utf-8"), ("open", os.devnull, os.O_RDONLY),
                          ("dup2", 42, 0), ("close", 42)]
    assert sys.stdin.name == os.devnull and sys.stdin.read() == ""
    sys.stdin.close()


def test_on_windows_the_process_stdin_handle_is_pointed_at_the_null_device_too(monkeypatch, stdin_kept):
    import ctypes
    set_handles = []
    kernel32 = SimpleNamespace(SetStdHandle=lambda which, handle: set_handles.append((which, handle)))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: f"handle-{fd}"))
    monkeypatch.setattr(sql_worker, "os", FakeOS())
    monkeypatch.setattr(sql_worker.sys, "platform", "win32")
    assert sql_worker._take_stdin() == "engine pipe"
    assert set_handles == [(-10, "handle-0")]  # STD_INPUT_HANDLE
    sys.stdin.close()



def test_temp_files_that_cannot_grow_are_reported_as_a_full_temp_drive(tmp_path, monkeypatch):
    from fabric_desktop import disk_full
    monkeypatch.setattr(disk_full, "free_space", lambda path: f"free on {path}")
    exc = RuntimeError(f"Out of Memory Error: {disk_full._SPILL} (spill)")
    kind, message = sql_worker.error_message(exc, {"temp_dir": "T:/spill"})
    assert kind == "disk"
    assert message.startswith("Stopped: the query ran out of room for its temp files") and "(free on T:/spill)" in message


def test_a_full_disk_while_writing_the_result_says_nothing_was_saved(monkeypatch):
    import errno

    from fabric_desktop import disk_full
    monkeypatch.setattr(disk_full, "free_space", lambda path: "")
    kind, message = sql_worker.error_message(OSError(errno.ENOSPC, "No space left on device"), {"out": "o.arrow"})
    assert (kind, message) == ("disk", "The disk is full. Nothing was saved. Free some space and run it again.")
