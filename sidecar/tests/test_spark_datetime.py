"""Spark SQL's date and time functions on this PC, value-checked against Spark 3.5 (WR-106).

Each expected value is Spark's, ANSI off as Fabric runs it: Spark's datetime patterns
(https://spark.apache.org/docs/3.5.1/sql-ref-datetime-pattern.html, java.time's DateTimeFormatter in
Locale US), and the functions' documented results (sql-ref-functions-builtin). They run through the
notebook's spark.sql, the path a Fabric notebook's SQL takes here. What still differs is listed in
docs/sql-compatibility.md ("Dates and times on this PC"), and LOC-003 warns where a pattern is used.
None of this was run on a Fabric cluster: UNVERIFIED live.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel
from fabric_desktop.sqlcompat import analyze

DOCS = Path(__file__).resolve().parents[2] / "docs" / "sql-compatibility.md"

_RUN = """
import json, datetime
from zoneinfo import ZoneInfo
zone = {zone!r}
spark.conf.set('spark.sql.session.timeZone', zone or 'UTC')
out = []
for c in {cases!r}:
    try:
        r = spark.sql('SELECT ' + c + ' AS v')
        v = r.collect()[0][0]
        if isinstance(v, datetime.datetime) and r.dtypes[0][1] == 'timestamp':
            # Spark's timestamp as its wall time in the session's zone. DuckDB 1.x hands the zone back;
            # 2.x leaves it off, in this machine's zone, which astimezone reads for a naive value.
            v = v.astimezone(ZoneInfo(zone or 'UTC')).replace(tzinfo=None)
        out.append([r.dtypes[0][1], None if v is None else str(v)])
    except Exception as e:
        out.append(['error', str(e)])
print('RESULT' + json.dumps(out))
"""


@pytest.fixture(scope="module")
def kernel() -> Kernel:
    return Kernel()


def run(kernel: Kernel, cases: list[str], zone: str | None = None) -> list[tuple[str, str | None]]:
    out = kernel.run(_RUN.format(zone=zone, cases=cases))
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return [tuple(x) for x in json.loads(text.split("RESULT", 1)[1])]


def check(kernel: Kernel, table: list[tuple[str, str, str | None]], zone: str | None = None) -> None:
    got = run(kernel, [c for c, _, _ in table], zone)
    wrong = [(c, (t, v), g) for (c, t, v), g in zip(table, got, strict=True) if g != (t, v)]
    assert not wrong, "\n".join(f"{c}: want {w}, got {g}" for c, w, g in wrong)


TS = "TIMESTAMP'2024-02-29 13:04:05.123456'"

# ---- date_format: each pattern letter as java.time writes it (UTC session)
FORMATS = [
    (f"date_format({TS}, 'yyyy-MM-dd HH:mm:ss.SSS')", "string", "2024-02-29 13:04:05.123"),
    (f"date_format({TS}, 'y yy yyy yyyyy')", "string", "2024 24 2024 02024"),
    (f"date_format({TS}, 'M MM MMM MMMM L')", "string", "2 02 Feb February 2"),
    (f"date_format({TS}, 'd dd D DD DDD')", "string", "29 29 60 60 060"),
    (f"date_format({TS}, 'Q QQ QQQ QQQQ')", "string", "1 01 Q1 1st quarter"),
    (f"date_format({TS}, 'E EEE EEEE')", "string", "Thu Thu Thursday"),
    (f"date_format({TS}, 'h hh K k H HH a')", "string", "1 01 1 13 13 13 PM"),
    ("date_format(TIMESTAMP'2024-02-29 00:04:05', 'h K k H a')", "string", "12 0 24 0 AM"),
    (f"date_format({TS}, 'm mm s ss')", "string", "4 04 5 05"),
    (f"date_format({TS}, 'S SS SSSSSS SSSSSSSSS')", "string", "1 12 123456 123456000"),
    (f"date_format({TS}, \"yyyy-MM-dd'T'HH:mm:ssXXX\")", "string", "2024-02-29T13:04:05Z"),
    (f"date_format({TS}, 'X XX XXX x xx xxx Z ZZZZ ZZZZZ O OOOO VV')", "string",
     "Z Z Z +00 +0000 +00:00 +0000 GMT Z GMT GMT UTC"),
    (f"date_format({TS}, \"'it''s' G GGGG ''\")", "string", "it's AD Anno Domini '"),
    (f"date_format({TS}, \"yyyy/MM/dd, 'at' HH.mm\")", "string", "2024/02/29, at 13.04"),
    ("date_format(DATE'2024-02-29', 'dd/MM/yyyy HH:mm')", "string", "29/02/2024 00:00"),
    ("date_format('2024-02-29 13:04:05', 'MM-dd HH')", "string", "02-29 13"),
    ("date_format('not a date', 'MM-dd')", "string", None),
    ("date_format(CAST(NULL AS TIMESTAMP), 'MM-dd')", "string", None),
    ("from_unixtime(1709211845, 'yyyy QQQ HH:mm XXX')", "string", "2024 Q1 13:04 Z"),
    ("from_unixtime(1709211845)", "string", "2024-02-29 13:04:05"),
]


def test_date_format_writes_each_pattern_letter_as_spark(kernel):
    check(kernel, FORMATS)


# ---- the same in a session zone with daylight saving: offsets and wall times follow the zone
ZONED = [
    ("date_format(TIMESTAMP'2024-02-29 13:04:05', 'HH:mm XXX Z ZZZZ O xx VV')", "string",
     "13:04 -05:00 -0500 GMT-05:00 GMT-5 -0500 America/New_York"),
    ("date_format(TIMESTAMP'2024-07-01 13:04:05', 'HH:mm XXX O')", "string", "13:04 -04:00 GMT-4"),
    ("from_unixtime(1709211845, 'yyyy-MM-dd HH:mm:ss XXX')", "string", "2024-02-29 08:04:05 -05:00"),
    ("date_format(TIMESTAMP'2024-02-29 13:04:05+00:00', 'HH:mm')", "string", "08:04"),
    ("hour(TIMESTAMP'2024-02-29 02:00:00+00:00')", "int", "21"),
    ("to_date(TIMESTAMP'2024-02-29 02:00:00+00:00')", "date", "2024-02-28"),
    ("to_timestamp('2024-02-29T13:04:05Z')", "timestamp", "2024-02-29 08:04:05"),
    ("to_timestamp('2024-02-29 13:04:05')", "timestamp", "2024-02-29 13:04:05"),
    ("unix_timestamp('2024-02-29 13:04:05')", "bigint", "1709229845"),
    ("to_timestamp('29/02/2024 13:04', 'dd/MM/yyyy HH:mm')", "timestamp", "2024-02-29 13:04:00"),
    ("to_timestamp('2024-02-29 13:04:05 +05:30', 'yyyy-MM-dd HH:mm:ss XXX')", "timestamp", "2024-02-29 02:34:05"),
    ("from_utc_timestamp(TIMESTAMP'2024-02-29 13:00:00', 'Asia/Tokyo')", "timestamp", "2024-02-29 22:00:00"),
    ("to_utc_timestamp(TIMESTAMP'2024-02-29 13:00:00', 'Asia/Tokyo')", "timestamp", "2024-02-29 04:00:00"),
    ("make_timestamp(2024, 2, 29, 13, 4, 5)", "timestamp", "2024-02-29 13:04:05"),
    ("make_timestamp(2024, 2, 29, 13, 4, 5, 'Asia/Tokyo')", "timestamp", "2024-02-28 23:04:05"),
    ("date_trunc('DAY', TIMESTAMP'2024-02-29 02:00:00+00:00')", "timestamp", "2024-02-28 00:00:00"),
    ("CAST(TIMESTAMP'2024-03-10 02:30:00' AS STRING)", "string", "2024-03-10 03:30:00"),  # the clocks skip 2:00 to 3:00
]


def test_time_zones_follow_the_session_zone_as_in_spark(kernel):
    check(kernel, ZONED, zone="America/New_York")


# ---- reading text with a pattern
PARSED = [
    ("to_timestamp('2024-02-29 13:04:05.12', 'yyyy-MM-dd HH:mm:ss.SS')", "timestamp", "2024-02-29 13:04:05.120000"),
    ("to_timestamp('2024-02-29 13:04:05.123456', 'yyyy-MM-dd HH:mm:ss.SSSSSS')", "timestamp", "2024-02-29 13:04:05.123456"),
    ("to_timestamp('2024-02-29T13:04:05Z', \"yyyy-MM-dd'T'HH:mm:ssX\")", "timestamp", "2024-02-29 13:04:05"),
    ("to_timestamp('2024-02-29T13:04:05+05:30', \"yyyy-MM-dd'T'HH:mm:ssXXX\")", "timestamp", "2024-02-29 07:34:05"),
    # a Z in quoted text isn't an offset: only the Z standing on its own is read as +00:00
    ("to_timestamp('Zone 2024-02-29 13:04:05Z', \"'Zone' yyyy-MM-dd HH:mm:ssX\")", "timestamp", "2024-02-29 13:04:05"),
    ("to_timestamp('2024-02-29 13:04:05+0530', 'yyyy-MM-dd HH:mm:ssZ')", "timestamp", "2024-02-29 07:34:05"),
    ("to_timestamp('2024 060', 'yyyy DDD')", "timestamp", "2024-02-29 00:00:00"),
    ("to_timestamp('99-02-28', 'yy-MM-dd')", "timestamp", "2099-02-28 00:00:00"),  # Spark's base year is 2000
    ("to_timestamp('24-02-29', 'yy-MM-dd')", "timestamp", "2024-02-29 00:00:00"),
    ("to_timestamp('Thursday Feb 29 2024', 'EEEE MMM d yyyy')", "timestamp", "2024-02-29 00:00:00"),
    ("to_timestamp('February 29 2024 01:04 PM', 'MMMM d yyyy hh:mm a')", "timestamp", "2024-02-29 13:04:00"),
    ("to_timestamp('13:04', 'HH:mm')", "timestamp", "1970-01-01 13:04:00"),  # no year: 1970-01-01, as in Spark
    ("to_timestamp('2024-02-30', 'yyyy-MM-dd')", "timestamp", None),
    ("to_timestamp('2024-02-29 13:04:05 extra', 'yyyy-MM-dd HH:mm:ss')", "timestamp", None),
    ("to_date('29.02.2024', 'dd.MM.yyyy')", "date", "2024-02-29"),
    ("to_date('2024-02-29 13:04:05', 'yyyy-MM-dd')", "date", None),
    ("unix_timestamp('2024-02-29T13:04:05Z', \"yyyy-MM-dd'T'HH:mm:ssXXX\")", "bigint", "1709211845"),
    ("to_unix_timestamp('29/02/2024', 'dd/MM/yyyy')", "bigint", "1709164800"),
    ("to_timestamp_ltz('2024-02-29 13:04', 'yyyy-MM-dd HH:mm')", "timestamp", "2024-02-29 13:04:00"),
    ("try_to_timestamp('2024-02-29 13:04:05.1', 'yyyy-MM-dd HH:mm:ss.S')", "timestamp", "2024-02-29 13:04:05.100000"),
]


def test_patterns_read_text_as_spark_reads_it(kernel):
    check(kernel, PARSED)


# ---- result types: Spark's int, and Spark's timestamp (with time zone) where it gives one
TYPES = [
    ("year('2024-02-29')", "int", "2024"), ("month(DATE'2024-02-29')", "int", "2"),
    ("day(DATE'2024-02-29')", "int", "29"), ("dayofmonth(DATE'2024-02-29')", "int", "29"),
    ("dayofyear(DATE'2024-02-29')", "int", "60"), ("weekofyear(DATE'2024-12-30')", "int", "1"),
    ("dayofweek(DATE'2024-02-25')", "int", "1"), ("weekday(DATE'2024-02-25')", "int", "6"),
    ("quarter(DATE'2024-11-05')", "int", "4"), ("hour(TIMESTAMP'2024-02-29 13:04:05')", "int", "13"),
    ("minute(TIMESTAMP'2024-02-29 13:04:05')", "int", "4"), ("second(TIMESTAMP'2024-02-29 13:04:05')", "int", "5"),
    ("datediff('2024-03-01', '2024-02-28')", "int", "2"), ("unix_date(DATE'2024-02-29')", "int", "19782"),
    ("to_timestamp('2024-02-29 13:04:05')", "timestamp", "2024-02-29 13:04:05"),
    ("to_timestamp(1709211845)", "timestamp", "2024-02-29 13:04:05"),
    ("to_timestamp(DATE'2024-02-29')", "timestamp", "2024-02-29 00:00:00"),
    ("to_timestamp('2024-02-29 13:04:05 America/Los_Angeles')", "timestamp", "2024-02-29 21:04:05"),
    ("to_timestamp_ltz('2024-02-29 13:04:05')", "timestamp", "2024-02-29 13:04:05"),
    ("to_timestamp_ntz('2024-02-29 13:04:05')", "timestamp_ntz", "2024-02-29 13:04:05"),
    ("timestamp_seconds(1709211845)", "timestamp", "2024-02-29 13:04:05"),
    ("make_timestamp(2024, 2, 29, 13, 4, 5.5)", "timestamp", "2024-02-29 13:04:05.500000"),
    ("make_timestamp(2024, 2, 29, 23, 59, 60)", "timestamp", "2024-03-01 00:00:00"),
    ("from_utc_timestamp('2024-07-01 13:00:00', 'Europe/London')", "timestamp", "2024-07-01 14:00:00"),
    ("date_trunc('QUARTER', '2024-02-29 13:04:05')", "timestamp", "2024-01-01 00:00:00"),
    ("unix_timestamp(TIMESTAMP'2024-02-29 13:04:05')", "bigint", "1709211845"),
    ("timestampdiff(MONTH, TIMESTAMP'2024-01-31', TIMESTAMP'2024-02-29')", "bigint", "0"),
]


def test_result_types_are_sparks(kernel):
    check(kernel, TYPES)


# ---- values at the edges: month ends, the leap day, week starts
EDGES = [
    ("date_add(DATE'2024-02-28', 1)", "date", "2024-02-29"), ("date_add('2024-02-28', -60)", "date", "2023-12-30"),
    ("date_add(TIMESTAMP'2024-02-28 23:30:00', 1)", "date", "2024-02-29"),
    ("date_sub(DATE'2024-03-01', 1)", "date", "2024-02-29"),
    ("add_months(DATE'2024-01-31', 1)", "date", "2024-02-29"),
    ("add_months(DATE'2019-02-28', 1)", "date", "2019-03-28"),  # Spark 3 keeps the day, not the month's end
    ("add_months('2024-03-31', -1)", "date", "2024-02-29"),
    ("datediff('2024-03-01 01:00:00', '2024-02-28 23:00:00')", "int", "2"),
    ("months_between('1997-02-28 10:30:00', '1996-10-30')", "double", "3.94959677"),
    ("months_between('1997-02-28 10:30:00', '1996-10-30', false)", "double", "3.9495967741935485"),
    ("months_between(DATE'2024-03-31', DATE'2024-02-29')", "double", "1.0"),  # both a month's last day
    ("months_between(TIMESTAMP'2024-03-15 12:00:00', TIMESTAMP'2024-02-15 00:00:00')", "double", "1.0"),
    ("months_between(DATE'2024-01-01', DATE'2024-03-15')", "double", "-2.4516129"),
    ("trunc(DATE'2024-02-29', 'MM')", "date", "2024-02-01"), ("trunc(DATE'2024-02-29', 'mon')", "date", "2024-02-01"),
    ("trunc(DATE'2024-02-29', 'yy')", "date", "2024-01-01"), ("trunc(DATE'2024-02-29', 'QUARTER')", "date", "2024-01-01"),
    ("trunc(DATE'2024-02-29', 'WEEK')", "date", "2024-02-26"),
    ("trunc('2024-02-29 13:04:05', 'MM')", "date", "2024-02-01"),
    ("date_trunc('week', TIMESTAMP'2024-02-29 13:04:05')", "timestamp", "2024-02-26 00:00:00"),
    ("date_trunc('DD', TIMESTAMP'2024-02-29 13:04:05')", "timestamp", "2024-02-29 00:00:00"),
    ("date_trunc('MILLISECOND', TIMESTAMP'2024-02-29 13:04:05.123456')", "timestamp", "2024-02-29 13:04:05.123000"),
    ("last_day(DATE'2024-02-10')", "date", "2024-02-29"),
    ("next_day(DATE'2024-02-29', 'Mon')", "date", "2024-03-04"), ("next_day(DATE'2024-02-26', 'MO')", "date", "2024-03-04"),
    ("next_day('2024-02-26', 'monday')", "date", "2024-03-04"), ("next_day(DATE'2024-02-25', 'SUN')", "date", "2024-03-03"),
    ("make_date(2024, 2, 29)", "date", "2024-02-29"),
]


def test_dates_at_month_ends_leap_days_and_week_starts(kernel):
    check(kernel, EDGES)


# ---- NULL where Spark (ANSI off) gives NULL, not an error
NULLS = [
    ("date_add('not a date', 1)", "date", None), ("date_sub('not a date', 1)", "date", None),
    ("datediff('2024-03-01', 'not a date')", "int", None), ("last_day('not a date')", "date", None),
    ("year('not a date')", "int", None), ("add_months('not a date', 1)", "date", None),
    ("next_day(DATE'2024-02-29', 'xx')", "date", None), ("next_day(DATE'2024-02-29', 'MONX')", "date", None),
    ("trunc(DATE'2024-02-29', 'DD')", "date", None), ("trunc(DATE'2024-02-29', 'bogus')", "date", None),
    ("date_trunc('bogus', TIMESTAMP'2024-02-29 13:04:05')", "timestamp", None),
    ("date_trunc('HOUR', 'not a date')", "timestamp", None),
    ("make_date(2023, 2, 29)", "date", None), ("make_timestamp(2024, 2, 30, 0, 0, 0)", "timestamp", None),
    ("to_timestamp('garbage')", "timestamp", None), ("to_date('2024-02-30')", "date", None),
    ("unix_timestamp('nope')", "bigint", None),
]


def test_bad_values_give_null_as_spark_does_with_ansi_off(kernel):
    check(kernel, NULLS)


# ---- what Spark refuses is refused here by name; what this PC can't read is refused, never guessed
REFUSED = [
    ("date_format(DATE'2024-02-29', 'YYYY')", "'YYYY'", "week-based"),
    ("date_format(DATE'2024-02-29', 'ww')", "'ww'", "week-based"),
    ("date_format(DATE'2024-02-29', 'u')", "'u'", "week-based"),
    ("date_format(DATE'2024-02-29', 'e')", "'e'", "week-based"),
    ("date_format(DATE'2024-02-29', 'zzzz')", "'zzzz'", "time zone names"),
    ("date_format(DATE'2024-02-29', 'F')", "'F'", "Spark refuses"),
    ("date_format(DATE'2024-02-29', '[yyyy]')", "'['", "optional sections"),
    ("date_format(DATE'2024-02-29', 'ddd')", "'ddd'", "too many"),
    # Spark stops on five of a text letter and on seven or more y: "Too many pattern letters".
    *[(f"date_format(DATE'2024-02-29', '{r}')", f"'{r}'", f"(\"Too many pattern letters: {r[0]}\")")
      for r in ("GGGGG", "MMMMM", "LLLLL", "EEEEE", "QQQQQ", "qqqqq", "yyyyyyy", "yyyyyyyyyy")],
    ("to_timestamp('2024', 'yyyyyyy')", "'yyyyyyy'", "Spark stops on it too"),
    ("date_format(DATE'2024-02-29', 'b')", "'b'", "isn't a pattern letter"),
    ("date_format(DATE'2024-02-29', 'yyyy at')", "'t'", "isn't a pattern letter"),  # Spark: unknown pattern letter
    ("date_format(DATE'2024-02-29', \"'open\")", "'open", "isn't closed"),
    ("to_timestamp('2024-02-29', 'YYYY-MM-dd')", "'YYYY'", "week-based"),
    ("to_date('2024 Q1', 'yyyy QQQ')", "'QQQ'", "no reading here"),
    ("unix_timestamp('AD 2024', 'G yyyy')", "'G'", "no reading here"),
    ("to_timestamp('24:00', 'kk:mm')", "'kk'", "no reading here"),
]


@pytest.mark.parametrize("case, run_text, why", REFUSED, ids=[c for c, _, _ in REFUSED])
def test_refused_patterns_say_which_letters(case, run_text, why):
    with pytest.raises(ValueError) as caught:
        sql.duckdb_sql(sql.parse(f"SELECT {case}")[0])
    assert run_text in str(caught.value) and why in str(caught.value) and "nothing ran" in str(caught.value)


def test_databricks_sql_keeps_ansi_errors_with_the_same_patterns():
    from fabric_desktop.engines.local_sql import _databricks
    import duckdb
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    formatted = sql.duckdb_sql(_databricks(f"SELECT date_format({TS}, 'yyyy-MM-dd HH:mm:ss.SSS Q')")[-1])
    assert con.execute(formatted).fetchone()[0] == "2024-02-29 13:04:05.123 1"
    for query in ("SELECT to_timestamp('2024-02-30', 'yyyy-MM-dd')", "SELECT date_format('nope', 'yyyy')"):
        with pytest.raises(duckdb.Error):  # ANSI: text that isn't a timestamp stops the query
            con.execute(sql.duckdb_sql(_databricks(query)[-1])).fetchall()


STILL_DIFFERS = {
    "a year or a year and month": "SELECT CAST('2024-02' AS DATE)",
    "one digit where the pattern has two": "SELECT to_date(s, 'yyyy-MM-dd') FROM t",
    "timeParserPolicy": "SELECT to_date(s, 'yyyy-MM-dd') FROM t",
}


def test_what_still_differs_is_listed_in_the_compatibility_docs():
    text = DOCS.read_text(encoding="utf-8")
    section = text.split("## Dates and times on this PC", 1)[1].split("\n## ", 1)[0]
    for words in STILL_DIFFERS:
        assert words in section, words


@pytest.mark.parametrize("case", [c for c, q in STILL_DIFFERS.items() if "to_" in q])
def test_the_static_check_warns_where_a_pattern_is_read(case):
    hits = [d for d in analyze(STILL_DIFFERS[case], "spark")["diagnostics"] if d["rule"] == "LOC-003"]
    assert hits, case
