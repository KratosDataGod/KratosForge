"""VACUUM on real local Delta tables: what it keeps, what it deletes, and what it says it deleted.

Each table here has real history: four overwrites, each removing the file the one before wrote. The
age of each removal is set by rewriting that commit's deletionTimestamp, and the age of a file no
commit names by its mtime, so nothing waits on the clock.
"""

import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, maintenance
from tests.test_spark_writer import lake  # noqa: F401 - a fixture

DAY = 24 * 3600


def _age_removes(uri: str, version: int, days: float) -> None:
    """Say the files commit ``version`` removed were removed ``days`` ago."""
    commit = Path(uri) / "_delta_log" / f"{version:020d}.json"
    ms = int((time.time() - days * DAY) * 1000)
    commit.write_text(re.sub(r'"deletionTimestamp":\d+', f'"deletionTimestamp":{ms}', commit.read_text()))


def _age(path: Path, hours: float) -> None:
    then = time.time() - hours * 3600
    os.utime(path, (then, then))


def _history(uri: str, removed_days_ago=(20, 10, 2), existing: bool = False) -> dict[int, str]:
    """Versions 0 to 3, each overwrite after the first removing the file the one before wrote,
    ``removed_days_ago[v]`` days ago for version v's file. Versions 1 to 3 hold one row, id = the version
    (version 0 too, unless ``existing`` keeps the table's own). Returns the data file each version names."""
    for i in range(1 if existing else 0, 4):
        write_deltalake(uri, pa.table({"id": [i], "region": ["EU"]}), mode="overwrite")
    for v, days in enumerate(removed_days_ago):
        _age_removes(uri, v + 1, days)
    named = {}
    for v in range(4):
        (u,) = DeltaTable(uri, version=v).file_uris()
        named[v] = u.rsplit("/", 1)[-1]
    return named


def _parquet(uri: str) -> set[str]:
    return {p.name for p in Path(uri).glob("*.parquet")}


def _ids(uri: str, version: int | None = None) -> list[int]:
    return sorted(r["id"] for r in DeltaTable(uri, version=version).to_pyarrow_table().to_pylist())


def _orphan(uri: str, hours: float) -> Path:
    """A data file no commit names, as a write stopped before its commit leaves, untouched for ``hours``."""
    root = Path(uri)
    path = root / f"part-00000-{uuid.uuid4()}-c000.snappy.parquet"
    shutil.copy(next(root.glob("*.parquet")), path)
    _age(path, hours)
    return path


@pytest.fixture
def table(tmp_path):
    uri = str(tmp_path / "t")
    return uri, _history(uri)


def test_a_reader_holding_a_version_inside_the_retention_still_reads_it_after_vacuum(table):
    uri, _named = table
    reader = DeltaTable(uri, version=2)  # its file was removed 2 days ago: inside the 7 days kept
    maintenance.compact_local(uri, 7)
    assert reader.to_pyarrow_table().to_pylist() == [{"id": 2, "region": "EU"}]
    assert _ids(uri, 2) == [2]  # and a reader opening it now


def test_versions_past_the_retention_lose_only_the_files_vacuum_removed(table):
    uri, named = table
    before = _parquet(uri)
    result = maintenance.compact_local(uri, 7)
    gone = before - _parquet(uri)
    assert gone == {named[0], named[1]}  # removed 20 and 10 days ago; nothing else
    assert result["vacuumed"] == 2
    for v in (0, 1):
        with pytest.raises(FileNotFoundError):
            DeltaTable(uri, version=v).to_pyarrow_table()
    assert _ids(uri, 2) == [2]
    latest = DeltaTable(uri)
    assert all(Path(u).exists() for u in latest.file_uris())  # the latest version is always whole
    assert _ids(uri) == [3]


def test_a_longer_retention_keeps_files_removed_inside_it(table):
    uri, named = table
    before = _parquet(uri)
    result = maintenance.compact_local(uri, 14)
    assert before - _parquet(uri) == {named[0]} and result["vacuumed"] == 1  # 20 days: past 14; 10 days: kept
    assert _ids(uri, 1) == [1] and _ids(uri, 2) == [2]


@pytest.mark.parametrize("days", [7, 30])
def test_a_data_file_no_commit_names_goes_only_once_older_than_the_retention(table, days):
    """Kept as long as Delta keeps a removed file (the days plus an hour), so a long write still under way
    never loses one; gone once it's older."""
    uri, _named = table
    inside = _orphan(uri, days * 24)  # exactly the days: still inside the hour of margin
    past = _orphan(uri, days * 24 + 2)
    result = maintenance.compact_local(uri, days)
    assert inside.exists() and not past.exists()
    assert result["unnamed_removed"] == 1
    assert _ids(uri) == [3]


def test_the_preview_deletes_nothing_and_lists_exactly_what_vacuum_then_removes(table):
    uri, named = table
    before = _parquet(uri)
    preview = maintenance.vacuum_preview(uri, 7)
    assert maintenance.vacuum_preview(uri, 7) == preview  # and again: still all there
    assert _parquet(uri) == before and DeltaTable(uri).version() == 3
    assert set(preview) == {named[0], named[1]}
    result = maintenance.compact_local(uri, 7)
    assert before - _parquet(uri) == set(preview) and result["vacuumed"] == len(preview)


def test_the_preview_doesnt_list_a_data_file_no_commit_names(table):
    """Only what VACUUM itself deletes: Compact removes such a file in a step of its own, counted apart."""
    uri, _named = table
    past = _orphan(uri, 30 * 24)
    assert past.name not in maintenance.vacuum_preview(uri, 7)
    result = maintenance.compact_local(uri, 7)
    assert not past.exists() and result["unnamed_removed"] == 1 and result["vacuumed"] == 2


def test_compact_counts_only_the_files_it_actually_removed(table):
    """A file VACUUM would delete that is gone meanwhile (another process, a hand) isn't counted as deleted."""
    uri, named = table
    (Path(uri) / named[0]).unlink()
    assert named[0] in maintenance.vacuum_preview(uri, 7)  # the log still lists it
    result = maintenance.compact_local(uri, 7)
    assert result["vacuumed"] == 1 and not (Path(uri) / named[1]).exists()
    assert maintenance.compact_local(uri, 7)["vacuumed"] == 0  # a second run removes nothing more


def _log_race(uri: str) -> OSError:
    """What a losing VACUUM commit raises on Windows when a writer commits the same next version."""
    staged = Path(uri) / "_delta_log" / "00000000000000000030.json#1"
    return OSError(f"Generic LocalFileSystem error: Unable to open file {staged}: Access is denied. (os error 5)")


@pytest.fixture
def races(monkeypatch):
    """VACUUM's own calls, real ones in between; ``plan`` says, per call, whether to fail before VACUUM runs
    ("before"), after it ran and deleted ("after"), or not at all (None)."""
    real, calls = DeltaTable.vacuum, []
    plan: list = []

    def vacuum(self, *args, **kwargs):
        calls.append(kwargs.get("dry_run"))
        when = plan.pop(0) if plan and not kwargs.get("dry_run", True) else None
        if when == "before":
            raise _log_race(self.table_uri)
        listed = real(self, *args, **kwargs)
        if when == "after":
            raise _log_race(self.table_uri)
        return listed

    monkeypatch.setattr(DeltaTable, "vacuum", vacuum)
    monkeypatch.setattr("fabric_desktop.retrying.time.sleep", lambda _s: None)
    return plan, calls


@pytest.mark.parametrize("when", ["before", "after"])
def test_vacuum_losing_a_commit_race_on_windows_is_tried_again_and_counts_what_it_removed(table, races, when):
    """On Windows a writer committing the same next version makes VACUUM START or VACUUM END fail to open its
    staged log file (os error 5). VACUUM is tried again: the same files go, none a kept version names, and the
    count includes what a try that failed after deleting had already removed."""
    uri, named = table
    plan, _calls = races
    plan += [when, when]
    before = _parquet(uri)
    result = maintenance.compact_local(uri, 7)
    assert plan == []  # both failing tries happened
    assert before - _parquet(uri) == {named[0], named[1]}
    assert result["vacuumed"] == 2
    assert _ids(uri, 2) == [2] and _ids(uri) == [3]


def test_a_version_committed_between_vacuum_tries_keeps_its_files(table, races, monkeypatch):
    """The first VACUUM try loses the race; before the next, a writer restores version 0, putting back the file
    version 1 removed 20 days ago. The next try reads the table afresh, so that file, now in the latest version,
    stays; a try on the table as it was before the restore would delete it."""
    uri, named = table
    plan, _calls = races
    plan.append("before")
    real_race = _log_race

    def race_then_restore(u: str) -> OSError:
        DeltaTable(uri).restore(0)
        return real_race(u)

    monkeypatch.setattr(sys.modules[__name__], "_log_race", race_then_restore)
    result = maintenance.compact_local(uri, 7)
    assert plan == []
    assert (Path(uri) / named[0]).exists()  # named by the latest version again
    assert _ids(uri) == [0]  # and the latest version reads whole
    assert not (Path(uri) / named[1]).exists() and result["vacuumed"] == 1  # version 1's file: still past the 7 days


def test_vacuum_still_raises_any_other_error_at_once(table, races, monkeypatch):
    uri, _named = table
    _plan, calls = races

    def refused(self, *args, **kwargs):
        calls.append(kwargs.get("dry_run"))
        raise OSError("Generic LocalFileSystem error: Unable to open file C:/x/part-0.parquet: Access is denied. "
                      "(os error 5)")  # not the Delta log: not the race

    monkeypatch.setattr(DeltaTable, "vacuum", refused)
    with pytest.raises(OSError, match="part-0.parquet"):
        maintenance.compact_local(uri, 7)
    assert calls.count(False) == 1


def test_vacuum_gives_up_on_a_race_that_never_clears(table, races):
    uri, named = table
    plan, calls = races
    plan += ["before"] * 10
    with pytest.raises(OSError, match="os error 5"):
        maintenance.compact_local(uri, 7)
    assert calls.count(False) == 6  # retrying.local_retry's attempts, then the error as it was
    assert {named[0], named[1]} <= _parquet(uri)  # nothing deleted


def test_nothing_inside_the_retention_means_nothing_vacuumed(tmp_path):
    uri = str(tmp_path / "t")
    named = _history(uri, removed_days_ago=(6, 3, 1))
    before = _parquet(uri)
    assert maintenance.vacuum_preview(uri, 7) == []
    assert maintenance.compact_local(uri, 7)["vacuumed"] == 0
    assert _parquet(uri) == before
    assert [_ids(uri, v) for v in named] == [[0], [1], [2], [3]]  # all time travel still works


# The notebook's VACUUM (lakehouse.maintain_table) on a table of the attached lakehouse.

@pytest.fixture
def sales(lake, monkeypatch):  # noqa: F811
    """dbo.sales (the lake fixture writes version 0) with three overwrites after it."""
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: None)
    uri = str(lake / "dbo" / "sales")
    return uri, _history(uri, removed_days_ago=(40, 10, 2), existing=True)


def test_notebook_vacuum_dry_run_deletes_nothing_and_lists_what_the_real_run_deletes(sales):
    uri, named = sales
    before, version = _parquet(uri), DeltaTable(uri).version()
    dry = lakehouse.maintain_table("dbo.sales", "vacuum", dry_run=True)
    assert _parquet(uri) == before and DeltaTable(uri).version() == version
    assert dry.metrics["numDeletedFiles"] == 0 and dry.metrics["dryRun"] is True
    assert set(dry.files) == {named[0], named[1]}
    real = lakehouse.maintain_table("dbo.sales", "vacuum")
    gone = before - _parquet(uri)
    assert set(real.files) == set(dry.files) == gone
    assert real.metrics["numDeletedFiles"] == len(gone) == 2
    assert real.metrics["retentionHours"] == maintenance.retention_hours(7)
    assert _ids(uri, 2) == [2] and _ids(uri) == [3]


def test_notebook_vacuum_keeps_a_version_a_reader_holds_inside_the_retention(sales):
    uri, _named = sales
    reader = DeltaTable(uri, version=2)
    lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=24 * 7)
    assert reader.to_pyarrow_table().to_pylist() == [{"id": 2, "region": "EU"}]
    with pytest.raises(FileNotFoundError):
        DeltaTable(uri, version=0).to_pyarrow_table()
    assert all(Path(u).exists() for u in DeltaTable(uri).file_uris())


def test_notebook_vacuum_honours_the_tables_own_longer_retention(sales):
    uri, named = sales
    DeltaTable(uri).alter.set_table_properties({"delta.deletedFileRetentionDuration": "interval 30 days"})
    before = _parquet(uri)
    dry = lakehouse.maintain_table("dbo.sales", "vacuum", dry_run=True)
    assert dry.metrics["retentionHours"] == maintenance.retention_hours(30) and set(dry.files) == {named[0]}
    real = lakehouse.maintain_table("dbo.sales", "vacuum")
    assert before - _parquet(uri) == {named[0]} and real.metrics["numDeletedFiles"] == 1  # 40 days: past 30
    assert _ids(uri, 1) == [1] and _ids(uri, 2) == [2]  # removed 10 and 2 days ago: kept
    with pytest.raises(ValueError, match="keeps 30 days"):
        lakehouse.maintain_table("dbo.sales", "vacuum", retain_hours=24 * 7)
    assert _ids(uri, 1) == [1]  # the refusal deleted nothing
