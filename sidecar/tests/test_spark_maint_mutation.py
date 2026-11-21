"""Mutation batch 53: how a notebook's OPTIMIZE, VACUUM and RESTORE are read, and Spark's answer shape.
Nothing here touches a table or Fabric."""

from datetime import datetime, timedelta, timezone

import pytest

from fabric_desktop import lakehouse, spark_maint


def test_restore_to_a_timestamp_reads_it_as_utc():
    got = spark_maint.statement("RESTORE TABLE dbo.sales TO TIMESTAMP AS OF '2026-11-01 13:00:00'")
    assert got == {"table": "dbo.sales", "operation": "restore",
                   "timestamp": datetime(2026, 11, 1, 13, 0, tzinfo=timezone.utc)}
    got = spark_maint.statement('RESTORE dbo.sales TIMESTAMP AS OF "2026-11-01"')
    assert got["timestamp"] == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_a_timestamp_with_its_own_offset_keeps_it():
    when = spark_maint.parse_timestamp("2026-11-01 13:00:00+02:00")
    assert when == datetime(2026, 11, 1, 13, 0, tzinfo=timezone(timedelta(hours=2)))
    assert when.utcoffset() == timedelta(hours=2)
    assert spark_maint.parse_timestamp(" 2026-11-01T13:00 ").tzinfo == timezone.utc


def test_a_timestamp_restore_cant_read_is_refused():
    with pytest.raises(ValueError, match="isn't a date and time RESTORE can read"):
        spark_maint.parse_timestamp("last tuesday")
    with pytest.raises(ValueError, match="isn't a date and time RESTORE can read"):
        spark_maint.statement("RESTORE dbo.sales TO TIMESTAMP AS OF 'soon'")


@pytest.mark.parametrize("where", ["region > 'US'", "region = other", "region = 'US' OR id = 1", "region"])
def test_optimize_where_takes_only_partition_column_equals_a_value(where):
    with pytest.raises(ValueError, match="takes partition column = value"):
        spark_maint.statement(f"OPTIMIZE dbo.sales WHERE {where}")


def test_optimize_where_with_and_gives_one_filter_per_column():
    got = spark_maint.statement("OPTIMIZE dbo.sales WHERE region = 'US' AND year = 2026 ZORDER BY (id, `day`)")
    assert got == {"table": "dbo.sales", "operation": "optimize", "where": [("region", "=", "US"), ("year", "=", "2026")],
                   "zorder": ["id", "day"]}


def _result(operation, metrics, files=()):
    return lakehouse.MaintenanceResult(table="dbo.sales", operation=operation, delta_version=3, metrics=metrics,
                                       files=list(files))


def test_answers_count_missing_metrics_as_zero():
    opt = spark_maint.answer(_result("optimize", {})).to_pylist()
    assert opt == [{"path": "dbo.sales", "metrics": {"numFilesAdded": 0, "numFilesRemoved": 0, "partitionsOptimized": 0,
                                                       "totalConsideredFiles": 0, "totalFilesSkipped": 0}}]
    assert spark_maint.answer(_result("restore", {})).to_pylist() == [{"num_removed_files": 0, "num_restored_files": 0}]


def test_answers_carry_the_metrics_delta_reported():
    m = {"numFilesAdded": 2, "numFilesRemoved": 5, "partitionsOptimized": 1, "totalConsideredFiles": 7,
         "totalFilesSkipped": 3}
    assert spark_maint.answer(_result("optimize", m)).to_pylist()[0]["metrics"] == m
    got = spark_maint.answer(_result("restore", {"numRemovedFile": 4, "numRestoredFile": 6})).to_pylist()
    assert got == [{"num_removed_files": 4, "num_restored_files": 6}]


def test_a_vacuum_dry_run_lists_files_and_a_real_one_answers_with_the_table():
    dry = spark_maint.answer(_result("vacuum", {"dryRun": True}, ["a.parquet", "b.parquet"])).to_pylist()
    assert dry == [{"path": "a.parquet"}, {"path": "b.parquet"}]
    real = spark_maint.answer(_result("vacuum", {"dryRun": False}, ["a.parquet"])).to_pylist()
    assert real == [{"path": "dbo.sales"}]


def _quickest(text, tries=5):
    import time

    best = float("inf")
    for _ in range(tries):
        started = time.perf_counter()
        try:
            spark_maint.statement(text)
        except ValueError:
            pass
        best = min(best, time.perf_counter() - started)
    return best


@pytest.mark.parametrize("build", [
    lambda n: "OPTIMIZE t WHERE x" + " " * n + "y",
    lambda n: "OPTIMIZE t ZORDER BY (a" + " " * n + "x",
    lambda n: "OPTIMIZE t" + " " * n + "y",
    lambda n: "VACUUM t RETAIN 1 HOURS" + " " * n + "y",
    lambda n: "RESTORE t TO VERSION AS OF 1" + " " * n + "y",
])
def test_a_long_run_of_blanks_is_read_once(build):
    # Split every way around the ";" and tried as the end of WHERE's text from inside, 800 blanks took
    # 0.8 s and the time grew with the cube. Read once, 8 times the blanks take about 8 times as long.
    assert _quickest(build(16000)) / max(_quickest(build(2000)), 50e-6) < 30


def test_where_and_zorder_by_still_end_where_they_did():
    assert spark_maint._OPTIMIZE.match("OPTIMIZE t WHERE  ").group("where") == " "
    assert spark_maint._OPTIMIZE.match("OPTIMIZE t ZORDER BY ( ) ;").group("zorder") == " "
    m = spark_maint._OPTIMIZE.match("OPTIMIZE t WHERE a = 1   ZORDER BY (b , c )  ; ")
    assert (m.group("where"), m.group("zorder")) == ("a = 1", "b , c ")
