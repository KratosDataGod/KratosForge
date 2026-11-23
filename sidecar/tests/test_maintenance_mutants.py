"""What a hand mutation run over the maintenance safety code (retrying.py, maintenance.py, a notebook's
OPTIMIZE / VACUUM / RESTORE and LocalStore's race handling) showed no test checked. Each test here fails
for one change to that code that the other tests let through. Local tables in tmp_path; nothing is sent.

The skew tests hand skewed_versions a history (it reads nothing else of the table), with the clock held
still, so a commit can be dated to the millisecond at VACUUM's cutoff."""

from __future__ import annotations

import ntpath
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError
from deltalake.table import TableOptimizer

from fabric_desktop import delta_profile, lakehouse, maintenance, onelake, retrying
from fabric_desktop.meta import store as store_mod
from tests.test_delta_profile_writes import OUTSIDE
from tests.test_spark_writer import lake  # noqa: F401 - a fixture
from tests.test_vacuum_hardening import _age, _age_removes, _history, _ids, sales  # noqa: F401 - sales is a fixture
from tests.test_vacuum_retention_boundaries import _snapshot
from tests.test_windows_commit_race_conflicts import STAGED, frame, no_wait, store, table_ids  # noqa: F401
from tests.test_zorder_hardening import allowed, events  # noqa: F401 - allowed is a fixture

NOW = 1_800_000_000  # seconds: the clock, held still
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
CUTOFF_MS = NOW * 1000 - maintenance.retention_hours(7) * HOUR_MS + 300_000  # skewed_versions' cutoff for 7 days


def dated(*ages_ms: float | None) -> SimpleNamespace:
    """A table whose commit v is dated ``ages_ms[v]`` before NOW (None: the commit has no date)."""
    history = [{"version": v} if age is None else {"version": v, "timestamp": NOW * 1000 - age}
               for v, age in enumerate(ages_ms)]
    return SimpleNamespace(history=lambda: list(reversed(history)))


def skewed(*ages_ms: float | None) -> tuple[list[int], int | None]:
    return maintenance.skewed_versions(dated(*ages_ms), maintenance.retention_hours(7), NOW)


# ---- retrying: which errors are the Windows staged-commit race

def test_a_staged_commit_is_a_twenty_digit_log_version_only():
    assert retrying.staged_commit_race(OSError("Unable to open file _delta_log/3.json#1 (os error 5)")) is False


# ---- the retention guards

def test_true_is_not_a_number_of_days_even_where_a_shorter_retention_is_allowed(tmp_path):
    uri = str(tmp_path / "t")
    _history(uri)
    before = _snapshot(uri)
    with pytest.raises(ValueError, match="at least 7 days"):
        maintenance.check_retention(True, allow_shorter=True)
    with pytest.raises(ValueError, match="at least 7 days"):
        maintenance.compact_local(uri, True, allow_shorter=True)
    assert _snapshot(uri) == before


def test_compact_refuses_too_few_days_even_when_the_tables_own_retention_is_longer(tmp_path):
    uri = str(tmp_path / "t")
    _history(uri)
    DeltaTable(uri).alter.set_table_properties({"delta.deletedFileRetentionDuration": "interval 30 days"})
    before = _snapshot(uri)
    with pytest.raises(ValueError, match="at least 7 days"):
        maintenance.compact_local(uri, 1)
    assert _snapshot(uri) == before


# ---- skewed_versions: which versions keep their files whatever a later commit's clock says

def test_a_commit_dated_exactly_at_the_cutoff_is_inside_the_retention():
    # version 1 is dated at the cutoff, so versions 0 and 1 are inside it, and version 2 is dated before it
    assert skewed(30 * DAY_MS, NOW * 1000 - CUTOFF_MS, 20 * DAY_MS, DAY_MS) == ([0, 1], NOW * 1000 - 20 * DAY_MS)
    # a later commit dated exactly at the cutoff isn't before it: VACUUM keeps what it removed anyway
    assert skewed(30 * DAY_MS, DAY_MS, NOW * 1000 - CUTOFF_MS, 2 * HOUR_MS) == ([], None)


def test_a_removal_dated_inside_the_five_minutes_after_vacuums_own_cutoff_is_kept():
    # two minutes inside 7 days and an hour: VACUUM reads the clock later, so it could fall outside by then
    inside = maintenance.retention_hours(7) * HOUR_MS - 2 * 60_000
    assert skewed(30 * DAY_MS, DAY_MS, inside, 2 * HOUR_MS) == ([0, 1], NOW * 1000 - inside)


def test_a_version_is_inside_the_retention_when_the_commit_that_replaced_it_is():
    # version 0 is dated before the cutoff, but version 1 (which replaced it) is inside; version 2 is skewed
    assert skewed(20 * DAY_MS, DAY_MS, 10 * DAY_MS, DAY_MS // 2) == ([0, 1], NOW * 1000 - 10 * DAY_MS)


def test_the_version_before_the_newest_is_checked_too():
    assert skewed(30 * DAY_MS, 20 * DAY_MS, DAY_MS, 10 * DAY_MS) == ([1, 2], NOW * 1000 - 10 * DAY_MS)


def test_a_commit_with_no_date_counts_as_made_now():
    assert skewed(30 * DAY_MS, None, 10 * DAY_MS) == ([0, 1], NOW * 1000 - 10 * DAY_MS)


def test_the_oldest_date_is_the_earliest_over_every_kept_version():
    keep, oldest = skewed(30 * DAY_MS, DAY_MS, 20 * DAY_MS, 2 * HOUR_MS, 10 * DAY_MS, HOUR_MS)
    assert keep == [0, 1, 2, 3] and oldest == NOW * 1000 - 20 * DAY_MS


# ---- skew_safe_days: the days Fabric's VACUUM needs, which can only be told days

def test_skew_safe_days_rounds_a_skew_just_past_the_days_up_to_one_more_day():
    table = dated(30 * DAY_MS, DAY_MS, 7.5 * DAY_MS, DAY_MS // 2)
    assert maintenance.skew_safe_days(table, 7, NOW) == 8


def test_skew_safe_days_counts_the_hour_of_margin_vacuum_keeps():
    # 168.5 hours ago is past 7 days but inside the 7 days and an hour that fabric_retention keeps
    table = dated(30 * DAY_MS, DAY_MS, 168.5 * HOUR_MS, DAY_MS // 2)
    assert maintenance.skew_safe_days(table, 7, NOW) == 7


def test_skew_safe_days_looks_again_with_the_longer_retention():
    # at 8 days version 0 comes inside the retention, and version 2 (dated 40 days ago) would take its files
    table = dated(50 * DAY_MS, 7.5 * DAY_MS, 40 * DAY_MS, DAY_MS, 8 * DAY_MS, HOUR_MS)
    assert maintenance.skew_safe_days(table, 7, NOW) == 40


# ---- compact_local: what it deletes, and what it says it deleted

def test_compact_leaves_every_file_a_version_inside_the_retention_reads_however_old_the_file_is(tmp_path):
    """A file written long ago is no leftover: the current version reads it, or a version still inside the
    retention does. Nor is a file that isn't a Delta data file."""
    uri = str(tmp_path / "t")
    _history(uri, removed_days_ago=(2, 2, 2))
    note = Path(uri) / "notes.txt"
    note.write_text("kept")
    for path in [*Path(uri).glob("*.parquet"), note]:
        _age(path, 30 * 24)
    out = maintenance.compact_local(uri, 7)
    assert out["vacuumed"] == 0 and out["unnamed_removed"] == 0
    assert [_ids(uri, v) for v in range(4)] == [[0], [1], [2], [3]] and note.exists()


def _spaced(uri: str) -> str:
    """Three overwrites in partition region=North America, every file written 30 days ago; version 0's file
    removed 20 days ago (past the retention), version 1's 2 days ago (inside it). delta-rs names the folder
    region=North%20America on disk, the log escapes that again (North%2520America), and VACUUM lists a file
    by its name on disk."""
    for i in range(3):
        write_deltalake(uri, pa.table({"id": [i], "region": ["North America"]}), mode="overwrite",
                        partition_by=["region"])
    for path in Path(uri).rglob("*.parquet"):
        _age(path, 30 * 24)
    _age_removes(uri, 1, 20)
    _age_removes(uri, 2, 2)
    return uri


def test_compact_keeps_a_partition_with_a_space_in_its_value_and_counts_what_it_vacuums(tmp_path):
    uri = str(tmp_path / "t")
    out = maintenance.compact_local(_spaced(uri), 7)
    assert out["vacuumed"] == 1 and out["unnamed_removed"] == 0
    assert _ids(uri, 1) == [1] and _ids(uri) == [2]


def test_notebook_vacuum_counts_a_file_in_a_partition_with_a_space_in_its_value(allowed):  # noqa: F811
    uri = _spaced(str(allowed / "dbo" / "spaced"))
    result = lakehouse.maintain_table("dbo.spaced", "vacuum")
    (gone,) = result.files
    assert gone.startswith("region=North%20America/") and result.metrics["numDeletedFiles"] == 1
    assert _ids(uri, 1) == [1] and _ids(uri) == [2]


def test_only_a_url_is_decoded_a_path_is_the_files_name_on_disk():
    assert maintenance.relative_to_table("/lake/t", "region=a%25b/part-1.parquet") == "region=a%25b/part-1.parquet"
    assert maintenance.relative_to_table("/lake/t", "/lake/t/region=a%25b/part-1.parquet") == "region=a%25b/part-1.parquet"
    assert maintenance.relative_to_table("/lake/t", "file:///lake/t/region=a%2525b/part-1.parquet") == \
        "region=a%25b/part-1.parquet"


def _vacuum_leaving(monkeypatch, path: Path) -> None:
    """VACUUM that deletes what it lists, except ``path``, which is back in place when it returns (a reader
    holding it open on Windows, another process writing it back)."""
    real = DeltaTable.vacuum

    def vacuum(self, *a, **k):
        data = path.read_bytes() if path.exists() else None
        listed = real(self, *a, **k)
        if not k.get("dry_run") and data is not None and not path.exists():
            path.write_bytes(data)
        return listed
    monkeypatch.setattr(DeltaTable, "vacuum", vacuum)


def test_compact_doesnt_count_a_listed_file_still_on_disk(tmp_path, monkeypatch):
    uri = str(tmp_path / "t")
    named = _history(uri)  # versions 0 and 1 lose their files: removed 20 and 10 days ago
    _vacuum_leaving(monkeypatch, Path(uri) / named[0])
    assert maintenance.compact_local(uri, 7)["vacuumed"] == 1


def test_notebook_vacuum_doesnt_count_a_listed_file_still_on_disk(sales, monkeypatch):
    uri, named = sales
    _vacuum_leaving(monkeypatch, Path(uri) / named[0])
    result = lakehouse.maintain_table("dbo.sales", "vacuum")
    assert set(result.files) == {named[0], named[1]} and result.metrics["numDeletedFiles"] == 1


def test_a_notebook_vacuum_dry_run_of_a_table_elsewhere_counts_nothing_deleted(sales, monkeypatch):
    """A table not on this PC (here a file:// URL) can't be checked for what was deleted: a dry run says none."""
    uri, named = sales
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: Path(uri).as_uri()))
    result = lakehouse.maintain_table("dbo.sales", "vacuum", dry_run=True)
    assert len(result.files) == 2 and result.metrics["numDeletedFiles"] == 0


# ---- the paths delta-rs lists, and the files a table folder holds

@pytest.fixture
def windows_case(monkeypatch):
    monkeypatch.setattr(maintenance.os.path, "normcase", ntpath.normcase)


def test_a_whole_windows_path_in_a_partition_is_named_with_forward_slashes(windows_case):
    listed = "C:\\Lake\\Tables\\dbo\\events\\region=EU\\part-1.parquet"
    assert maintenance.relative_to_table("C:\\Lake\\Tables\\dbo\\events", listed) == "region=EU/part-1.parquet"


def test_a_table_folder_given_with_a_trailing_slash_still_names_files_relative_to_it():
    assert maintenance.relative_to_table("/lake/t/", "/lake/t/part-1.parquet") == "part-1.parquet"


def test_table_files_compare_as_windows_does_ignoring_case(tmp_path, windows_case):
    (tmp_path / "Part-A.parquet").write_bytes(b"x")
    assert maintenance.table_files(str(tmp_path)) == {"part-a.parquet"}


def test_removing_counts_only_what_it_deleted_and_reports_a_refused_delete(tmp_path, monkeypatch):
    assert maintenance._remove(str(tmp_path), ["gone.parquet"]) == 0
    (tmp_path / "held.parquet").write_bytes(b"x")

    def refused(self, *a, **k):
        raise PermissionError(13, "Access is denied", str(self))
    monkeypatch.setattr(Path, "unlink", refused)
    with pytest.raises(PermissionError):
        maintenance._remove(str(tmp_path), ["held.parquet"])


# ---- a notebook's OPTIMIZE, VACUUM and RESTORE

def test_a_notebook_optimize_that_lost_the_race_compacts_the_winners_files_too(allowed, monkeypatch, no_wait):  # noqa: F811
    uri = events(allowed, 3)
    real = TableOptimizer.compact
    calls: list = []

    def compact(self, *a, **k):
        calls.append(1)
        if len(calls) == 1:  # another writer commits the version this one wanted
            write_deltalake(uri, pa.table({"id": [999], "region": ["EU"], "amount": [1.0]}), mode="append")
            raise OSError(STAGED)
        return real(self, *a, **k)
    monkeypatch.setattr(TableOptimizer, "compact", compact)
    lakehouse.maintain_table("dbo.events", "optimize")
    assert len(calls) == 2 and len(DeltaTable(uri).file_uris()) == 1
    assert 999 in _ids(uri)


def test_a_notebook_vacuum_that_lost_the_race_to_a_restore_runs_on_the_restored_table(sales, monkeypatch, no_wait):
    uri, _named = sales
    real = DeltaTable.vacuum
    calls: list = []

    def vacuum(self, *a, **k):
        calls.append(1)
        if len(calls) == 1:  # another writer restores version 0, whose file was removed 40 days ago
            DeltaTable(uri).restore(0)
            raise OSError(STAGED)
        return real(self, *a, **k)
    monkeypatch.setattr(DeltaTable, "vacuum", vacuum)
    lakehouse.maintain_table("dbo.sales", "vacuum")
    assert len(calls) == 2 and _ids(uri) == [1, 2, 3]


def test_a_notebook_restore_to_a_negative_version_is_refused_before_delta_rs_sees_it(allowed, monkeypatch):  # noqa: F811
    uri = events(allowed, 3)
    version = DeltaTable(uri).version()
    monkeypatch.setattr(DeltaTable, "restore", lambda *a, **k: pytest.fail("RESTORE was sent"))
    with pytest.raises(ValueError, match="RESTORE goes to an earlier one, not -1"):
        lakehouse.maintain_table("dbo.events", "restore", version=-1)
    assert DeltaTable(uri).version() == version


# ---- LocalStore: an overwrite never goes in over another writer's commit

def test_an_overwrite_after_another_writer_committed_since_the_read_is_a_conflict(store):  # noqa: F811
    uri = store.uri("LH_Bronze", "dbo", "events")
    store.write("LH_Bronze", "dbo", "events", frame(100), mode="append")  # another writer, after version 0
    with pytest.raises(CommitFailedError):
        store.write("LH_Bronze", "dbo", "events", frame(200), mode="overwrite", read_version=0)
    assert table_ids(store) == list(range(5)) + list(range(100, 105)) and DeltaTable(uri).version() == 1


def test_a_streamed_replace_after_another_writer_committed_since_the_read_is_a_conflict(store):  # noqa: F811
    store.write("LH_Bronze", "dbo", "events", frame(100), mode="append")
    rows = frame(200).to_arrow()
    with pytest.raises(CommitFailedError):
        store.write_stream("LH_Bronze", "dbo", "events", pa.RecordBatchReader.from_batches(rows.schema, rows.to_batches()),
                           read_version=0)
    assert table_ids(store) == list(range(5)) + list(range(100, 105))


def test_an_overwrite_of_a_table_there_wasnt_one_of_is_a_conflict_when_one_was_made_meanwhile(store):  # noqa: F811
    with pytest.raises(CommitFailedError, match="events was created by another writer meanwhile"):
        store.write("LH_Bronze", "dbo", "events", frame(200), mode="overwrite", read_version=None)
    assert table_ids(store) == list(range(5))


def test_an_overwrite_that_lost_to_another_writers_create_isnt_sent_again(store, monkeypatch):  # noqa: F811
    calls: list = []

    def lose(*a, **k):
        calls.append(1)
        raise CommitFailedError("Protocol changed since the read")
    monkeypatch.setattr(store_mod, "write_deltalake", lose)
    with pytest.raises(CommitFailedError, match="Protocol changed"):
        store.write("LH_Bronze", "dbo", "events", frame(200), mode="overwrite")
    assert calls == [1]


def test_a_first_append_that_lost_but_finds_no_table_after_is_the_conflict(store, monkeypatch):  # noqa: F811
    calls: list = []

    def lose(*a, **k):
        calls.append(1)
        raise CommitFailedError("Protocol changed since the read")
    monkeypatch.setattr(store_mod, "write_deltalake", lose)
    with pytest.raises(CommitFailedError, match="Protocol changed"):
        store.write("LH_Bronze", "dbo", "fresh", frame(0), mode="append")
    assert calls == [1]


def test_an_append_that_lost_to_another_writers_new_column_is_made_on_the_table_as_it_is_now(store, monkeypatch):  # noqa: F811
    uri = store.uri("LH_Bronze", "dbo", "events")
    real = store_mod.write_deltalake
    calls: list = []

    def lose_once(target, data, **k):
        calls.append(1)
        if len(calls) == 1:
            real(uri, pa.table({"id": pa.array([999], pa.int64()), "amount": [0.0], "note": ["x"]}), mode="append",
                 schema_mode="merge")
            raise CommitFailedError("Metadata changed since the read")
        return real(target, data, **k)
    monkeypatch.setattr(store_mod, "write_deltalake", lose_once)
    store.write("LH_Bronze", "dbo", "events", frame(100), mode="append")
    assert len(calls) == 2 and table_ids(store) == list(range(5)) + list(range(100, 105)) + [999]


def test_a_first_append_that_lost_to_a_table_outside_the_profile_isnt_made_again(store, monkeypatch):  # noqa: F811
    uri = store.uri("LH_Bronze", "dbo", "fresh")
    real = store_mod.write_deltalake
    calls: list = []

    def lose_once(target, data, **k):
        calls.append(1)
        if len(calls) == 1:  # another writer makes the table first, with a setting Fabric can't read
            real(uri, pa.table({"id": pa.array([999], pa.int64()), "amount": [0.0]}), configuration=OUTSIDE)
            raise CommitFailedError("Protocol changed since the read")
        return real(target, data, **k)
    monkeypatch.setattr(store_mod, "write_deltalake", lose_once)
    with pytest.raises(delta_profile.ProfileViolation):
        store.write("LH_Bronze", "dbo", "fresh", frame(0), mode="append")
    assert calls == [1] and DeltaTable(uri).version() == 0


def _named_only_by(adds: list[str]) -> SimpleNamespace:
    """A table whose current version names ``adds`` (as its log spells them) and whose VACUUM lists nothing."""
    return SimpleNamespace(get_add_actions=lambda flatten=True: pa.table({"path": adds}),
                           vacuum=lambda **k: [])


def _old_file(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    _age(path, 30 * 24)


def test_a_file_a_commit_names_as_written_on_disk_is_never_a_leftover(tmp_path):
    """A writer that didn't escape the partition folder's name again in the log: the add's path is the name on
    disk, and decoding it (region=a/b) would name nothing there. Every spelling counts, so it's kept."""
    rel = "region=a%2Fb/part-00000-0b1d6a52-6f0e-4c3b-9d0a-5f8c2e7a9b10-c000.snappy.parquet"
    _old_file(tmp_path, rel)
    assert maintenance._unnamed(str(tmp_path), _named_only_by([rel]), 7 * 24) == []
    assert maintenance._unnamed(str(tmp_path), _named_only_by([]), 7 * 24) == [rel]  # an unnamed one still goes


def test_a_file_a_commit_names_in_another_case_is_never_a_leftover_on_windows(tmp_path, windows_case):
    rel = "region=EU/part-00000-0b1d6a52-6f0e-4c3b-9d0a-5f8c2e7a9b10-c000.snappy.parquet"
    _old_file(tmp_path, rel)
    named = _named_only_by(["Region=eu/PART-00000-0B1D6A52-6F0E-4C3B-9D0A-5F8C2E7A9B10-C000.SNAPPY.PARQUET"])
    assert maintenance._unnamed(str(tmp_path), named, 7 * 24) == []
