"""spark_time_zone's own rules, read directly (mutation testing, batch 326).

test_spark_time_zone_cov2.py and test_spark_timezone_state.py cover the session's zone and a few rewrites;
these pin which offsets zone_id refuses and with which words, the gaps it finds in a zone, the interval split
for each unit, which casts are wrapped (and only once), and the earlier instant read on DuckDB in
America/Chicago, where 01:30 on 2024-11-03 is shown twice (06:30 and 07:30 UTC) and Spark takes the earlier.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest
import sqlglot

from fabric_desktop import spark_time_zone as stz
from fabric_desktop.sql import duckdb_sql

RANGE = "isn't a zone Spark reads"
WHOLE_HOURS = "can't be used on this PC"


@pytest.mark.parametrize(("zone", "words"), [
    ("+05:60", RANGE), ("+05:00:60", RANGE), ("+18:30", RANGE), ("+18:00:01", RANGE), ("+19", RANGE),
    ("-18:01", RANGE), ("+05:59", WHOLE_HOURS), ("+05:00:59", WHOLE_HOURS), ("+17:59:59", WHOLE_HOURS),
    ("+00:30", WHOLE_HOURS),
])
def test_an_offset_past_spark_s_range_and_one_with_minutes_are_refused_each_by_its_own_words(zone, words):
    with pytest.raises(ValueError, match=words):
        stz.zone_id(zone)


@pytest.mark.parametrize(("zone", "duckdb_zone"), [("+18", "+18"), ("+18:00", "+18:00"), ("-18", "-18"),
                                                   ("+5", "Etc/GMT-5"), ("UTC-03", "Etc/GMT+3")])
def test_an_offset_of_whole_hours_up_to_eighteen_is_read(zone, duckdb_zone):
    assert stz.zone_id(zone) == duckdb_zone


@pytest.mark.parametrize(("zone", "gaps"), [
    ("UTC", ()), ("Etc/GMT+8", ()),
    ("America/Chicago", (3600,)),
    ("Australia/Eucla", (3600,)),  # +08:45, +09:45 in summer: whole hours apart
    ("Australia/Lord_Howe", (3600, 1800)),  # moves by half an hour
    ("Africa/Cairo", (3600, 1800)),  # its local mean time (+02:05:09) ended in 1900
])
def test_a_zones_gaps_are_read_from_1900_to_2100(zone, gaps):
    stz.gaps.cache_clear()
    assert stz.gaps(zone) == gaps


def written(sql: str, shifting: tuple[int, ...] = (3600,)) -> str:
    return duckdb_sql(stz.fix(sqlglot.parse_one(sql, read="duckdb"), shifting))


@pytest.mark.parametrize("interval", ["24 HOUR", "24 HOURS", "1440 MINUTE", "1440 MINUTES", "86400 SECOND",
                                      "86400 SECONDS"])
def test_a_day_of_each_unit_is_a_calendar_day(interval):
    assert written(f"SELECT t + INTERVAL {interval} FROM x", ()) == \
        "SELECT t + (INTERVAL '1' DAY + INTERVAL '0' SECOND) FROM x"


def test_more_than_a_day_of_hours_is_split_and_an_interval_on_its_own_is_left():
    assert written("SELECT t + INTERVAL 25 HOURS FROM x", ()) == \
        "SELECT t + (INTERVAL '1' DAY + INTERVAL '3600' SECOND) FROM x"
    assert written("SELECT INTERVAL 25 HOURS AS i", ()) == "SELECT INTERVAL '25' HOURS AS i"


def test_one_gap_writes_one_earlier_instant_to_try():
    sql = written("SELECT CAST(s AS TIMESTAMPTZ) FROM x")
    assert "3600" in sql and "1800" not in sql
    both = written("SELECT CAST(s AS TIMESTAMPTZ) FROM x", (3600, 1800))
    assert "3600" in both and "1800" in both


def test_an_instant_is_not_wrapped():
    for sql in ["SELECT CAST(0 AS TIMESTAMPTZ)", "SELECT CAST('2024-11-03 01:30:00-05:00' AS TIMESTAMPTZ)",
                "SELECT CAST(CAST(0 AS TIMESTAMPTZ) AS TIMESTAMPTZ)"]:
        assert written(sql) == duckdb_sql(sqlglot.parse_one(sql, read="duckdb")), sql


def test_a_cast_of_text_is_wrapped_once():
    tree = sqlglot.parse_one("SELECT CAST(CAST(s AS VARCHAR) AS TIMESTAMPTZ) FROM x", read="duckdb")
    once = duckdb_sql(stz.fix(tree, (3600,)))
    assert "kf_w" in once
    assert duckdb_sql(stz.fix(tree, (3600,))) == once


@pytest.fixture(scope="module")
def chicago():
    con = duckdb.connect()
    con.execute("SET TimeZone = 'America/Chicago'")
    yield con
    con.close()


def utc(con, sql: str) -> dt.datetime:
    return con.execute(f"SELECT CAST(({sql}) AT TIME ZONE 'UTC' AS VARCHAR)").fetchone()[0]


def test_a_repeated_wall_clock_is_read_as_the_earlier_instant(chicago):
    assert utc(chicago, "SELECT CAST('2024-11-03 01:30:00' AS TIMESTAMPTZ)") == "2024-11-03 07:30:00"  # DuckDB's own
    assert utc(chicago, written("SELECT CAST('2024-11-03 01:30:00' AS TIMESTAMPTZ)")) == "2024-11-03 06:30:00"
    nested = "SELECT CAST(CAST(CAST('2024-11-03 01:30:00' AS TIMESTAMPTZ) AS VARCHAR) AS TIMESTAMPTZ)"
    assert utc(chicago, written(nested)) == "2024-11-03 06:30:00"
    assert utc(chicago, written("SELECT CAST(CAST('2024-11-03 01:30:00' AS VARCHAR) AS TIMESTAMPTZ)")) == \
        "2024-11-03 06:30:00"


def test_earlier_in_reads_the_earlier_of_two_instants_in_the_zone_it_names(chicago):
    later = "CAST('2024-11-03 07:30:00+00' AS TIMESTAMPTZ)"
    assert utc(chicago, written("SELECT " + stz.earlier_in("'America/Chicago'", later), ())) == "2024-11-03 06:30:00"
    once = "CAST('2024-11-03 12:00:00+00' AS TIMESTAMPTZ)"
    assert utc(chicago, written("SELECT " + stz.earlier_in("'America/Chicago'", once), ())) == "2024-11-03 12:00:00"
