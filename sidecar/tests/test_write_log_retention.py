"""Ordinary writes keep the Delta log that time travel inside the history kept reads, as maintenance does.

delta-rs 1.6.6 cleans the log at every commit that writes a checkpoint, (version + 1) a multiple of
delta.checkpointInterval, OPTIMIZE or not: an append, overwrite, MERGE, UPDATE or DELETE on that version deleted
the log of the version current at the start of the history kept once it was older than delta.logRetentionDuration.
Each write here lands on a checkpoint version (an interval of 5) with that version 35 days old and current until 2
days ago, and the real cleanup runs (it still deletes the 100-day-old version 0's log): no mocks of it. Commits are dated as test_maintenance_log_retention does.
"""

import os
import shutil
import time
from pathlib import Path

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, maintenance
from fabric_desktop.localdata import LocalData
from fabric_desktop.meta.store import LocalStore
from tests.test_maintenance_log_retention import _ids, _ids_at
from tests.test_spark_writer import lake, run  # noqa: F401 - a fixture
from tests.test_vacuum_retention_boundaries import _stamp

DAY = 24 * 3600
INTERVAL = {"delta.checkpointInterval": "5"}
CHECKPOINT = 4  # the version a write lands on after _history's: (4 + 1) % 5 == 0


def _history(uri: str, ages_days: list[float], config: dict[str, str] | None = None) -> None:
    """Version v holds id v (each an overwrite), dated ``ages_days[v]`` days ago, each with a checkpoint of that
    date as a table checkpointed at every commit has; then version 3, a day ago, sets a checkpoint interval of 5
    (KEEP_LOG, so it cleans nothing), so the next write (version 4) checkpoints and can clean the log."""
    for v in range(len(ages_days)):
        write_deltalake(uri, pa.table({"id": [v], "region": ["EU"]}), mode="overwrite",
                        configuration=(config or None) if v == 0 else None)
    for v, days in enumerate(ages_days):
        _stamp(uri, v, days * DAY)
        DeltaTable(uri, version=v).create_checkpoint()
        when = time.time() - days * DAY
        os.utime(Path(uri) / "_delta_log" / f"{v:020d}.checkpoint.parquet", (when, when))
    DeltaTable(uri).alter.set_table_properties(INTERVAL, post_commithook_properties=maintenance.KEEP_LOG)
    _stamp(uri, len(ages_days), DAY)


def _log(uri: str) -> set[str]:
    return {p.name for p in (Path(uri) / "_delta_log").iterdir()}


AGES = [100, 35, 2]  # and version 3, a day ago


def _kept(uri: str, cleaned: bool = True) -> None:
    """Version 1, written 35 days ago and current until 2 days ago, still loads; a time travel to 3 days ago
    reads it; the write's version checkpointed; and (``cleaned``) the log cleanup ran: version 0's log, 100 days
    old and before the history kept, is gone."""
    assert f"{CHECKPOINT:020d}.checkpoint.parquet" in _log(uri)
    assert ({f"{0:020d}.json", f"{0:020d}.checkpoint.parquet"} & _log(uri) == set()) is cleaned
    assert _ids(uri, 1) == [1] and _ids(uri, 2) == [2]
    assert _ids_at(uri, 3) == [1]


ROW = pl.DataFrame({"id": [9], "region": ["EU"]})
LH = "LH_Silver"


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "store")
    uri = s.uri(LH, "dbo", "t")
    Path(uri).mkdir(parents=True)
    _history(uri, AGES)
    return s, uri


# ---- the meta engine's writes (LocalStore; Store.write, write_stream, upsert and merge_insert are OneLake's too)

STORE_WRITES = {
    "append": lambda s: s.write(LH, "dbo", "t", ROW, mode="append"),
    "overwrite": lambda s: s.write(LH, "dbo", "t", ROW, mode="overwrite"),
    "overwrite at the version read": lambda s: s.write(LH, "dbo", "t", ROW, mode="overwrite", read_version=3),
    "write_stream": lambda s: s.write_stream(LH, "dbo", "t", pa.RecordBatchReader.from_batches(
        ROW.to_arrow().schema, ROW.to_arrow().to_batches()), read_version=3),
    "upsert": lambda s: s.upsert(LH, "dbo", "t", ROW, on=["id"], read_version=3),
    "upsert without a version": lambda s: s.upsert(LH, "dbo", "t", ROW, on=["id"]),
    "merge_insert": lambda s: s.merge_insert(LH, "dbo", "t", ROW, on=["id"], version=3),
}


@pytest.mark.parametrize("how", STORE_WRITES)
def test_a_store_write_on_a_checkpoint_version_keeps_the_version_current_at_the_start_of_the_history_kept(store, how):
    s, uri = store
    STORE_WRITES[how](s)
    assert 9 in _ids(uri, CHECKPOINT)  # the write is version 4, the checkpoint version
    _kept(uri)


def test_a_layers_longer_keep_history_is_kept_by_a_write(tmp_path):
    """Store.history_days (the layer's retention_days, set by the Engine): version 1, written 70 days ago, is
    current at the start of a 60-day history and stays (with 7 days kept, its log would be cleaned)."""
    s = LocalStore(tmp_path / "store")
    uri = s.uri(LH, "dbo", "t")
    Path(uri).mkdir(parents=True)
    _history(uri, [100, 70, 50, 2])  # and version 4, the interval
    s.history_days = {LH: 60}
    for _ in range(5):  # versions 5 to 9: 9 checkpoints
        s.write(LH, "dbo", "t", ROW, mode="append")
    assert f"{9:020d}.checkpoint.parquet" in _log(uri)
    assert _ids(uri, 1) == [1] and _ids_at(uri, 55) == [1] and _ids_at(uri, 10) == [2]


def test_the_engine_gives_the_store_each_layers_keep_history(tmp_path):
    from fabric_desktop.meta.engine import Engine
    from fabric_desktop.meta.project import Layer

    class P:
        layers = [Layer("bronze", retention_days=30), Layer("silver", retention_days=90)]
        entities: dict = {}

        def delta_policies(self):
            return {}
    s = LocalStore(tmp_path / "store")
    Engine(P(), s)  # type: ignore[arg-type]
    assert s.history_days == {"LH_Bronze": 30, "LH_Silver": 90}


def test_the_log_before_the_history_kept_is_still_cleaned_by_a_write(tmp_path):
    """Only what the history kept reads is kept: an append that checkpoints deletes versions 0 and 1's log,
    replaced 40 days ago, and keeps version 2's checkpoint on, from which version 2 loads."""
    s = LocalStore(tmp_path / "store")
    uri = s.uri(LH, "dbo", "t")
    Path(uri).mkdir(parents=True)
    _history(uri, [100, 95, 40])
    s.write(LH, "dbo", "t", ROW, mode="append")  # version 4
    log = _log(uri)
    assert not {f"{v:020d}.{kind}" for v in (0, 1) for kind in ("json", "checkpoint.parquet")} & log
    assert f"{2:020d}.checkpoint.parquet" in log
    assert _ids(uri, 2) == [2] and _ids_at(uri, 6) == [2]
    assert maintenance.log_seconds(DeltaTable(uri).metadata().configuration) == 60 * DAY


def test_a_write_that_doesnt_checkpoint_doesnt_list_the_log(store, monkeypatch):
    """keep_log (the listing) and the cleanup run only after a write on a checkpoint version."""
    s, uri = store
    calls: list = []
    monkeypatch.setattr(maintenance, "keep_log", lambda *a, **k: calls.append("keep_log"))
    monkeypatch.setattr(DeltaTable, "cleanup_metadata", lambda self: calls.append("cleanup"))
    DeltaTable(uri).alter.set_table_properties({"delta.checkpointInterval": "10"},
                                               post_commithook_properties=maintenance.KEEP_LOG)  # version 4
    s.write(LH, "dbo", "t", ROW, mode="append")  # versions 5 to 8: none checkpoints
    s.write(LH, "dbo", "t", ROW, mode="overwrite", read_version=5)
    s.upsert(LH, "dbo", "t", ROW, on=["id"], read_version=6)
    s.merge_insert(LH, "dbo", "t", pl.DataFrame({"id": [10], "region": ["EU"]}), on=["id"], version=7)
    assert DeltaTable(uri).version() == 8 and calls == []
    s.write(LH, "dbo", "t", ROW, mode="append")  # version 9 checkpoints
    assert calls == ["keep_log", "cleanup"]
    assert f"{9:020d}.checkpoint.parquet" in _log(uri)


def test_a_keep_log_that_fails_leaves_the_log_and_the_write_stands(store, monkeypatch):
    """Fail-safe: the write is in, and nothing of the log is cleaned until the next checkpoint."""
    s, uri = store

    def broken(*args, **kwargs):
        raise OSError("listing refused")
    monkeypatch.setattr(maintenance, "keep_log", broken)
    assert s.write(LH, "dbo", "t", ROW, mode="append") == CHECKPOINT
    assert {f"{v:020d}.json" for v in range(5)} <= _log(uri)
    assert _ids(uri, 0) == [0]
    _kept(uri, cleaned=False)


@pytest.mark.parametrize("config, after, checkpoints", [
    ({}, 98, False), ({}, 99, True), ({"delta.checkpointInterval": "5"}, 3, False),
    ({"delta.checkpointInterval": "5"}, 4, True), ({"delta.checkpointInterval": "abc"}, 3, True),
    ({"delta.checkpointInterval": "0"}, 3, True), ({"delta.checkpointInterval": " 1 "}, 3, True)])
def test_checkpoints_between_reads_the_tables_interval(config, after, checkpoints):
    assert maintenance.checkpoints_between(config, after - 1, after) is checkpoints


def test_checkpoints_between_counts_every_commit_since_the_version_before():
    """Another writer's commit in between can be the checkpoint one; a new table counts from version 0."""
    five = {"delta.checkpointInterval": "5"}
    assert maintenance.checkpoints_between(five, 3, 6) is True
    assert maintenance.checkpoints_between(five, 4, 8) is False
    assert maintenance.checkpoints_between(five, None, 4) is True
    assert maintenance.checkpoints_between(five, None, 3) is False


# ---- a notebook's writes: write_table (publish), saveAsTable, and MERGE / UPDATE / DELETE (change_table)

NOTEBOOK = {
    "write_table": lambda: lakehouse.write_table(ROW, "dbo.hist", mode="append"),
    "write_table overwrite": lambda: lakehouse.write_table(ROW, "dbo.hist", mode="overwrite"),
    "saveAsTable": lambda: run("df.write.mode('append').saveAsTable('dbo.hist')"),
    "UPDATE": lambda: run("spark.sql(\"UPDATE dbo.hist SET id = 9 WHERE id = 2\")"),
    "MERGE": lambda: run("spark.sql(\"MERGE INTO dbo.hist AS t USING (SELECT 9 AS id, 'EU' AS region) AS s "
                         "ON t.id = s.id WHEN NOT MATCHED THEN INSERT *\")"),
}


@pytest.mark.parametrize("how", NOTEBOOK)
def test_a_notebook_write_on_a_checkpoint_version_keeps_the_version_current_at_the_start_of_the_history_kept(
        lake, how):  # noqa: F811
    uri = str(lake / "dbo" / "hist")
    _history(uri, AGES)
    out = NOTEBOOK[how]()
    if isinstance(out, tuple):
        assert out[0] == "ok", out[1]
    assert 9 in _ids(uri, CHECKPOINT)
    _kept(uri)


def test_a_notebook_delete_on_a_checkpoint_version_keeps_the_history(lake):  # noqa: F811
    uri = str(lake / "dbo" / "hist")
    _history(uri, AGES)
    status, text = run("spark.sql(\"DELETE FROM dbo.hist WHERE id = 2\")")
    assert status == "ok", text
    assert _ids(uri, CHECKPOINT) == []
    _kept(uri)


# ---- the Local data page's Delta tables (INSERT INTO / CREATE TABLE ... USING DELTA, MERGE / UPDATE / DELETE)

@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.mark.parametrize("sql", ["INSERT INTO hist SELECT 9 AS id, 'EU' AS region",
                                 "UPDATE hist SET id = 9 WHERE id = 2"])
def test_a_local_data_write_on_a_checkpoint_version_keeps_the_history(local, sql):
    e = local.engine(local.create("Log")["id"])
    e.execute("CREATE TABLE hist USING DELTA AS SELECT 0::BIGINT AS id, 'EU' AS region")
    uri = str(e.tables_dir / "dbo" / "hist")
    _restart(uri)
    e.execute(sql)
    assert 9 in _ids(uri, CHECKPOINT)
    _kept(uri)


def _restart(uri: str) -> None:
    """The Local data table, made by its CREATE TABLE, rebuilt in place with _history's versions."""
    shutil.rmtree(Path(uri) / "_delta_log")
    for p in Path(uri).glob("*.parquet"):
        p.unlink()
    _history(uri, AGES)
