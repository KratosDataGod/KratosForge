"""The background OneLake write queue: how much memory it may hold (on POSIX, on Windows, and when
neither can say), a spill file Windows won't let go of, and waiting on, flushing and checking writes
that were cancelled, failed or are still going."""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

import pyarrow as pa
import pytest

from fabric_desktop import write_queue as wq


def no_sysconf(monkeypatch):
    def refuse(name):
        raise ValueError(name)
    monkeypatch.setattr(wq.os, "sysconf", refuse, raising=False)


def test_memory_cap_is_a_quarter_of_memory_at_least_one_gb(monkeypatch):
    sizes = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 16 * (1 << 30) // 4096}
    monkeypatch.setattr(wq.os, "sysconf", lambda n: sizes[n], raising=False)
    assert wq._memory_cap() == 4 << 30
    sizes["SC_PHYS_PAGES"] = (1 << 30) // 4096  # 1 GB of memory: never less than 1 GB
    assert wq._memory_cap() == 1 << 30


def test_memory_cap_asks_windows_when_sysconf_cant_say(monkeypatch):
    import ctypes
    no_sysconf(monkeypatch)

    class Kernel32:
        def GlobalMemoryStatusEx(self, ref):  # noqa: N802 - the Windows name
            ref._obj.total = 32 << 30
            return 1

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=Kernel32()), raising=False)
    assert wq._memory_cap() == 8 << 30


def test_memory_cap_defaults_to_four_gb_when_nothing_can_say(monkeypatch):
    import ctypes
    no_sysconf(monkeypatch)
    monkeypatch.delattr(ctypes, "windll", raising=False)
    assert wq._memory_cap() == 4 << 30


def test_a_spill_file_that_cant_be_removed_is_left_for_the_sweep(tmp_path, monkeypatch, caplog):
    spill = wq.Spill(tmp_path / "spill", pa.schema([("id", pa.int64())]))
    spill.add(pa.record_batch([pa.array([1, 2])], names=["id"]))
    spill.close()
    assert spill.reader().read_all().num_rows == 2
    assert spill.reader().read_all().num_rows == 2  # read again for a retry: the old map is closed first
    real = type(spill.path).unlink

    def in_use(self, *a, **kw):
        if self == spill.path:
            raise PermissionError("in use by another process")
        return real(self, *a, **kw)

    monkeypatch.setattr(type(spill.path), "unlink", in_use)
    with caplog.at_level(logging.INFO, logger=wq.__name__):
        spill.remove()
    assert spill.path.exists() and "Couldn't remove a write's spill file" in caplog.text


def test_wait_returns_for_cancelled_and_failed_writes_and_busy_says_when_one_is_going(monkeypatch):
    q = wq.Queue(workers=1, memory_cap=1 << 30)
    gate = threading.Event()
    q.submit("t1", "first", 0, lambda: gate.wait(5))
    queued = q.submit("t1", "second", 0, lambda: "never")  # waits behind the first: same table
    q.submit("t2", "broken", 0, lambda: (_ for _ in ()).throw(OSError("disk full")))
    assert q.busy()
    assert queued.cancel()
    gate.set()
    q.wait()  # no raise, for the cancelled one or the failed one
    assert not q.busy()
    failed = q.flush()
    assert failed == ["second: not written (the cell was cancelled)", "broken: OSError: disk full"]
    assert q.flush() == [] and not q.busy()


def test_flush_with_cancel_drops_writes_that_havent_started():
    q = wq.Queue(workers=1, memory_cap=1 << 30)
    gate = threading.Event()
    q.submit("t", "running", 0, lambda: gate.wait(5))
    q.submit("t", "waiting", 0, lambda: pytest.fail("a cancelled write ran"))
    threading.Timer(0.05, gate.set).start()
    assert q.flush(cancel=True) == ["waiting: not written (the cell was cancelled)"]


def test_a_write_that_went_in_with_a_warning_leaves_it_for_the_cell():
    q = wq.Queue(workers=2, memory_cap=1 << 30)
    q.submit("t", "ok", 0, lambda: SimpleNamespace(warning="lease lapsed after the commit"))
    q.submit("u", "plain", 0, lambda: None)
    assert q.flush() == [] and q.warnings == ["lease lapsed after the commit"]
