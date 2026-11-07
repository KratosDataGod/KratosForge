"""Each local Delta commit is flushed to disk before the write returns (durable.py): its data files,
their folders, the commit file and _delta_log."""

import json
import os
import statistics
import time
from pathlib import Path

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable

from fabric_desktop import durable, maintenance
from fabric_desktop.meta.store import LocalStore


@pytest.fixture
def synced(monkeypatch) -> list[tuple[Path, bool]]:
    calls: list[tuple[Path, bool]] = []
    real = durable._sync

    def record(path: Path, folder: bool = False) -> None:
        calls.append((Path(path), folder))
        real(path, folder)
    monkeypatch.setattr(durable, "_sync", record)
    return calls


def df(ids: range) -> pl.DataFrame:
    return pl.DataFrame({"id": list(ids), "v": [str(i) for i in ids]})


def flushed(calls: list[tuple[Path, bool]], uri: str, version: int) -> None:
    """Every file commit ``version`` added, the table folder, the commit file and _delta_log were flushed."""
    log, added = durable.commit_files(uri, version)
    assert added
    files = {p for p, folder in calls if not folder}
    folders = {p for p, folder in calls if folder}
    assert set(added) | {log} <= files
    assert {Path(uri), log.parent} | {p.parent for p in added} <= folders
    # The commit file goes after the data it names.
    order = [p for p, folder in calls if not folder]
    assert max(order.index(p) for p in added) < order.index(log)


def test_every_kind_of_local_write_is_flushed_before_it_returns(tmp_path, synced):
    s = LocalStore(tmp_path)
    uri = s.uri("lh", "dbo", "t")
    v = s.write("lh", "dbo", "t", df(range(5)))
    flushed(synced, uri, v)
    synced.clear()
    v = s.write("lh", "dbo", "t", df(range(5, 8)), mode="append")
    flushed(synced, uri, v)
    synced.clear()
    v, _ = s.upsert("lh", "dbo", "t", df(range(6, 10)), on=["id"], read_version=v)
    flushed(synced, uri, v)
    synced.clear()
    data = df(range(3)).to_arrow()
    v = s.write_stream("lh", "dbo", "t", pa.RecordBatchReader.from_batches(data.schema, data.to_batches()), read_version=v)
    flushed(synced, uri, v)
    synced.clear()
    s.meta_append("run_log", [{"run_id": "r1", "entity": "e", "status": "ok"}])
    assert any(p.name.endswith(".json") and "run_log" in str(p) for p, _ in synced)
    assert sorted(pl.from_arrow(DeltaTable(uri).to_pyarrow_table())["id"].to_list()) == [0, 1, 2]


def test_a_created_table_and_a_compaction_are_flushed(tmp_path, synced):
    s = LocalStore(tmp_path)
    assert s.create_if_absent("lh", "dbo", "empty", {"id": pl.Int64})
    assert (Path(s.uri("lh", "dbo", "empty")) / "_delta_log" / f"{0:020d}.json", False) in synced
    s2 = LocalStore(tmp_path / "s2")
    for i in range(3):
        s2.write("lh", "dbo", "c", df(range(i, i + 1)), mode="append")
    uri = s2.uri("lh", "dbo", "c")
    before = DeltaTable(uri).version()
    synced.clear()
    maintenance.compact_local(uri)
    after = DeltaTable(uri).version()
    flushed_logs = {p.name for p, folder in synced if not folder and p.suffix == ".json"}
    assert {f"{v:020d}.json" for v in range(before + 1, after + 1)} <= flushed_logs
    optimize = next(v for v in range(before + 1, after + 1) if durable.commit_files(uri, v)[1])
    flushed(synced, uri, optimize)


def test_a_flush_that_fails_never_fails_the_write(tmp_path, monkeypatch, caplog):
    def broken(path, folder=False):
        raise OSError("device not ready")
    monkeypatch.setattr(durable, "_sync", broken)
    s = LocalStore(tmp_path)
    assert s.write("lh", "dbo", "t", df(range(3))) == 0
    assert "Couldn't flush commits 0-0" in caplog.text
    assert DeltaTable(s.uri("lh", "dbo", "t")).version() == 0


def test_only_files_inside_the_table_folder_are_flushed(tmp_path):
    uri = tmp_path / "t"
    (uri / "_delta_log").mkdir(parents=True)
    (uri / "p=1").mkdir()
    (uri / "p=1" / "part-0.parquet").write_bytes(b"x")
    actions = [{"add": {"path": "p%3D1/part-0.parquet"}}, {"add": {"path": "../outside.parquet"}},
               {"add": {"path": str(tmp_path / "abs.parquet")}}, {"add": {"path": "abfss://x@y/z.parquet"}},
               {"commitInfo": {}}]
    (uri / "_delta_log" / f"{0:020d}.json").write_text("\n".join(json.dumps(a) for a in actions))
    log, added = durable.commit_files(str(uri), 0)
    assert added == [uri / "p=1" / "part-0.parquet"]
    assert durable.sync_commits(str(uri), 0, 0)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes in a folder are POSIX only")
def test_a_pipe_under_a_data_file_s_name_never_blocks_the_flush(tmp_path):
    import threading
    uri = tmp_path / "t"
    (uri / "_delta_log").mkdir(parents=True)
    os.mkfifo(uri / "part-0.parquet")
    (uri / "_delta_log" / f"{0:020d}.json").write_text(json.dumps({"add": {"path": "part-0.parquet"}}))
    done = threading.Thread(target=durable.sync_commits, args=(str(uri), 0, 0), daemon=True)
    done.start()
    done.join(10)
    assert not done.is_alive()


def test_the_flush_costs_a_fraction_of_the_write(tmp_path, monkeypatch):
    """What the flush costs next to the write, on this machine. Written to the CI run's summary (on
    each OS), so the cost on Windows is on record; it fails only if the flush costs more than the
    write itself."""
    s = LocalStore(tmp_path)
    rows = df(range(300_000))
    write_ms, flush_ms = [], []
    for i in range(3):
        monkeypatch.setattr(durable, "sync_commits", lambda *a: True)
        t0 = time.perf_counter()
        v = s.write("lh", "dbo", f"t{i}", rows)
        t1 = time.perf_counter()
        monkeypatch.undo()
        assert durable.sync_commits(s.uri("lh", "dbo", f"t{i}"), v, v)
        write_ms.append((t1 - t0) * 1000)
        flush_ms.append((time.perf_counter() - t1) * 1000)
    w, f = statistics.median(write_ms), statistics.median(flush_ms)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(f"\nLocal Delta commit flush ({os.name}): write of 300,000 rows {w:.1f} ms, "
                      f"flush {f:.1f} ms ({f / w:.0%} of the write)\n")
    assert f < w
