"""Two silent wrong answers from mutation testing, now as Spark 3.5.3 answers (checked there):

- from_json with a schema Spark can't read ('a INT, b', 'a INT b INT', 'a INT,', 'a: INT, b: STRING') stops,
  as Spark's does (AnalysisException); sqlglot read only the fields before the bad part, so b was dropped.
- TIMESTAMP '2026-13-01 00:00:00' stops in a session zone with clock changes too, not only in UTC (#3150):
  the zone pass read the literal through a TRY_CAST, so it was NULL in America/New_York.
"""
from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import spark_local, sql


def _translate(query: str) -> str:
    return sql.duckdb_sql(sql.parse(query, "spark")[0])


@pytest.mark.parametrize("schema", ["a INT, b", "a INT b INT", "a INT,", "s struct<x int y int>", "a: INT, b: STRING",
                                    "a array<int b>"])
def test_a_schema_spark_cant_read_stops(schema: str) -> None:
    with pytest.raises(ValueError, match="isn't a schema Spark reads: Spark stops on it, so nothing ran"):
        _translate(f"""SELECT from_json('{{"a": 1}}', '{schema}') AS j""")


@pytest.mark.parametrize("schema", ["a INT, b STRING", "a INT NOT NULL", "struct<a: int, b: string>",
                                    "a LONG, b ARRAY<STRUCT<x: INT, y: STRING>>", "MAP<STRING, INT>",
                                    "a DECIMAL(10,2), `b c` STRING"])
def test_a_schema_spark_reads_still_runs(schema: str) -> None:
    assert "LIST_TRANSFORM" in _translate(f"""SELECT from_json('{{"a": 1}}', '{schema}') AS j""")


def test_both_fields_are_kept() -> None:
    got = spark_local.session().sql("""SELECT from_json('{"a": 1, "b": "x"}', 'a INT, b STRING') AS j""").collect()
    assert got[0][0].asDict() == {"a": 1, "b": "x"}


@pytest.fixture
def new_york():
    spark = spark_local.session()
    spark.conf.set("spark.sql.session.timeZone", "America/New_York")
    yield spark
    spark.conf.unset("spark.sql.session.timeZone")


@pytest.mark.parametrize("literal", ["TIMESTAMP '2026-13-01 00:00:00'", "TIMESTAMP_NTZ '2026-13-01 00:00:00'",
                                     "TIMESTAMP '2026-02-30 10:00:00'"])
def test_a_bad_timestamp_literal_stops_in_a_zone_with_clock_changes(new_york, literal: str) -> None:
    with pytest.raises(Exception, match="out of range"):
        new_york.sql(f"SELECT {literal} AS t").collect()


def test_good_literals_and_casts_still_answer_as_spark_in_that_zone(new_york) -> None:
    rows = new_york.sql("SELECT TIMESTAMP '2026-03-08 02:30:00' AS gap, CAST('2026-13-01' AS TIMESTAMP) AS bad, "
                        "TIMESTAMP '2026-07-01 12:00:00' AS summer").collect()[0]
    assert _utc(rows["gap"]) == dt.datetime(2026, 3, 8, 7, 30)  # Spark: the hour the clock skips reads one later
    assert rows["bad"] is None  # a cast of text that isn't a timestamp is NULL with ANSI off
    assert _utc(rows["summer"]) == dt.datetime(2026, 7, 1, 16, 0)


def _utc(value: dt.datetime) -> dt.datetime:
    """The instant in UTC, without a zone: DuckDB 1.5 gives it in the session's zone, 2.0 in UTC."""
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None) if value.tzinfo else value
