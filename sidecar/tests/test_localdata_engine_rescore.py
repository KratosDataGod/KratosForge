"""Mutation batch 152, a re-score of localdata/engine.py from batch 3 (docs/quality/mutation-testing.md):
the totals of an analyzed query's profile, Stop with nothing running, how often changed files are looked
for, a write's seconds and commits synced, appends to a partitioned table or one they don't fit, a MERGE
source's column names, and a table list that doesn't wait for a running query (fixed in #3045)."""

from __future__ import annotations

import time

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import engine as eng


@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.fixture
def engine(local):
    e = local.engine(local.create("Rescore")["id"])
    e.execute("CREATE TABLE sales USING DELTA AS SELECT 1 AS id, 10 AS amount")
    return e


def test_an_empty_profile_has_zero_totals():
    totals = eng._explained({}, "analyze")["totals"]
    assert totals == {"seconds": 0.0, "cpu_seconds": 0.0, "rows": 0, "rows_scanned": 0,
                      "peak_memory_bytes": 0, "spilled_bytes": 0, "bytes_read": 0}


def test_a_profiles_bytes_read_are_kept():
    assert eng._explained({"total_bytes_read": 4096}, "analyze")["totals"]["bytes_read"] == 4096


def test_cancelling_with_nothing_running_stops_nothing(engine):
    assert engine.cancel() is False


def test_a_look_for_changed_files_waits_two_seconds_after_the_last(engine, monkeypatch):
    looked = []
    monkeypatch.setattr(engine, "refresh", lambda force=False: looked.append(force))
    engine._last_check = time.monotonic()
    engine.refresh_if_changed()
    assert looked == []
    engine._last_check = time.monotonic() - eng._REFRESH_EVERY - 0.1
    engine.refresh_if_changed()
    assert looked == [False]


def test_a_write_says_how_long_it_took(engine):
    out = engine.write_delta("dbo.more", "SELECT 2 AS id")
    assert 0 <= out["seconds"] < 60


def test_an_append_that_doesnt_fit_suggests_schema_mode_merge(engine):
    with pytest.raises(ValueError, match="Use schema_mode merge to add new columns, or CREATE OR REPLACE"):
        engine.write_delta("dbo.sales", "SELECT 2 AS id, 20 AS amount, 'x' AS note", mode="append")
    with pytest.raises(ValueError) as err:
        engine.write_delta("dbo.sales", "SELECT 2 AS id, 20 AS amount, 'x' AS note", mode="overwrite")
    assert "schema_mode merge" not in str(err.value)


def test_an_append_to_a_partitioned_table_keeps_its_partitions(engine):
    engine.write_delta("dbo.parts", "SELECT 1 AS id, 'a' AS region", partition_by=["region"])
    out = engine.write_delta("dbo.parts", "SELECT 2 AS id, 'b' AS region", mode="append")
    assert out["version"] == 1
    from deltalake import DeltaTable

    assert DeltaTable(out["folder"]).metadata().partition_columns == ["region"]


def test_a_new_tables_commits_are_synced_from_version_zero(engine, monkeypatch):
    synced = []
    monkeypatch.setattr(eng.durable, "sync_commits", lambda folder, first, last: synced.append((first, last)))
    engine.write_delta("dbo.fresh", "SELECT 1 AS id")
    engine.write_delta("dbo.fresh", "SELECT 2 AS id", mode="append")
    assert synced == [(0, 0), (1, 1)]


def test_a_merge_source_may_have_a_column_name_a_table_couldnt(engine):
    out = engine.change_delta('MERGE INTO sales t USING (SELECT 1 AS id, 99 AS "new amount") s ON t.id = s.id '
                              'WHEN MATCHED THEN UPDATE SET amount = s."new amount"')
    assert out["rows_updated"] == 1
    assert engine.execute("SELECT amount FROM sales")["rows"] == [[99]]


def test_the_table_list_doesnt_wait_for_a_running_query(engine):
    import threading

    engine.open()
    listed = []
    engine._lock.acquire()  # as a running query holds it
    try:
        t = threading.Thread(target=lambda: listed.append(engine.tables()), daemon=True)
        t.start()
        t.join(5)
        assert listed and {x["table"] for x in listed[0]} >= {"sales"}
    finally:
        engine._lock.release()
        t.join(5)
