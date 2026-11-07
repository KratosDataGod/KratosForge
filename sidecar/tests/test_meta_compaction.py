"""The meta tables on this PC stay a bounded number of files however many runs append to them.

Each run appends one small file to the run log and the watermarks; reading them for the last good
run and the watermark cost a little more per file, so a soak's 2,000th run of an entity spent over
a second there. Every META_COMPACT_EVERY-th commit now compacts the table: same rows, same order,
every version still readable."""

from __future__ import annotations

import polars as pl
import pytest
from deltalake import DeltaTable
from deltalake.table import TableOptimizer

from fabric_desktop.meta import store as store_mod
from fabric_desktop.meta.store import META_LAKEHOUSE, LocalStore


def _append(store: LocalStore, i: int) -> None:
    store.meta_append("watermarks", [{"entity": "bronze.events", "value": str(i), "run_id": f"r{i}"}])


def _files(store: LocalStore, table: str) -> int:
    return len(DeltaTable(store.uri(META_LAKEHOUSE, "meta", table)).file_uris())


def test_meta_table_files_stay_bounded_and_rows_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "META_COMPACT_EVERY", 10)
    store = LocalStore(tmp_path)
    for i in range(45):
        _append(store, i)
    # Compacted at versions 10, 20, 30 and 40: one compacted file plus the appends since.
    assert _files(store, "watermarks") <= 10
    w = store.meta_where("watermarks", pl.col("entity") == "bronze.events")
    assert w["value"].to_list() == [str(i) for i in range(45)]
    assert w["run_id"].to_list() == [f"r{i}" for i in range(45)]
    # Nothing is vacuumed: an old version still reads as it was.
    old = DeltaTable(store.uri(META_LAKEHOUSE, "meta", "watermarks"), version=5).to_pyarrow_table()
    assert sorted(old.column("value").to_pylist(), key=int) == [str(i) for i in range(6)]


def test_without_compaction_every_append_is_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "META_COMPACT_EVERY", 10**9)
    store = LocalStore(tmp_path)
    for i in range(25):
        _append(store, i)
    assert _files(store, "watermarks") == 25  # what the bound above is measured against


def test_a_compaction_that_fails_leaves_the_append(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(store_mod, "META_COMPACT_EVERY", 3)
    store = LocalStore(tmp_path)

    def refuse(self, *a, **kw):
        raise OSError("disk busy")

    monkeypatch.setattr(TableOptimizer, "compact", refuse)
    for i in range(5):
        _append(store, i)
    assert store.meta("watermarks")["value"].to_list() == [str(i) for i in range(5)]
    assert "Couldn't compact the watermarks table" in caplog.text


@pytest.mark.parametrize("version", [0, 1, 99, 101])
def test_only_every_nth_commit_compacts(tmp_path, monkeypatch, version):
    calls = []
    monkeypatch.setattr(TableOptimizer, "compact", lambda self, *a, **kw: calls.append(1))
    LocalStore(tmp_path)._tidy_meta("watermarks", version)
    assert calls == []
