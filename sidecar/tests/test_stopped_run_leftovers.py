"""PB-067: the files a run left in its table when its process ended mid-write (out of memory, the app
closed), found by the next run of the table as a dry run. Only files proven to be that run's count:
Delta's own names, written since it started, named by no version of the table still on disk, and none
at all once another commit followed its start. Nothing is deleted."""

import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable, write_deltalake
from test_large_tables import DAY1, DAY2, Setup, rows
from test_out_of_memory import ended_pid

from fabric_desktop import maintenance
from fabric_desktop.leases import Leases, LocalBackend, holding, table_key
from fabric_desktop.meta.engine import Engine
from fabric_desktop.meta.project import Project

TESTS = Path(__file__).parent


def _part() -> str:
    return f"part-00000-{uuid.uuid4()}-c000.snappy.parquet"


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def _table(tmp_path: Path) -> tuple[str, Path]:
    """A table at version 2 whose first version's file only time travel reads, all written a minute ago."""
    uri = str(tmp_path / "t")
    write_deltalake(uri, pl.DataFrame({"id": [1, 2, 3]}).to_arrow())
    write_deltalake(uri, pl.DataFrame({"id": [4]}).to_arrow(), mode="overwrite")
    write_deltalake(uri, pl.DataFrame({"id": [5]}).to_arrow(), mode="append")
    root = Path(uri)
    for p in root.rglob("*"):
        if p.is_file():
            _age(p, 60)
    return uri, root


def _plant(root: Path, name: str, like: Path | None = None, at: float | None = None) -> Path:
    """A file written at ``at`` (default now)."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if like is not None:
        shutil.copy(like, path)
    else:
        path.write_bytes(b"x" * 10)
    if at is not None:
        os.utime(path, (at, at))
    return path


def test_only_files_proven_to_be_the_stopped_runs_count_and_none_is_deleted(tmp_path):
    uri, root = _table(tmp_path)
    committed = sorted(root.glob("*.parquet"))
    named_before = {p.name for p in committed}
    since = time.time()  # the stopped run started after the table's last commit
    during = since + 1  # when it wrote its files
    left = {
        "orphan": _plant(root, _part(), committed[0], during),
        "staged": _plant(root, _part() + "#1", committed[0], during),
        "partition": _plant(root, f"region=EU/{_part()}", committed[0], during),
        "staged_commit": _plant(root, "_delta_log/00000000000000000003.json#1", at=during),
    }
    not_its = {
        "before_it_started": _plant(root, _part(), committed[0], since - 1),
        "not_a_delta_name": _plant(root, "notes.parquet", at=during),
        "a_dropped_csv": _plant(root, "export.csv", at=during),
        "kept_out_folder": _plant(root, f"_change_data/{_part()}", at=during),
        "dot_folder": _plant(root, f".tmp/{_part()}", at=during),
    }
    linked = tmp_path / "elsewhere"
    _plant(linked, _part(), at=during)
    (root / "linked").symlink_to(linked, target_is_directory=True)

    found = maintenance.stopped_run_leftovers(uri, since)

    assert found["refused"] is None
    assert found["files"] == sorted(p.relative_to(root).as_posix() for p in left.values())
    assert found["bytes"] == sum(p.stat().st_size for p in left.values())
    # A dry run: every file is still there, and the table still reads at its last version.
    assert all(p.exists() for p in [*left.values(), *not_its.values(), *committed])
    assert DeltaTable(uri).version() == 2 and named_before <= {p.name for p in root.glob("*.parquet")}


def test_a_file_only_an_older_version_names_is_never_counted(tmp_path):
    uri, root = _table(tmp_path)
    first = json.loads(next(line for line in (root / "_delta_log" / "00000000000000000000.json").read_text()
                            .splitlines() if '"add"' in line))["add"]["path"]
    since = time.time()
    os.utime(root / first, (since + 1, since + 1))  # touched after the run started (a sync client, a scan)
    found = maintenance.stopped_run_leftovers(uri, since)
    assert first not in found["files"] and found["files"] == []
    assert DeltaTable(uri, version=0).to_pyarrow_table().num_rows == 3  # time travel still reads it


def test_a_commit_after_the_run_started_means_nothing_counts(tmp_path):
    uri, root = _table(tmp_path)
    since = time.time()
    _plant(root, _part(), next(root.glob("*.parquet")), since + 1)
    write_deltalake(uri, pl.DataFrame({"id": [6]}).to_arrow(), mode="append")  # another writer was at work
    found = maintenance.stopped_run_leftovers(uri, since)
    assert found["files"] == [] and "a commit was made" in found["refused"]


def test_a_first_load_that_stopped_leaves_files_no_log_names(tmp_path):
    root = tmp_path / "t"
    since = time.time()
    planted = [_plant(root, _part(), at=since + 1), _plant(root, _part() + "#2", at=since + 1)]
    found = maintenance.stopped_run_leftovers(str(root), since)
    assert found["refused"] is None and found["files"] == sorted(p.name for p in planted)
    assert maintenance.stopped_run_leftovers(str(tmp_path / "none"), since) == {"files": [], "bytes": 0, "refused": None}


def test_a_writer_killed_mid_stream_leaves_exactly_the_files_counted(tmp_path):
    """A real delta-rs write ended by its process exiting part way, as running out of memory ends it."""
    uri, root = _table(tmp_path)
    before = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    since = time.time()
    script = textwrap.dedent(f"""
        import os
        import pyarrow as pa
        from deltalake import write_deltalake
        schema = pa.schema([("id", pa.int64())])
        def batches():
            for i in range(6):
                yield pa.record_batch([pa.array(range(i * 500_000, (i + 1) * 500_000))], schema=schema)
            os._exit(3)
        write_deltalake({uri!r}, pa.RecordBatchReader.from_batches(schema, batches()), mode="overwrite",
                        target_file_size=1_000_000)
    """)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300)
    assert done.returncode == 3, done.stderr
    after = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    written = after - before
    assert written, "delta-rs wrote no file before it was stopped"
    assert DeltaTable(uri).version() == 2  # nothing committed

    found = maintenance.stopped_run_leftovers(uri, since)
    assert found["refused"] is None and found["files"] == sorted(written)
    assert after == {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}  # dry run


# ---- the next run of the table


EVENTS = ("LH_Bronze", "dbo", "events")


def _lock(s: Setup):
    leases = Leases(LocalBackend(s.store.root / "_leases"), owner="test", ttl=120, wait=5)

    @contextmanager
    def table_lock(e):
        with leases.hold(table_key(*EVENTS)):
            assert holding(table_key(*EVENTS))
            yield
    return table_lock


def _stopped_note(s: Setup, started: dt.datetime) -> str:
    run_id = "r-20261111-010000-" + uuid.uuid4().hex[:12]
    folder = s.store.root / "_running"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{run_id}.json").write_text(json.dumps({
        "run_id": run_id, "entity": "bronze.events", "started_at": started.isoformat(timespec="microseconds"),
        "pid": ended_pid(), "pc": socket.gethostname(), "load_date": DAY2.isoformat(), "backfill": "", "batch_id": ""}))
    return run_id


def _after_a_stopped_run(tmp_path: Path) -> tuple[Setup, str, list[Path]]:
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run()
    root = Path(s.store.uri(*EVENTS))
    for p in root.rglob("*"):
        if p.is_file():
            _age(p, 60)
    started = dt.datetime.now(dt.timezone.utc)  # after the first run's commit
    run_id = _stopped_note(s, started)
    like, during = next(root.glob("*.parquet")), started.timestamp() + 1
    left = [_plant(root, _part(), like, during), _plant(root, _part() + "#1", like, during)]
    s.source(rows([10, 11], DAY2))
    return s, run_id, left


def _cleanup(res) -> list:
    return [st for st in res.steps if st.name == "clean up"]


def test_the_next_run_counts_what_the_stopped_run_left_and_removes_nothing(tmp_path):
    s, run_id, left = _after_a_stopped_run(tmp_path)
    res = Engine(Project(s.root), s.store, DAY2, merge_writes=True, table_lock=_lock(s)).run("bronze.events")
    assert res.status == "ok", res.message
    [step] = _cleanup(res)
    assert step.rows == 2 and step.detail.startswith(f"dry run: run {run_id} stopped mid-write and left 2 files (")
    assert step.detail.endswith("that no version of the table names; nothing was removed")
    assert all(p.exists() for p in left)
    assert any(st.name == "record" and run_id in st.detail for st in res.steps)  # still recorded as before


def test_without_the_tables_lease_nothing_is_looked_for(tmp_path):
    s, run_id, left = _after_a_stopped_run(tmp_path)
    res = s.run(day=DAY2)  # an engine with no table lock: no proof no other write is under way
    assert _cleanup(res) == [] and all(p.exists() for p in left)
    assert any(st.name == "record" and run_id in st.detail for st in res.steps)


def test_a_commit_after_the_stopped_run_started_is_reported_and_nothing_counted(tmp_path):
    s, run_id, left = _after_a_stopped_run(tmp_path)
    write_deltalake(s.store.uri(*EVENTS), DeltaTable(s.store.uri(*EVENTS)).to_pyarrow_table().slice(0, 0),
                    mode="append")
    res = Engine(Project(s.root), s.store, DAY2, merge_writes=True, table_lock=_lock(s)).run("bronze.events")
    [step] = _cleanup(res)
    assert step.rows == 0 and "a commit was made to the table after the stopped run started" in step.detail
    assert all(p.exists() for p in left)


@pytest.mark.parametrize("broken", ["not a time", None])
def test_a_note_it_cant_read_a_start_from_only_costs_the_dry_run(tmp_path, broken):
    s, run_id, left = _after_a_stopped_run(tmp_path)
    note = s.store.root / "_running" / f"{run_id}.json"
    body = json.loads(note.read_text())
    body["started_at"] = broken
    note.write_text(json.dumps(body))
    res = Engine(Project(s.root), s.store, DAY2, merge_writes=True, table_lock=_lock(s)).run("bronze.events")
    assert res.status == "ok"
    [step] = _cleanup(res)
    assert step.rows == 0 and step.detail.startswith(f"couldn't look for the files run {run_id} left")
    assert all(p.exists() for p in left)


def test_holding_is_true_only_inside_the_lease(tmp_path):
    leases = Leases(LocalBackend(tmp_path), owner="test", ttl=120, wait=1)
    key = table_key(*EVENTS)
    assert not holding(key)
    with leases.hold(key):
        assert holding(key) and not holding(table_key("LH_Bronze", "dbo", "other"))
    assert not holding(key)


def test_a_linked_table_folder_or_log_is_never_looked_at(tmp_path):
    uri, root = _table(tmp_path)
    since = time.time()
    _plant(root, _part(), next(root.glob("*.parquet")), since + 1)
    link = tmp_path / "via_link"
    link.symlink_to(root, target_is_directory=True)
    found = maintenance.stopped_run_leftovers(str(link), since)
    assert found["files"] == [] and "is a link" in found["refused"]
    real_log = tmp_path / "real_log"
    shutil.move(str(root / "_delta_log"), real_log)
    (root / "_delta_log").symlink_to(real_log, target_is_directory=True)
    found = maintenance.stopped_run_leftovers(uri, since)
    assert found["files"] == [] and "is a link" in found["refused"]


@pytest.mark.parametrize("path", ["abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Tables/t/part-0.parquet",
                                  "file:///C:/data/t/part-0.parquet", "/data/t/part-0.parquet",
                                  "C:/data/t/part-0.parquet"])
def test_a_log_that_names_a_file_by_its_full_path_means_nothing_counts(tmp_path, path):
    uri, root = _table(tmp_path)
    since = time.time()
    _plant(root, _part(), next(root.glob("*.parquet")), since + 1)
    commit = root / "_delta_log" / "00000000000000000001.json"
    commit.write_text(commit.read_text().rstrip("\n") + "\n" + json.dumps({"remove": {"path": path, "dataChange": True}}) + "\n")
    os.utime(commit, (since - 30, since - 30))
    found = maintenance.stopped_run_leftovers(uri, since)
    assert found["files"] == [] and "full path" in found["refused"]


def test_a_commit_with_no_time_means_nothing_counts(tmp_path, monkeypatch):
    uri, root = _table(tmp_path)
    since = time.time()
    _plant(root, _part(), next(root.glob("*.parquet")), since + 1)
    real = DeltaTable.history
    monkeypatch.setattr(DeltaTable, "history", lambda self, n=None: [{k: v for k, v in c.items() if k != "timestamp"}
                                                                    for c in real(self, n)])
    found = maintenance.stopped_run_leftovers(uri, since)
    assert found["files"] == [] and "a commit was made" in found["refused"]
