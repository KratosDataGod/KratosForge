"""Maintenance keeps the Delta log that time travel inside the history kept reads, as VACUUM keeps the files.

delta-rs 1.6.6 cleans the log when a commit writes a checkpoint: it deletes each commit and checkpoint older than
delta.logRetentionDuration before the last checkpoint at or before the oldest log file it keeps. That can be the
version current at the start of the history kept, which then can't be loaded although VACUUM kept its files,
and a time travel to then reads a later version. Each commit here is dated as its writer's clock would have stamped it (commitInfo, removals, the
commit file's and its checkpoint's mtime), and the real cleanup runs: no mocks.
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, maintenance
from fabric_desktop.meta import restore as restore_mod
from fabric_desktop.meta.store import META_LAKEHOUSE, LocalStore
from fabric_desktop.meta_service import MetaMixin
from tests.test_maintenance_failure_paths import RUNNING, engine
from tests.test_spark_writer import lake, run  # noqa: F401 - a fixture
from tests.test_vacuum_retention_boundaries import _stamp

DAY = 24 * 3600
EVERY = {"delta.checkpointInterval": "1"}
SHORT_LOG = {"delta.logRetentionDuration": "interval 1 days"}  # as another engine might set it


def _date(uri: str, ages_days: list[float]) -> None:
    """Date commit v ``ages_days[v]`` days ago as _stamp does, then give each version a checkpoint of that
    date, as a table checkpointed at every commit has; and then a commit a day ago (KEEP_LOG, so it cleans
    nothing) that makes every later commit checkpoint, and so able to clean the log."""
    for v, days in enumerate(ages_days):
        _stamp(uri, v, days * DAY)
        DeltaTable(uri, version=v).create_checkpoint()
        when = time.time() - days * DAY
        os.utime(Path(uri) / "_delta_log" / f"{v:020d}.checkpoint.parquet", (when, when))
    DeltaTable(uri).alter.set_table_properties(EVERY, post_commithook_properties=maintenance.KEEP_LOG)
    _stamp(uri, len(ages_days), DAY)


def _ids(uri: str, version: int | None = None) -> list[int]:
    return sorted(DeltaTable(uri, version=version).to_pyarrow_table().column("id").to_pylist())


def _ids_at(uri: str, days_ago: float) -> list[int]:
    t = DeltaTable(uri)
    t.load_as_version(datetime.fromtimestamp(time.time() - days_ago * DAY, timezone.utc))
    return sorted(t.to_pyarrow_table().column("id").to_pylist())


def _history(uri: str, config: dict[str, str], ages_days: list[float]) -> None:
    """Version 0 holds id 0 and each later one overwrites it with id = the version, dated by _date."""
    write_deltalake(uri, pa.table({"id": [0], "region": ["EU"]}), configuration=config or None)
    for v in range(1, len(ages_days)):
        write_deltalake(uri, pa.table({"id": [v], "region": ["EU"]}), mode="overwrite")
    _date(uri, ages_days)


# ---- what delta-rs does: the hazard the tests below run into is real

def test_a_commit_that_checkpoints_cleans_the_log_of_a_version_whose_files_are_kept(tmp_path):
    """Without the app: version 1, current until two days ago, is gone once a write checkpoints."""
    uri = str(tmp_path / "t")
    _history(uri, {}, [40, 35, 2])
    write_deltalake(uri, pa.table({"id": [9], "region": ["EU"]}), mode="append")
    with pytest.raises(Exception, match="(?i)no files in log segment|not found"):
        DeltaTable(uri, version=1)
    assert _ids_at(uri, 3) != [1]  # a time travel to three days ago doesn't read version 1


# ---- compact_local (Optimize on the Maintain page, layers' Keep history)

def test_compact_keeps_the_version_current_at_the_start_of_the_history_kept(tmp_path):
    """Delta's default 30-day log: version 1, written 35 days ago and current until 2 days ago, stays
    loadable after compacting with 7 days kept, and a time travel to 3 days ago reads it."""
    uri = str(tmp_path / "t")
    _history(uri, {}, [40, 35, 2])
    maintenance.compact_local(uri, 7)
    assert _ids(uri, 1) == [1] and _ids(uri, 2) == [2]
    assert _ids_at(uri, 3) == [1]
    assert maintenance.log_seconds(DeltaTable(uri).metadata().configuration) >= 36 * DAY


def test_compact_with_a_short_log_retention_set_elsewhere_keeps_every_version_inside_the_days(tmp_path):
    uri = str(tmp_path / "t")
    _history(uri, SHORT_LOG, [20, 10, 3, 2])
    maintenance.compact_local(uri, 7)
    assert [_ids(uri, v) for v in (1, 2, 3)] == [[1], [2], [3]]
    assert _ids_at(uri, 6) == [1] and _ids_at(uri, 2.5) == [2]


def test_compact_twice_raises_the_log_retention_once(tmp_path):
    """A table written seldom isn't given a property commit at every run."""
    uri = str(tmp_path / "t")
    _history(uri, {}, [40, 35, 2])
    maintenance.compact_local(uri, 7)
    version = DeltaTable(uri).version()
    maintenance.compact_local(uri, 7)
    assert DeltaTable(uri).version() == version
    assert _ids(uri, 1) == [1]


def test_set_retention_keeps_the_tables_own_longer_log_retention(tmp_path):
    uri = str(tmp_path / "t")
    _history(uri, {"delta.logRetentionDuration": "interval 365 days"}, [3, 2])
    assert maintenance.set_retention(uri, 7) is True
    config = DeltaTable(uri).metadata().configuration
    assert config["delta.logRetentionDuration"] == "interval 365 days"
    assert config["delta.deletedFileRetentionDuration"] == "interval 7 days"
    assert maintenance.set_retention(uri, 7) is False
    maintenance.set_retention(uri, 400)
    assert DeltaTable(uri).metadata().configuration["delta.logRetentionDuration"] == "interval 400 days"


def test_keep_log_changes_nothing_when_the_log_already_keeps_enough(tmp_path):
    uri = str(tmp_path / "t")
    _history(uri, {}, [20, 10, 3])
    assert maintenance.keep_log(uri, maintenance.retention_hours(7)) is False
    assert DeltaTable(uri).version() == 3
    _history(str(tmp_path / "u"), {"delta.enableExpiredLogCleanup": "false"} | SHORT_LOG, [20, 10, 3])
    assert maintenance.keep_log(str(tmp_path / "u"), maintenance.retention_hours(7)) is False


@pytest.mark.parametrize("text, seconds", [("interval 30 days", 30 * DAY), ("interval 36 hours", 36 * 3600),
                                           ("interval 2 weeks", 14 * DAY), ("INTERVAL 1 DAY", DAY),
                                           ("interval 5 minutes", 300), ("a while", 0), ("", 0)])
def test_log_seconds_reads_the_tables_interval_and_an_unreadable_one_as_none_kept(text, seconds):
    assert maintenance.log_seconds({"delta.logRetentionDuration": text}) == seconds


def test_log_seconds_default_and_cleanup_off():
    assert maintenance.log_seconds({}) == maintenance.LOG_RETENTION_DAYS * DAY
    assert maintenance.log_seconds({"delta.enableExpiredLogCleanup": "False"}) == float("inf")


# ---- a notebook's OPTIMIZE, ZORDER, VACUUM and RESTORE (lakehouse.maintain_table)

@pytest.fixture
def guarded(lake, tmp_path, monkeypatch):  # noqa: F811
    m = engine(tmp_path, RUNNING)
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: MetaMixin._maintenance_refusal(m, op, ws, t))
    return str(lake / "dbo" / "sales")


# A log kept 1 day with 7 days of history kept, and no checkpoint; and a log kept 10 days on a table keeping
# 30 days of deleted files, checkpointed at version 4. Version 2 is current at the start of the history kept.
CASES = {"short log": (SHORT_LOG, [30, 20, 10, 3, 1.5], False, 6),
         "log shorter than files": ({"delta.logRetentionDuration": "interval 10 days",
                                     "delta.deletedFileRetentionDuration": "interval 30 days"},
                                    [60, 50, 40, 20, 1.5], True, 29)}
OPS = {"optimize": {}, "zorder": {"zorder": ["id"]}, "vacuum": {}, "restore": {"version": 3}}


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("op", OPS)
def test_notebook_maintenance_keeps_every_version_inside_the_history_kept(guarded, case, op):
    """Versions 2 to 4 are current inside the history kept: each still loads after the notebook's OPTIMIZE,
    ZORDER, VACUUM or RESTORE, and a time travel to a day after its start reads version 2. Version 1 sets
    the table's properties; 3 and 4 append to 2, so OPTIMIZE has files to compact."""
    uri = guarded
    config, ages, checkpoint, inside = CASES[case]
    DeltaTable(uri).alter.set_table_properties(config | EVERY, post_commithook_properties=maintenance.KEEP_LOG)
    for i, mode in ((2, "overwrite"), (3, "append"), (4, "append")):
        write_deltalake(uri, pa.table({"id": [i], "region": ["EU"]}), mode=mode)
    for v, days in enumerate(ages):
        _stamp(uri, v, days * DAY)
    for p in (Path(uri) / "_delta_log").glob("*checkpoint*"):
        p.unlink()  # it was written before the dates, which it would otherwise hide
    if checkpoint:
        DeltaTable(uri).create_checkpoint()
    r = lakehouse.maintain_table("dbo.sales", "optimize" if op == "zorder" else op, **OPS[op])
    if op == "vacuum" and not checkpoint:
        assert r.metrics["numDeletedFiles"] == 1  # version 0's: it commits, and checkpoints
    assert [_ids(uri, v) for v in (2, 3, 4)] == [[2], [2, 3], [2, 3, 4]]
    assert _ids_at(uri, inside) == [2]


def test_the_log_before_the_history_kept_is_still_cleaned(tmp_path):
    """keep_log keeps only what the history kept reads: an OPTIMIZE that checkpoints still deletes the log of
    versions 0 and 1, replaced 40 days ago, and keeps version 2's checkpoint on, from which version 2 loads."""
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": [0], "region": ["EU"]}))
    for i, mode in ((1, "overwrite"), (2, "overwrite"), (3, "append")):
        write_deltalake(uri, pa.table({"id": [i], "region": ["EU"]}), mode=mode)
    _date(uri, [100, 95, 40, 3])  # and version 4
    assert maintenance.keep_log(uri, maintenance.retention_hours(7)) is True  # version 5
    assert DeltaTable(uri).metadata().configuration["delta.logRetentionDuration"] == "interval 60 days"
    DeltaTable(uri).optimize.compact()  # version 6: it checkpoints, and cleans the log
    log = {p.name for p in (Path(uri) / "_delta_log").iterdir()}
    assert not {f"{v:020d}.{kind}" for v in (0, 1) for kind in ("json", "checkpoint.parquet")} & log
    assert f"{2:020d}.checkpoint.parquet" in log
    assert [_ids(uri, v) for v in (2, 3, 6)] == [[2], [2, 3], [2, 3]]
    assert _ids_at(uri, 6) == [2]


def test_restore_in_the_app_cleans_no_log(tmp_path):
    """The History page's RESTORE (meta.restore) on a table whose log is kept 1 day."""
    uri = str(tmp_path / "t")
    _history(uri, SHORT_LOG, [20, 10, 3, 2])
    restore_mod.restore(uri, None, 2)
    assert [_ids(uri, v) for v in (1, 2, 3)] == [[1], [2], [3]]
    assert _ids_at(uri, 6) == [1]


def test_meta_table_compaction_keeps_the_versions_inside_seven_days(tmp_path):
    """LocalStore's compaction of a meta table every META_COMPACT_EVERY-th commit."""
    store = LocalStore(tmp_path)
    for i in range(3):
        store.meta_append("watermarks", [{"entity": "e", "value": str(i), "run_id": f"r{i}"}])
    uri = store.uri(META_LAKEHOUSE, "meta", "watermarks")
    _date(uri, [40, 35, 3])  # and version 3
    store._tidy_meta("watermarks", 100)
    assert len(DeltaTable(uri).file_uris()) == 1  # compacted
    for v in (1, 2, 3):
        assert DeltaTable(uri, version=v).to_pyarrow_table().num_rows == min(v, 2) + 1


def test_a_notebook_cant_set_the_log_retention_of_a_lakehouse_table(lake):  # noqa: F811
    """spark.sql ALTER TABLE ... SET TBLPROPERTIES fails and the table's properties are left as they were."""
    status, _text = run("spark.sql(\"ALTER TABLE dbo.sales SET TBLPROPERTIES "
                        "('delta.logRetentionDuration' = 'interval 1 days')\")")
    assert status == "error"
    assert "delta.logRetentionDuration" not in DeltaTable(str(lake / "dbo" / "sales")).metadata().configuration


def test_set_retention_with_log_cleanup_off_and_no_log_retention(tmp_path):
    uri = str(tmp_path / "t")
    _history(uri, {"delta.enableExpiredLogCleanup": "false"}, [3, 2])
    assert maintenance.set_retention(uri, 7) is True
    assert DeltaTable(uri).metadata().configuration["delta.logRetentionDuration"] == "interval 30 days"
