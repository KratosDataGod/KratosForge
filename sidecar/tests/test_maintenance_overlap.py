"""Maintenance on one local table from two processes at once: the Maintain page's Optimize (compact_local), a
notebook's OPTIMIZE, ZORDER BY and RESTORE (lakehouse.maintain_table, run in a kernel's own process), the app's
RESTORE (meta/restore.py) and a metadata run's write that cleans up after itself (LocalStore.write_stream).

Each test makes the overlap happen at the worst moment: a hook in this process, at a point the code passes
between reading the table and committing (or deleting), starts the other process and waits for it to finish.
Before the table's compaction lock (maintenance.compaction_lock), the other process ran to the end inside that
window, and the table lost or doubled rows. With the lock, the other process waits for this one, which the hook
sees as the other process not finishing within a few seconds; it then carries on. That wait is the only
timing here, and the outcome doesn't depend on it: whichever way it goes, the table must stay whole.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError

from fabric_desktop import lakehouse, maintenance
from fabric_desktop.leases import LeaseBusy
from fabric_desktop.meta.store import LocalStore
from tests.test_spark_writer import lake  # noqa: F401 - a fixture
from tests.test_vacuum_hardening import _age, _history
from tests.test_zorder_hardening import allowed, events, finish, wait_for  # noqa: F401 - allowed is a fixture

SIDECAR = Path(__file__).resolve().parents[1]
BATCH = 50
MEANWHILE = 8.0  # how long the hook gives the other process: it takes about a second when nothing stops it


def child(script: str, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(script), *args], cwd=SIDECAR,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def meanwhile(p: subprocess.Popen[str], started: Path) -> None:
    """Let ``p`` run until it ends, or until MEANWHILE seconds after it says it started (then it is waiting for
    this process, which carries on)."""
    wait_for(started.exists, "the other process to start")
    end = time.monotonic() + MEANWHILE
    while p.poll() is None and time.monotonic() < end:
        time.sleep(0.02)


HOLDER = """
    import sys, time
    from pathlib import Path
    from fabric_desktop import maintenance
    with maintenance.compaction_lock(sys.argv[1]):
        Path(sys.argv[2]).touch()
        end = time.monotonic() + 120
        while not Path(sys.argv[3]).exists() and time.monotonic() < end:
            time.sleep(0.02)
"""


@contextmanager
def held_elsewhere(uri: str, tmp_path: Path) -> Iterator[None]:
    """Another process holds ``uri``'s compaction lock for the block, as a compaction there would."""
    held, release = tmp_path / "held", tmp_path / "release"
    p = child(HOLDER, uri, str(held), str(release))
    try:
        wait_for(held.exists, "the other process to take the lock")
        yield
    finally:
        release.touch()
        p.wait(60)


def whole(uri: str) -> list[int]:
    """The latest version's ids, after checking every file it names is on disk."""
    t = DeltaTable(uri)
    missing = [u for u in t.file_uris() if not Path(u.removeprefix("file://")).exists()]
    assert missing == [], missing
    return sorted(t.to_pyarrow_table(columns=["id"]).column("id").to_pylist())


# The Maintain page's Optimize: compaction, VACUUM and the leftover cleanup, as compact_local runs them.
MAINTAIN_PAGE = """
    import json, os, sys
    from pathlib import Path
    from fabric_desktop import maintenance
    uri, started = sys.argv[1], Path(sys.argv[2])
    started.touch()
    try:
        out = {"ok": maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)}
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"[:500]}
    print(json.dumps(out), flush=True)
    os._exit(0)
"""

# The Maintain page's Optimize of a LocalStore table, under the store's compaction lease as CloudMixin._maintain runs it.
MAINTAIN_STORE = """
    import json, os, sys
    from pathlib import Path
    from fabric_desktop import maintenance
    from fabric_desktop.meta.store import LocalStore
    root, started = Path(sys.argv[1]), Path(sys.argv[2])
    store = LocalStore(root, "dev")
    started.touch()
    try:
        with store.compaction_lease("LH_Bronze", "dbo", "events", wait=120):
            out = {"ok": maintenance.compact_local(store.uri("LH_Bronze", "dbo", "events"), 7)}
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"[:500]}
    print(json.dumps(out), flush=True)
    os._exit(0)
"""

# A notebook kernel's OPTIMIZE (or ZORDER BY) of dbo.events, attached as tests.test_spark_writer's lake fixture attaches.
NOTEBOOK = """
    import json, os, sys
    from pathlib import Path
    from fabric_desktop import lakehouse, onelake, publish
    root, started, zorder = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == "zorder"
    onelake.TableRef.uri = property(lambda self: (root / "Tables" / self.schema / self.table).as_posix())
    publish.storage_options = lakehouse.storage_options = lambda token: {}
    lakehouse.maintenance_guard = lambda op, ws, t: None
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", root))
    started.touch()
    try:
        r = lakehouse.maintain_table("dbo.events", "optimize", zorder=["amount", "id"] if zorder else None)
        out = {"ok": r.metrics.get("numFilesRemoved")}
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"[:500]}
    print(json.dumps(out), flush=True)
    os._exit(0)
"""

# The app's RESTORE (the table history's Restore, meta/restore.py).
RESTORE = """
    import json, os, sys
    from pathlib import Path
    from fabric_desktop.meta import restore
    uri, started, version = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
    started.touch()
    try:
        out = {"ok": restore.restore(uri, None, version)}
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"[:500]}
    print(json.dumps(out), flush=True)
    os._exit(0)
"""


def _stale_optimize(monkeypatch, script: str, *args: str) -> list[subprocess.Popen[str]]:
    """While the notebook's OPTIMIZE has read the table and is about to plan its rewrite (its keep_log step),
    ``script`` runs in another process."""
    real, started, procs = maintenance.keep_log, Path(args[1]), []

    def keep_log(*a, **k):
        if not procs:
            procs.append(child(script, *args))
            meanwhile(procs[0], started)
        return real(*a, **k)
    monkeypatch.setattr(maintenance, "keep_log", keep_log)
    return procs


def test_a_notebook_optimize_while_the_maintain_page_optimizes_in_another_process_doubles_no_row(
        allowed, tmp_path, monkeypatch):  # noqa: F811
    """delta-rs 1.6.6 moves an OPTIMIZE onto the latest version before it commits without checking the files it
    rewrote are still there: the second of two compactions of one table committed every row again. (Compacted once
    already, the table has its retention set, so the Maintain page's Optimize changes no property: one it changed
    would make the notebook's commit fail as a conflict instead.)"""
    uri = events(allowed, 12)
    maintenance.set_retention(uri, maintenance.MIN_RETENTION_DAYS)
    base = whole(uri)
    procs = _stale_optimize(monkeypatch, MAINTAIN_PAGE, uri, str(tmp_path / "started"))
    lakehouse.maintain_table("dbo.events", "optimize")
    other = finish(procs[0])
    assert "ok" in other, other
    assert whole(uri) == base


@pytest.mark.parametrize("zorder", ["optimize", "zorder"])
def test_two_notebook_kernels_optimizing_one_table_at_once_double_no_row(allowed, tmp_path, monkeypatch,
                                                                        zorder):  # noqa: F811
    uri = events(allowed, 12)
    base = whole(uri)
    procs = _stale_optimize(monkeypatch, NOTEBOOK, str(tmp_path), str(tmp_path / "started"), zorder)
    lakehouse.maintain_table("dbo.events", "optimize", zorder=["id"])
    other = finish(procs[0])
    assert "ok" in other, other
    assert whole(uri) == base


def test_a_failed_runs_cleanup_never_deletes_a_file_a_compaction_in_another_process_commits(tmp_path, monkeypatch):
    """LocalStore.write_stream removes the files a write that failed left: the files that weren't there before
    it, that the version it read doesn't name. A compaction holds its own lease, not the table's write lease, so
    its new file, committed just after the version was checked, looked like one of them and was deleted: the
    table then named a file that wasn't there and couldn't be read at all."""
    store = LocalStore(tmp_path, "dev")
    for b in range(10):
        store.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": list(range(b * BATCH, (b + 1) * BATCH)),
                                                                "amount": [0.0] * BATCH}), mode="append")
    uri = store.uri("LH_Bronze", "dbo", "events")
    base, read_version = whole(uri), DeltaTable(uri).version()
    real, started, procs = LocalStore._data_files, tmp_path / "started", []

    def data_files(self, *a):
        if len(procs) == 0 and getattr(self, "_listed", False):  # the cleanup's listing, after the version check
            procs.append(child(MAINTAIN_STORE, str(tmp_path), str(started)))
            meanwhile(procs[0], started)
        self._listed = True
        return real(self, *a)
    monkeypatch.setattr(LocalStore, "_data_files", data_files)
    schema = pa.schema([("id", pa.int64()), ("amount", pa.float64())])

    def batches():
        yield pa.record_batch([pa.array(range(10_000, 10_000 + BATCH)), pa.array([1.0] * BATCH)], schema=schema)
        raise ConnectionError("the source dropped the connection")
    with pytest.raises(Exception, match="dropped the connection"):
        store.write_stream("LH_Bronze", "dbo", "events", pa.RecordBatchReader.from_batches(schema, batches()),
                           read_version=read_version)
    other = finish(procs[0])
    assert "ok" in other and other["ok"]["files_removed"] == 10, other
    assert whole(uri) == base


def test_the_cleanup_removes_its_own_files_when_no_compaction_runs(tmp_path):
    store = LocalStore(tmp_path, "dev")
    store.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [1], "amount": [0.0]}), mode="append")
    uri = store.uri("LH_Bronze", "dbo", "events")
    files = set(Path(uri).glob("*.parquet"))
    schema = pa.schema([("id", pa.int64()), ("amount", pa.float64())])

    def batches():
        for i in range(3):
            yield pa.record_batch([pa.array(range(i * 100_000, i * 100_000 + 50_000)), pa.array([1.0] * 50_000)],
                                  schema=schema)
        raise ConnectionError("the source dropped the connection")
    with pytest.raises(Exception, match="dropped the connection"):
        store.write_stream("LH_Bronze", "dbo", "events", pa.RecordBatchReader.from_batches(schema, batches()),
                           read_version=DeltaTable(uri).version())
    assert set(Path(uri).glob("*.parquet")) == files and store.last_cleanup["note"].startswith("removed")
    assert whole(uri) == [1]


def test_while_a_compaction_holds_the_table_a_failed_runs_cleanup_removes_nothing(tmp_path):
    store = LocalStore(tmp_path, "dev")
    store.write("LH_Bronze", "dbo", "events", pl.DataFrame({"id": [1], "amount": [0.0]}), mode="append")
    uri = store.uri("LH_Bronze", "dbo", "events")
    left = Path(uri) / "part-00000-00000000-0000-0000-0000-000000000000-c000.snappy.parquet"
    before = store._data_files("LH_Bronze", "dbo", "events")
    left.write_bytes(next(Path(uri).glob("part-*.parquet")).read_bytes())  # a compaction's file, not committed yet
    with held_elsewhere(uri, tmp_path):
        out = store._remove_unwritten("LH_Bronze", "dbo", "events", before, DeltaTable(uri).version())
    assert out == {"removed": 0, "note": "the table was being compacted meanwhile, so the files this write left "
                                         "are left for VACUUM"}
    assert left.exists()


def test_a_restore_in_another_process_while_compact_cleans_leftovers_leaves_the_table_whole(tmp_path, monkeypatch):
    """Versions 0 and 1 are past the 7 days kept, and a checkpoint has dropped their removals, so their files are
    leftovers no commit names: Compact's cleanup deletes them. A RESTORE to version 1 (allowed while its files are
    there) committed just before that cleanup made the table's latest version name a file then deleted."""
    uri = str(tmp_path / "t")
    named = _history(uri)
    for f in Path(uri).glob("*.parquet"):
        _age(f, 30 * 24)
    DeltaTable(uri).create_checkpoint()
    real, started, procs = maintenance._unnamed, tmp_path / "started", []

    def unnamed(*a, **k):
        if not procs:
            procs.append(child(RESTORE, uri, str(started), "1"))
            meanwhile(procs[0], started)
        return real(*a, **k)
    monkeypatch.setattr(maintenance, "_unnamed", unnamed)
    out = maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
    other = finish(procs[0])
    assert out["unnamed_removed"] == 2 and not (Path(uri) / named[1]).exists(), out
    assert "error" in other, other  # its files were gone by the time it ran: refused, nothing changed
    assert whole(uri) == [3]


def test_while_another_process_compacts_a_notebook_restore_waits_then_changes_nothing(allowed, tmp_path,
                                                                                      monkeypatch):  # noqa: F811
    uri = events(allowed, 3)
    monkeypatch.setattr(maintenance, "COMPACTION_WAIT", 0.5)
    with held_elsewhere(uri, tmp_path), pytest.raises(LeaseBusy) as caught:
        lakehouse.maintain_table("dbo.events", "restore", version=0)
    assert str(caught.value) == (
        "dbo.events is being optimized, vacuumed or restored by another process on this PC (the Maintain page's "
        "Optimize, a notebook or a scheduled run), so nothing was changed. Run it again once that has finished.")
    assert whole(uri) == list(range(3 * BATCH)) and DeltaTable(uri).version() == 2
    assert lakehouse.maintain_table("dbo.events", "restore", version=0).delta_version == 3  # once it has finished


def test_threads_of_one_process_take_turns_too(tmp_path):
    """On Windows a file lock doesn't keep threads of one process apart; the lock's thread lock does."""
    import threading
    uri = str(tmp_path / "t")
    results: list[str] = []

    def other():
        try:
            with maintenance.compaction_lock(uri, wait=0):
                results.append("in")
        except LeaseBusy:
            results.append("busy")
    with maintenance.compaction_lock(uri):
        t = threading.Thread(target=other)
        t.start()
        t.join(30)
    other()
    assert results == ["busy", "in"]


def test_the_lock_is_let_go_when_its_process_is_killed(tmp_path):
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": [1]}))
    held = tmp_path / "held"
    p = child("""
        import sys, time
        from pathlib import Path
        from fabric_desktop import maintenance
        with maintenance.compaction_lock(sys.argv[1]):
            Path(sys.argv[2]).touch()
            time.sleep(300)
    """, uri, str(held))
    try:
        wait_for(held.exists, "the other process to take the lock")
        with pytest.raises(LeaseBusy):
            with maintenance.compaction_lock(uri, wait=0):
                pass
    finally:
        p.kill()
        p.wait(30)
    with maintenance.compaction_lock(uri, wait=5):
        pass
    assert maintenance.compact_local(uri, 7)["files_before"] == 1


# ---- a property commit (keep_log, set_retention) while a metadata run holds what it read

def test_a_pinned_overwrite_after_keep_logs_property_commit_fails_cleanly_and_goes_through_once_read_again(tmp_path):
    """Maintenance's keep_log commits a property change when the log needs keeping longer. A metadata run's
    read-merge-overwrite pinned to the version before it is refused with delta-rs's CommitFailedError, the table
    as it was (the engine then reads and merges again: Engine._write, tests/test_meta_engine_retries.py); read
    again, it goes through with every row."""
    store = LocalStore(tmp_path, "dev")
    store.write("LH_Silver", "dbo", "orders", pl.DataFrame({"id": [1, 2]}), mode="overwrite")
    uri = store.uri("LH_Silver", "dbo", "orders")
    df, version = store.read_at("LH_Silver", "dbo", "orders")
    assert maintenance.keep_log(uri, maintenance.retention_hours(7), now=time.time() + 40 * 86400) is True
    with pytest.raises(CommitFailedError):
        store.write("LH_Silver", "dbo", "orders", pl.concat([df, pl.DataFrame({"id": [3]})]), read_version=version)
    assert whole(uri) == [1, 2]
    df, version = store.read_at("LH_Silver", "dbo", "orders")
    store.write("LH_Silver", "dbo", "orders", pl.concat([df, pl.DataFrame({"id": [3]})]), read_version=version)
    assert whole(uri) == [1, 2, 3]
    assert "interval 60 days" == DeltaTable(uri).metadata().configuration["delta.logRetentionDuration"]


def test_a_pinned_stream_after_a_property_commit_fails_cleanly_and_leaves_the_files_for_vacuum(tmp_path):
    store = LocalStore(tmp_path, "dev")
    store.write("LH_Silver", "dbo", "orders", pl.DataFrame({"id": [1, 2]}), mode="overwrite")
    uri = store.uri("LH_Silver", "dbo", "orders")
    version = DeltaTable(uri).version()
    maintenance.set_retention(uri, 14)
    rows = pa.table({"id": pa.array([1, 2, 3], pa.int64())})
    with pytest.raises(CommitFailedError):
        store.write_stream("LH_Silver", "dbo", "orders", rows.to_reader(), read_version=version)
    assert store.last_cleanup["note"].startswith("another writer committed")
    assert whole(uri) == [1, 2]
