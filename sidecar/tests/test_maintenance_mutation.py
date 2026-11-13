"""maintenance.py: behavior that mutation testing (docs/quality/mutation-testing.md, batch 2) showed no
test pinned. The thresholds and defaults the advice and VACUUM rest on, health()'s answer field by field
(on a stand-in Delta table, so each history shape can be made exactly), retention in hours and days,
VACUUM being a dry run in the preview and a real one in Compact, a folder entry that vanishes or is a
reparse point, the one-hour age of a stale staged file, and the refusals of stopped_run_leftovers."""

from __future__ import annotations

import os
import stat
import time

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import TableNotFoundError

from fabric_desktop import durable
from fabric_desktop import maintenance as M


def test_the_thresholds_and_defaults_maintenance_rests_on():
    assert M.SMALL_FILE_BYTES == 32 * 1024 * 1024
    assert M.SMALL_FILE_COUNT == 20
    assert M.VERSIONS_BEFORE_VACUUM == 30
    assert M.RETENTION_HOURS == 7 * 24 + 1  # Fabric's 7 days, and an hour of margin
    assert M.MIN_RETENTION_DAYS == 7  # Delta's default
    assert M.LOG_RETENTION_DAYS == 30  # Delta's default
    assert M.STALE_STAGED_HOURS == 1
    assert M.FOOTERS_CHECKED == 200
    assert M._UNIT_HOURS == {"hour": 1, "day": 24, "week": 168}


def test_a_commit_time_in_milliseconds_reads_as_utc_to_the_second():
    assert M._when(1_700_000_000_999) == "2023-11-14T22:13:20+00:00"
    assert M._when(None) is None


class Table:
    """A stand-in for deltalake's DeltaTable: health() reads only these."""

    def __init__(self, sizes, history, version, config=None):
        self.sizes, self._history, self._version, self.config = sizes, history, version, config or {}

    def get_add_actions(self, flatten=True):
        return pa.table({"size_bytes": pa.array(self.sizes, pa.int64())})

    def history(self, limit=None):
        return self._history[:limit]

    def version(self):
        return self._version

    def metadata(self):
        return type("Meta", (), {"configuration": self.config})()


@pytest.fixture
def stand_in(monkeypatch):
    def use(table):
        monkeypatch.setattr(M, "DeltaTable", lambda uri, storage_options=None: table)
        monkeypatch.setattr(M, "leftovers", lambda uri, table: [])  # no folder to walk
    return use


FABRIC = {"engineInfo": "Apache-Spark/3.5 Delta-Lake/3.2", "clientVersion": "Apache-Spark/3.5"}


def test_a_table_that_isnt_there_is_missing(monkeypatch):
    def missing(uri, storage_options=None):
        raise TableNotFoundError("no table")
    monkeypatch.setattr(M, "DeltaTable", missing)
    assert M.health("x") == {"exists": False, "advice": [], "status": "missing"}


def test_health_of_a_table_with_no_files(stand_in):
    stand_in(Table([], [{"operation": "CREATE TABLE", "timestamp": 1000, "version": 0}], 0))
    h = M.health("x", local=False)
    assert h["exists"] is True
    assert (h["files"], h["bytes"], h["avg_file_bytes"]) == (0, 0, 0)
    assert h["last_write"] == M._when(1000)
    assert h["last_optimize"] is None and h["last_vacuum"] is None
    assert h["versions_since_vacuum"] == 1


def test_an_optimize_with_v_order_at_the_same_time_as_the_last_write_counts(stand_in):
    history = [{"operation": "OPTIMIZE", "timestamp": 5000, "version": 3, **FABRIC},
               {"operation": "WRITE", "timestamp": 5000, "version": 2},
               {"operation": "WRITE", "timestamp": 4000, "version": 1}]
    stand_in(Table([10], history, 3))
    h = M.health("x")
    assert h["v_ordered"] is True
    assert h["last_optimize"] == M._when(5000)
    assert not any("V-Order" in a for a in h["advice"])


def test_a_missing_commit_time_counts_as_the_earliest(stand_in):
    optimize = {"operation": "OPTIMIZE", "version": 2, **FABRIC}
    stand_in(Table([10], [optimize, {"operation": "WRITE", "version": 1}], 2))
    assert M.health("x")["v_ordered"] is True
    stand_in(Table([10], [optimize, {"operation": "WRITE", "timestamp": 1, "version": 1}], 2))
    assert M.health("x")["v_ordered"] is False


def test_health_reads_the_newest_200_commits(stand_in):
    writes = [{"operation": "WRITE", "timestamp": 9, "version": 300 - i} for i in range(200)]
    stand_in(Table([10], [*writes, {"operation": "OPTIMIZE", "timestamp": 1, "version": 100, **FABRIC}], 300))
    assert M.health("x")["last_optimize"] is None


def test_versions_since_the_last_vacuum(stand_in):
    history = [{"operation": "WRITE", "timestamp": 9, "version": 9},
               {"operation": "VACUUM END", "timestamp": 5, "version": 5}]
    stand_in(Table([10], history, 9))
    h = M.health("x", local=True)
    assert h["versions_since_vacuum"] == 4 and h["last_vacuum"] == M._when(5)
    # A VACUUM entry that doesn't say its version counts as no VACUUM.
    stand_in(Table([10], [{"operation": "VACUUM END", "timestamp": 5}], 9))
    assert M.health("x", local=True)["versions_since_vacuum"] == 10


def test_thirty_versions_since_vacuum_is_advice(stand_in):
    stand_in(Table([10], [{"operation": "WRITE", "timestamp": 1, "version": 29}], 29))
    h = M.health("x", local=True)
    assert h["advice"] == ["30 versions since the last VACUUM: old files still take up storage"]
    stand_in(Table([10], [{"operation": "WRITE", "timestamp": 1, "version": 28}], 28))
    assert M.health("x", local=True)["advice"] == []


@pytest.mark.parametrize("n, size, advised", [
    (20, 1, True),
    (19, 1, False),
    (20, 32 * 1024 * 1024, False),       # exactly the threshold isn't small
    (20, 32 * 1024 * 1024 - 1, True),
])
def test_twenty_files_under_32_mb_on_average_are_worth_compacting(stand_in, n, size, advised):
    stand_in(Table([size] * n, [{"operation": "WRITE", "timestamp": 1, "version": 0}], 0))
    h = M.health("x", local=True)
    assert (f"{n} small files: compacting them speeds up every read" in h["advice"]) is advised
    assert h["status"] == ("needs_maintenance" if advised else "healthy")


@pytest.mark.parametrize("left, config, text", [
    (["a"], {}, "1 file (0.0 MB)"),
    (["a", "b"], {}, "2 files (0.0 MB)"),
])
def test_leftovers_advice_counts_files_and_the_days_kept(stand_in, monkeypatch, left, config, text):
    stand_in(Table([10], [{"operation": "WRITE", "timestamp": 1, "version": 0}], 0, config))
    monkeypatch.setattr(M, "leftovers", lambda uri, table: left)
    h = M.health("x", local=True)
    assert h["leftover_files"] == len(left)
    assert h["advice"][0].startswith(f"{text} left by writes that stopped part way")
    assert h["advice"][0].endswith("older than the 7 days of history the table keeps")


def test_retention_in_hours_and_its_minimum():
    assert M.retention_hours(7) == 169
    assert M.retention_hours(0) == 1
    with pytest.raises(ValueError, match="at least 7 days of history, not 6"):
        M.check_retention(6)
    M.check_retention(6, allow_shorter=True)
    M.check_retention(7)


@pytest.mark.parametrize("value, days", [
    ("interval 30 days", 30), ("interval 36 hours", 2), ("interval 24 hours", 1), ("interval 2 weeks", 14),
    ("INTERVAL 1 DAY", 1), ("", None), ("forever", None),
])
def test_the_history_a_table_keeps_in_whole_days(value, days):
    assert M.kept_days({"delta.deletedFileRetentionDuration": value}) == days
    assert M.kept_days(None) is None


@pytest.fixture
def vacuums(monkeypatch):
    calls = []
    real = DeltaTable.vacuum

    def record(self, *args, **kwargs):
        calls.append(kwargs)
        return real(self, *args, **kwargs)
    monkeypatch.setattr(DeltaTable, "vacuum", record)
    return calls


def test_the_vacuum_preview_deletes_nothing_and_gives_paths_in_the_table(tmp_path, vacuums, monkeypatch):
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": [1]}))
    assert M.vacuum_preview(uri, 7) == []
    assert vacuums == [{"retention_hours": 169, "dry_run": True, "enforce_retention_duration": False}]
    monkeypatch.setattr(DeltaTable, "vacuum", lambda self, **kw: [uri + "/a.parquet", "b/c.parquet"])
    assert M.vacuum_preview(uri, 7) == ["a.parquet", "b/c.parquet"]


def test_compact_vacuums_for_real_and_flushes_only_its_own_commits(tmp_path, vacuums, monkeypatch):
    uri = str(tmp_path / "t")
    for i in range(3):
        write_deltalake(uri, pa.table({"id": [i]}), mode="append")
    flushed = []
    monkeypatch.setattr(durable, "sync_commits", lambda u, first, last: flushed.append((first, last)) or True)
    M.compact_local(uri, 7)
    assert {"retention_hours": 169, "dry_run": False, "enforce_retention_duration": False} in vacuums
    after = DeltaTable(uri).version()
    assert flushed == [(3, after)] and after > 2


class Entry:
    def __init__(self, link=False, attrs=None, error=False, mtime=0.0):
        self.link, self.attrs, self.error, self.mtime = link, attrs, error, mtime

    def is_symlink(self):
        if self.error:
            raise FileNotFoundError("gone")
        return self.link

    def stat(self, follow_symlinks=True):
        if self.error:
            raise FileNotFoundError("gone")
        st = {"st_mtime": self.mtime}
        if self.attrs is not None:
            st["st_file_attributes"] = self.attrs
        return type("Stat", (), st)()


def test_what_counts_as_a_link_in_a_table_folder():
    assert M._is_link(Entry()) is False
    assert M._is_link(Entry(attrs=0)) is False
    assert M._is_link(Entry(link=True)) is True
    assert M._is_link(Entry(attrs=stat.FILE_ATTRIBUTE_REPARSE_POINT)) is True  # a junction or mount point
    assert M._is_link(Entry(error=True)) is True  # gone meanwhile: skipped like a link


def test_a_staged_file_is_stale_after_an_hour_and_one_gone_is_neither_old_nor_new():
    now = time.time()
    assert M._older_than(Entry(mtime=now - 3600 - 0.5), 1) is True
    assert M._older_than(Entry(mtime=now - 3600 + 5), 1) is False
    assert M._older_than(Entry(error=True), 1) is False
    assert M._written_since(Entry(error=True), 0) is False
    assert M._written_since(Entry(mtime=now), now) is True


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_a_linked_table_folder_is_refused_with_no_files_and_no_bytes(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "t").symlink_to(tmp_path / "real", target_is_directory=True)
    assert M.stopped_run_leftovers(str(tmp_path / "t"), 0) == {
        "files": [], "bytes": 0, "refused": "the table folder or its log is a link, so its files aren't looked at"}


def test_a_table_that_names_files_by_full_path_is_refused_with_no_files_and_no_bytes(tmp_path, monkeypatch):
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": [1]}))
    monkeypatch.setattr(M, "_named_anywhere", lambda uri, table: {"abfss://ws@onelake/x.parquet"})
    out = M.stopped_run_leftovers(uri, time.time() + 3600)
    assert out["files"] == [] and out["bytes"] == 0 and "names a file by its full path" in out["refused"]


def test_sizes_read_in_megabytes():
    assert M._mb(1024 * 1024) == "1.0 MB"
    assert M._mb(3 * 1024 * 1024 // 2) == "1.5 MB"
    assert M._mb(100 * 1024 * 1024) == "100.0 MB"
