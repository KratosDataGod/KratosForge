"""Background OneLake writes for notebook cells on this PC.

A table write is mostly upload: on a home connection OneLake takes the bytes at the line's speed,
however many streams carry them. So while a cell runs, ``lakehouse.write_table`` hands each write
to this queue and returns: the cell's code goes on computing while earlier writes encode and
upload, and different tables upload side by side. One table's writes still commit in the order
the cell made them. The kernel waits for the queue at the end of every cell (``flush``), so a
cell is only done when its writes are in OneLake, and a failed write fails the cell that made it.

A write the queue holds is either the cell's own Arrow table (in memory) or, for a stream of
batches, a spill file on this PC's disk, so a table bigger than memory never has to fit in it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .redact import describe

if TYPE_CHECKING:  # pyarrow is imported where it's used: every cell makes a Queue, few write
    import pyarrow as pa

_log = logging.getLogger(__name__)

# The write queue of the cell running on this thread: notebook.py sets it for each cell and waits
# for it when the cell ends. Here, not in lakehouse, so a cell that never writes doesn't import it.
# Unset, as in the service, scripts and threads a cell starts itself, write_table writes to
# OneLake before it returns.
cell_writes = threading.local()

# Writes uploading at once: the line is the limit, so a few are enough to keep it busy while
# another table encodes or commits.
WORKERS = 3


def _memory_cap() -> int:
    """How many bytes of in-memory tables may wait for upload before write_table waits too."""
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        total = 0
    if not total:
        try:
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                            ("virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                            ("ext", ctypes.c_ulonglong)]
            status = _Status()
            status.length = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            total = int(status.total)
        except (AttributeError, OSError):
            total = 0
    return max(total // 4, 1 << 30) if total else 4 << 30


# ---- what the rows are, so an unchanged rewrite can skip the upload


def fingerprint_part(table: pa.Table) -> tuple[int, int, int]:
    """(rows, sum, xor) of a hash of every row: in any order, and parts of a stream add up."""
    import duckdb
    if table.num_rows == 0:
        return 0, 0, 0
    con = duckdb.connect()
    try:
        con.register("t", table)
        rows, total, xor = con.execute(
            "SELECT count(*), coalesce(sum(hash(r)::HUGEINT), 0), coalesce(bit_xor(hash(r)), 0) "
            "FROM (SELECT t AS r FROM t)").fetchone()
    finally:
        con.close()
    return int(rows), int(total), int(xor)


def combine(parts: Iterable[tuple[int, int, int]]) -> tuple[int, int, int]:
    rows = total = xor = 0
    for r, s, x in parts:
        rows, total, xor = rows + r, total + s, xor ^ x
    return rows, total, xor


def fingerprint(schema: pa.Schema, parts: tuple[int, int, int]) -> str:
    """The rows and the columns they're in, as one string kept in the Delta commit."""
    rows, total, xor = parts
    cols = ",".join(f"{f.name}:{f.type}" for f in schema)
    return hashlib.sha256(f"{cols}|{rows}|{total}|{xor}".encode()).hexdigest()[:32]


# ---- a stream of batches, kept on disk until it uploads


class Spill:
    """A stream's batches in an Arrow IPC file on this PC: written at disk speed, read back as a stream."""

    def __init__(self, directory: Path, schema: pa.Schema):
        directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="write-", suffix=".arrow", dir=directory)
        os.close(fd)
        self.path = Path(name)
        self.schema = schema
        self.rows = 0
        import pyarrow as pa
        self._sink = pa.OSFile(str(self.path), "wb")
        self._writer = pa.ipc.new_stream(self._sink, schema)
        self._mapped: Any = None

    def add(self, batch: pa.RecordBatch) -> None:
        self._writer.write_batch(batch)
        self.rows += batch.num_rows

    def close(self) -> None:
        self._writer.close()
        self._sink.close()

    def reader(self) -> pa.RecordBatchReader:
        import pyarrow as pa
        self._mapped = pa.memory_map(str(self.path))
        return pa.ipc.open_stream(self._mapped)

    def remove(self) -> None:
        if self._mapped is not None:
            self._mapped.close()  # Windows won't delete a mapped file
            self._mapped = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:  # in use (Windows) or gone wrong: the retention sweep removes it a day later
            _log.info("Couldn't remove a write's spill file: %s", describe(exc))


# ---- the queue


class WriteFailed(RuntimeError):
    pass


class Queue:
    def __init__(self, workers: int = WORKERS, memory_cap: int | None = None):
        self.workers = workers
        self.memory_cap = _memory_cap() if memory_cap is None else memory_cap
        self._lock = threading.Condition()
        self._last: dict[str, Future] = {}  # each table's newest write: the next one starts after it
        self._pending: list[tuple[str, Future]] = []
        self._held = 0  # bytes of in-memory tables waiting
        self._running = 0
        self._ready: list[Callable[[], None]] = []
        self._threads: list[threading.Thread] = []

    def submit(self, key: str, label: str, nbytes: int, work: Callable[[], Any]) -> Future:
        """Run ``work`` after the table's earlier writes; waits first while too much is held in memory."""
        done: Future = Future()
        with self._lock:
            while nbytes and self._held and self._held + nbytes > self.memory_cap:
                self._lock.wait(1.0)
            self._held += nbytes
            before = self._last.get(key)
            self._last[key] = done
            self._pending.append((label, done))

        def run() -> None:
            if not done.set_running_or_notify_cancel():
                return self._release(nbytes)
            try:
                if before is not None and before.exception() is not None:
                    raise WriteFailed(f"{label} wasn't written because an earlier write to the same table failed.")
                done.set_result(work())
            except BaseException as exc:  # noqa: BLE001 - handed to the cell that made the write, at flush
                done.set_exception(exc)
            finally:
                self._release(nbytes)

        if before is None or before.done():
            self._start(run)
        else:
            before.add_done_callback(lambda _f: self._start(run))
        return done

    def _release(self, nbytes: int) -> None:
        with self._lock:
            self._held -= nbytes
            self._lock.notify_all()

    def _start(self, run: Callable[[], None]) -> None:
        with self._lock:
            self._ready.append(run)
            self._threads = [t for t in self._threads if t.is_alive()]
            if len(self._threads) < self.workers:
                t = threading.Thread(target=self._work, daemon=True, name="onelake-write")
                self._threads.append(t)
                t.start()
            self._lock.notify_all()

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._ready:
                    self._lock.wait(5.0)
                    if not self._ready:
                        return
                run = self._ready.pop(0)
            run()

    def wait(self) -> None:
        """Wait for every write so far, without raising: reads call this to see the cell's own writes."""
        with self._lock:
            pending = [f for _, f in self._pending]
        for f in pending:
            try:
                f.exception()
            except BaseException:  # noqa: BLE001  # silent-ok: cancelled, so there is nothing to wait for
                pass

    def flush(self, cancel: bool = False) -> list[str]:
        """Wait for every write, then forget them: what failed, one line each (empty when all went in).
        ``cancel`` drops the writes that haven't started (the cell was cancelled)."""
        with self._lock:
            pending, self._pending = self._pending, []
            self._last.clear()
        if cancel:
            for _, f in pending:
                f.cancel()
        failed = []
        for label, f in pending:
            if f.cancelled():
                failed.append(f"{label}: not written (the cell was cancelled)")
                continue
            exc = f.exception()
            if exc is not None:
                failed.append(f"{label}: {type(exc).__name__}: {exc}")
        return failed

    def busy(self) -> bool:
        with self._lock:
            return any(not f.done() for _, f in self._pending)
