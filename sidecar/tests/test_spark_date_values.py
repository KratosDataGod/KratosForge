"""Spark 3.5's date and time functions on this PC, value-checked against Spark 3.5.3 (see spark_values.py).

The queries in tools/spark_value_cases/dates.txt, each answered by Spark 3.5.3 in
tests/fixtures/spark_3_5_3_dates.json: a date plus a month, trunc's units, unix_seconds, the zone's name in
date_format, the minute of a time of day, and a zone written as GMT+2. What this PC can't answer as Spark
does is refused by name below, and listed in docs/sql-compatibility.md. The tests after it check the
neighbouring cases by Spark 3.5's rules (DateTimeUtils, java.time's ZoneId), not by a Spark run."""

from __future__ import annotations

import datetime as dt

import pytest
from spark_values import Refused, check_part, check_still_needed, parts

from fabric_desktop import spark_datetime
from fabric_desktop.notebook import Kernel

REFUSED = [
    Refused("year-month interval", r"make_ym_interval\(", r"no year-month interval type",
            "`make_ym_interval`"),
]


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def one(spark, query: str) -> tuple:
    """The first row; a timestamp without its zone, which DuckDB 2.0 leaves off (the session's is UTC)."""
    return tuple(v.replace(tzinfo=None) if isinstance(v, dt.datetime) else v for v in spark.sql(query).collect()[0])


@pytest.mark.parametrize("cases", parts("dates"))
def test_spark_date_functions_answer_as_spark_3_5_or_are_refused_by_name(spark, cases):
    check_part(spark, cases, REFUSED)


def test_each_refusal_of_dates_is_still_needed_and_listed(spark):
    check_still_needed(spark, "dates", REFUSED)


def test_a_date_plus_years_months_weeks_or_days_is_a_date_and_plus_hours_a_timestamp(spark):
    got = one(spark, "SELECT DATE'2024-01-31' - INTERVAL 1 MONTH, INTERVAL 1 YEAR + DATE'2024-02-29', "
                     "DATE'2024-01-31' + INTERVAL 1 WEEK, to_date('2024-01-31') + INTERVAL 2 DAYS, "
                     "DATE'2024-01-31' + INTERVAL 1 MONTH 2 DAY, DATE'2024-01-31' + INTERVAL 3 HOURS")
    assert got == (dt.date(2023, 12, 31), dt.date(2025, 2, 28), dt.date(2024, 2, 7), dt.date(2024, 2, 2),
                   dt.date(2024, 3, 2), dt.datetime(2024, 1, 31, 3, 0))


def test_a_date_column_plus_a_month_is_a_date_and_a_timestamp_column_stays_a_timestamp(spark):
    df = spark.sql("SELECT d + INTERVAL 1 MONTH AS a, (d + INTERVAL 1 MONTH) + INTERVAL 1 DAY AS b, "
                   "ts + INTERVAL 1 MONTH AS c FROM VALUES (DATE'2024-01-31', TIMESTAMP'2024-01-31 10:00:00') AS v(d, ts)")
    assert df.schema.simpleString() == "struct<a:date,b:date,c:timestamp>"
    assert tuple(df.collect()[0])[:2] == (dt.date(2024, 2, 29), dt.date(2024, 3, 1))


def test_trunc_and_date_trunc_give_null_for_a_unit_written_as_one_letter(spark):
    # Spark's parseTruncLevel takes QUARTER, DAY, HOUR...; sqlglot spells out Q, Y, D, H, M as them
    assert one(spark, "SELECT trunc(DATE'2024-05-17', 'q'), trunc(DATE'2024-05-17', 'Y'), "
                      "date_trunc('D', TIMESTAMP'2024-05-17 13:45:12'), date_trunc('M', TIMESTAMP'2024-05-17 13:45:12'), "
                      "trunc(DATE'2024-05-17', 'QUARTER'), trunc(DATE'2024-05-17', 'mm')") == (
        None, None, None, None, dt.date(2024, 4, 1), dt.date(2024, 5, 1))


def test_unix_seconds_and_unix_millis_round_down_before_1970_too(spark):
    assert one(spark, "SELECT unix_seconds(TIMESTAMP'1969-12-31 23:59:59.5'), "
                      "unix_millis(TIMESTAMP'1969-12-31 23:59:59.9995'), unix_millis(TIMESTAMP'2024-05-17 13:45:12.9999'), "
                      "unix_micros(TIMESTAMP'1969-12-31 23:59:59.5'), unix_seconds(NULL)") == (
        -1, -1, 1715953512999, -500000, None)


def test_hour_minute_and_second_read_text_that_is_a_time_of_day(spark):
    assert one(spark, "SELECT hour('13:45:12'), second('13:45:12.5'), hour('T09:08'), minute(' 13:45 '), "
                      "hour('2024-05-17 13:45:12'), hour('x'), hour('25:00:00')") == (13, 12, 9, 45, 13, None, None)


def test_nested_hour_minute_and_second_read_their_argument_once():
    # the text branch names its argument several times: bound once, nesting stays linear
    import time

    from fabric_desktop import sql
    query = "t"
    for fn in ("second", "minute", "hour") * 2:  # six deep: tens of seconds when its SQL multiplied
        query = f"{fn}({query})"
    start = time.perf_counter()
    got = sql.run(f"SELECT {query} n, hour(upper(t)) h FROM VALUES ('13:45:12'), ('x') v(t)").to_pylist()
    assert time.perf_counter() - start < 10
    assert got == [{"n": None, "h": 13}, {"n": None, "h": None}]


@pytest.mark.parametrize("zone", ["+18:01", "-19", "UTC+18:00:01", "GMT+05:60"])
def test_an_offset_past_18_hours_is_refused_with_that_reason(zone):
    from fabric_desktop import sql
    with pytest.raises(Exception, match=r"An offset is hours and minutes from -18:00 to \+18:00"):
        sql.run(f"SELECT from_utc_timestamp(TIMESTAMP'2024-05-17 12:00:00', '{zone}')")


def test_from_and_to_utc_timestamp_take_a_zone_written_as_an_offset(spark):
    assert one(spark, "SELECT to_utc_timestamp(TIMESTAMP'2024-05-17 12:00:00', 'GMT+2'), "
                      "from_utc_timestamp(TIMESTAMP'2024-05-17 12:00:00', 'UTC+05:30'), "
                      "from_utc_timestamp('2024-05-17 12:00:00', '-08:00'), "
                      "from_utc_timestamp(TIMESTAMP'2024-05-17 12:00:00', 'Asia/Tokyo')") == tuple(
        dt.datetime(2024, 5, 17, h, m) for h, m in ((10, 0), (17, 30), (4, 0), (21, 0)))


@pytest.mark.parametrize(("zone", "seconds"), [
    ("GMT+2", 7200), ("UTC+05:30", 19800), ("UT-3", -10800), ("+0530", 19800), ("-1:30", -5400), ("Z", 0),
    ("+02:00:30", 7230), ("+18:00", 64800), ("+18:01", None), ("+19", None), ("GMT+123", None), ("gmt+2", None),
    ("UTC", None), ("Europe/Paris", None)])
def test_a_zone_is_a_fixed_offset_as_java_reads_one(zone, seconds):
    assert spark_datetime.fixed_offset(zone) == seconds


def test_date_format_names_the_utc_zone_and_stops_by_name_for_another_zone():
    spark = Kernel().namespace["spark"]
    assert one(spark, "SELECT date_format(TIMESTAMP'2024-05-17 13:05:02', 'yyyy-MM-dd HH:mm zzz')") == (
        "2024-05-17 13:05 UTC",)
    spark.conf.set("spark.sql.session.timeZone", "Europe/Paris")
    with pytest.raises(Exception, match="can't use 'z' in a pattern, so nothing ran"):
        spark.sql("SELECT date_format(TIMESTAMP'2024-05-17 13:05:02', 'z')").collect()
    with pytest.raises(ValueError, match="can't use 'zzzz' in a pattern"):
        spark.sql("SELECT date_format(TIMESTAMP'2024-05-17 13:05:02', 'zzzz')").collect()
