"""The paths of a Delta table's files, as each part of the app compares or opens them. delta-rs names a
partition folder as it is on disk (region=North%20America), VACUUM lists a file by that name, and the
log escapes it once more (region=North%2520America). Every check that matches a log or VACUUM path
against the folder counts each spelling of a named file, compared as the disk does (on Windows, ignoring
case), so a file is only ever kept, or left out of a report. Local tables in tmp_path; nothing is sent."""

from __future__ import annotations

import ntpath
import os
import time
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import maintenance
from fabric_desktop.meta.store import LocalStore
from tests.test_spark_stream import lake  # noqa: F401 - a fixture
from tests.test_spark_stream_edges import k, outputs  # noqa: F401 - k is a fixture
from tests.test_vacuum_hardening import _age, _age_removes, _ids

# A space, a literal %, an =, a / (on disk as %2F) and a letter outside ASCII.
VALUES = ["North America", "a%b", "a=b", "a/b", "café"]


@pytest.fixture
def windows_case(monkeypatch):
    monkeypatch.setattr(maintenance.os.path, "normcase", ntpath.normcase)


def _files(uri: str) -> set[str]:
    return {p.relative_to(uri).as_posix() for p in Path(uri).rglob("*.parquet")}


def _partitioned(uri: str, value: str, versions: int = 3) -> str:
    """``versions`` overwrites of one partition, ``value``, every file written 30 days ago; version 0's file
    removed 20 days ago (past a 7-day retention), version 1's 2 days ago (inside it)."""
    for i in range(versions):
        write_deltalake(uri, pa.table({"id": [i], "region": [value]}), mode="overwrite", partition_by=["region"])
    for path in Path(uri).rglob("*.parquet"):
        _age(path, 30 * 24)
    _age_removes(uri, 1, 20)
    if versions > 2:
        _age_removes(uri, 2, 2)
    return uri


# ---- vacuum_preview: exactly what compact_local then deletes

@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("value", VALUES)
def test_vacuum_preview_lists_exactly_the_files_compact_then_removes(tmp_path, value, windows, monkeypatch):
    if windows:
        monkeypatch.setattr(maintenance.os.path, "normcase", ntpath.normcase)
    uri = _partitioned(str(tmp_path / "t"), value)
    before = _files(uri)
    preview = maintenance.vacuum_preview(uri, 7)
    out = maintenance.compact_local(uri, 7)
    assert sorted(before - _files(uri)) == sorted(preview) and len(preview) == out["vacuumed"] == 1
    assert _ids(uri, 1) == [1] and _ids(uri) == [2]


def _listing_whole(monkeypatch, whole) -> None:
    """VACUUM's dry run giving each file whole (``whole(uri, rel)``) rather than relative to the table."""
    real = DeltaTable.vacuum

    def vacuum(self, *a, **k):
        listed = real(self, *a, **k)
        uri = self.table_uri.removeprefix("file://").rstrip("/")
        return [whole(uri, p) for p in listed] if k.get("dry_run") else listed
    monkeypatch.setattr(DeltaTable, "vacuum", vacuum)


@pytest.mark.parametrize("value", VALUES)
def test_vacuum_preview_names_a_file_given_as_a_url_as_compact_does(tmp_path, value, monkeypatch):
    uri = _partitioned(str(tmp_path / "t"), value)
    want = maintenance.vacuum_preview(uri, 7)
    _listing_whole(monkeypatch, lambda root, rel: (Path(root) / rel).as_uri())  # escaped once more, as a URL is
    assert maintenance.vacuum_preview(uri, 7) == want


def test_vacuum_preview_names_a_whole_windows_path_relative_to_the_table(tmp_path, windows_case, monkeypatch):
    uri = _partitioned(str(tmp_path / "t"), "North America")
    want = maintenance.vacuum_preview(uri, 7)
    _listing_whole(monkeypatch, lambda root, rel: (root.upper() + "/" + rel).replace("/", "\\"))
    assert maintenance.vacuum_preview(uri, 7) == want


def test_compact_counts_a_file_vacuum_lists_in_another_case_on_windows(tmp_path, windows_case, monkeypatch):
    uri = _partitioned(str(tmp_path / "t"), "eu")
    real = DeltaTable.vacuum

    def vacuum(self, *a, **k):
        listed = real(self, *a, **k)
        return listed if k.get("dry_run") else [p.upper() for p in listed]
    monkeypatch.setattr(DeltaTable, "vacuum", vacuum)
    assert maintenance.compact_local(uri, 7)["vacuumed"] == 1


# ---- stopped_run_leftovers: a report, never of a file a version names

def _touched_since(uri: str) -> float:
    """The stopped run's start, after every commit; every data file touched after it (a sync client, a scan)."""
    since = time.time()
    for path in Path(uri).rglob("*.parquet"):
        os.utime(path, (since + 1, since + 1))
    return since


@pytest.mark.parametrize("value", VALUES)
def test_a_file_only_vacuum_names_is_never_a_stopped_runs_leftover(tmp_path, value):
    """Once a checkpoint holds the log and the commits before it are cleaned up, only VACUUM's list names the
    file version 1 removed. It lists it by its name on disk, which decoding would turn into another name."""
    uri = str(tmp_path / "t")
    for i in range(2):
        write_deltalake(uri, pa.table({"id": [i], "region": [value]}), mode="overwrite", partition_by=["region"])
    DeltaTable(uri).create_checkpoint()
    for v in (0, 1):
        (Path(uri) / "_delta_log" / f"{v:020d}.json").unlink()
    since = _touched_since(uri)
    assert DeltaTable(uri).vacuum(retention_hours=0, dry_run=True, enforce_retention_duration=False)
    assert maintenance.stopped_run_leftovers(uri, since) == {"files": [], "bytes": 0, "refused": None}


def test_a_file_a_commit_names_in_another_case_is_never_a_stopped_runs_leftover_on_windows(tmp_path, windows_case):
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": [1], "region": ["EU"]}), partition_by=["region"])
    (Path(uri) / "region=EU").rename(Path(uri) / "REGION=eu")  # the same folder, as Windows sees it
    since = _touched_since(uri)
    assert maintenance.stopped_run_leftovers(uri, since) == {"files": [], "bytes": 0, "refused": None}


# ---- a failed write's own files: the current version's are never removed

LH = "LH_Silver"


def _store_table(tmp_path: Path, value: str) -> tuple[LocalStore, Path, int]:
    store = LocalStore(tmp_path / "dev", "dev")
    uri = store.uri(LH, "dbo", "t")
    write_deltalake(uri, pa.table({"id": [1], "region": [value]}), partition_by=["region"])
    return store, Path(uri), DeltaTable(uri).version()


@pytest.mark.parametrize("value", VALUES)
def test_a_failed_writes_cleanup_never_removes_a_file_the_current_version_names(tmp_path, value):
    """The folder listing from before the write is given as empty, so only the log's names stand between the
    current version's file and its removal."""
    store, root, version = _store_table(tmp_path, value)
    kept = _files(str(root))
    assert store._remove_unwritten(LH, "dbo", "t", set(), version)["removed"] == 0
    assert _files(str(root)) == kept and _ids(str(root)) == [1]


def test_a_failed_writes_cleanup_keeps_a_file_the_log_names_in_another_case_on_windows(tmp_path, windows_case):
    store, root, version = _store_table(tmp_path, "EU")
    (root / "region=EU").rename(root / "REGION=eu")
    kept = _files(str(root))
    assert store._remove_unwritten(LH, "dbo", "t", set(), version)["removed"] == 0
    assert _files(str(root)) == kept


def test_a_failed_writes_cleanup_still_removes_a_file_no_version_names(tmp_path):
    store, root, version = _store_table(tmp_path, "North America")
    left = root / "region=North%20America" / "part-00001-0b1d6a52-6f0e-4c3b-9d0a-5f8c2e7a9b10-c000.snappy.parquet"
    left.write_bytes(b"x")
    assert store._remove_unwritten(LH, "dbo", "t", set(), version)["removed"] == 1 and not left.exists()


# ---- opening a file by the log's path

@pytest.mark.parametrize("value", VALUES)
def test_vorder_files_reads_the_footers_of_files_in_an_escaped_partition(tmp_path, value):
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": [1], "region": [value]}), partition_by=["region"])
    assert maintenance.vorder_files(uri) == {"files": 1, "checked": 1, "v_ordered": 0}


def test_a_delta_stream_reads_a_partition_with_a_space_in_its_value(k, tmp_path):  # noqa: F811
    uri = str(tmp_path / "events")
    write_deltalake(uri, pa.table({"id": [1, 2], "region": ["North America", "café"]}), partition_by=["region"])
    code = (f"src = spark.readStream.format('delta').load({uri!r})\n"
            "src.writeStream.format('console').start()\n")
    status, text = outputs(k, code)
    assert status == "ok" and "batch 0: 2 rows" in text, text
