"""Snowflake functions of a column whose type is known only when the query runs (SFX-030, SFX-031, SFN-034).

DuckDB has no TO_TIMESTAMP_LTZ or TO_TIMESTAMP_TZ, so a column stopped with its catalog error, and its LEFT, RIGHT
and SUBSTR take only text, so a timestamp, number or binary column stopped with a binder error. Expected values
are Snowflake's documented rules, quoted beside each test (https://docs.snowflake.com/en/sql-reference/...).
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf

_ROW = ("SELECT TIMESTAMP '2024-03-05 10:11:12.345' AS ts, TIMESTAMP '2024-03-05 10:11:12' AS whole, "
        "CAST('2024-03-05 10:11:12+00' AS TIMESTAMPTZ) AS tz, '2024-03-05 10:11:12' AS s, "
        "'2024-03-05 10:11:12 +02:00' AS s_offset, '1700000000000' AS s_ms, CAST(1700000000.5 AS DECIMAL(12, 1)) AS n, "
        "DATE '2024-03-05' AS d, 1700000000 AS i, CAST(100 AS DOUBLE) AS f, TIME '10:11:12.5' AS tm, "
        "CAST('ab' AS BLOB) AS b")


def _run(sql: str, zone: str = "America/New_York") -> list[tuple]:
    con = duckdb.connect()
    con.execute(f"SET TimeZone = '{zone}'")
    con.execute(f"CREATE TABLE t AS {_ROW}")
    return con.execute(sf.translate(sql).duckdb_sql).fetchall()


def test_to_timestamp_ltz_and_tz_of_a_column_read_it_by_its_type():
    # functions/to_timestamp: a number is "the number of seconds since the start of the Unix epoch (1970-01-01
    # 00:00:00 UTC)"; text holding an integer is read by its size; a value without a time zone (text, DATE,
    # TIMESTAMP_NTZ) is in the session's time zone (TIMEZONE), and a time zone written in the value is kept.
    for fn in ("TO_TIMESTAMP_LTZ", "TO_TIMESTAMP_TZ"):
        got = _run(f"SELECT CAST({fn}(ts) AS VARCHAR), CAST({fn}(tz) AS VARCHAR), CAST({fn}(s) AS VARCHAR), "
                   f"CAST({fn}(s_offset) AS VARCHAR), CAST({fn}(s_ms) AS VARCHAR), CAST({fn}(n) AS VARCHAR), "
                   f"CAST({fn}(d) AS VARCHAR), CAST({fn}(i) AS VARCHAR) FROM t")
        assert got == [("2024-03-05 10:11:12.345-05", "2024-03-05 05:11:12-05", "2024-03-05 10:11:12-05",
                        "2024-03-05 03:11:12-05", "2023-11-14 17:13:20-05", "2023-11-14 17:13:20.5-05",
                        "2024-03-05 00:00:00-05", "2023-11-14 17:13:20-05")], fn
        assert _run(f"SELECT {fn}(x) FROM (VALUES (CAST(NULL AS VARCHAR))) AS v(x)") == [(None,)]


def test_to_timestamp_ltz_of_a_column_is_an_instant_in_any_session_zone():
    utc = dt.timezone.utc
    got = _run("SELECT TO_TIMESTAMP_LTZ(i), TO_TIMESTAMP_LTZ(s) FROM t", zone="UTC")
    assert got == [(dt.datetime(2023, 11, 14, 22, 13, 20, tzinfo=utc), dt.datetime(2024, 3, 5, 10, 11, 12, tzinfo=utc))]


def test_to_timestamp_ltz_of_a_column_notes_the_session_time_zone():
    ids = [n.id for s in sf.translate("SELECT TO_TIMESTAMP_LTZ(c) FROM t").statements for n in s.notes]
    assert "SFD-004" in ids
    ids = [n.id for s in sf.translate("SELECT TO_TIMESTAMP_TZ(c) FROM t").statements for n in s.notes]
    assert {"SFD-002", "SFD-004"} <= set(ids)


def test_left_right_and_substr_cut_the_text_of_another_type():
    # data-type-conversion: NUMBER, FLOAT, DATE, TIME, TIMESTAMP and BOOLEAN are coerced to VARCHAR, as the
    # session's output formats write them: TIMESTAMP_NTZ_OUTPUT_FORMAT 'YYYY-MM-DD HH24:MI:SS.FF3',
    # TIMESTAMP_OUTPUT_FORMAT 'YYYY-MM-DD HH24:MI:SS.FF3 TZHTZM', TIME_OUTPUT_FORMAT 'HH24:MI:SS'; "When a FLOAT value
    # is cast to a VARCHAR value, trailing zeros are omitted". substr: 0 is 1, a negative start counts from the end.
    got = _run("SELECT LEFT(ts, 4), RIGHT(ts, 3), RIGHT(whole, 4), SUBSTR(d, 1, 4), SUBSTR(i, 2, 3), RIGHT(n, 2), "
               "RIGHT(tz, 5), RIGHT(f, 3), RIGHT(tm, 3), LEFT(TRUE, 2), SUBSTR(ts, 0, 4), SUBSTR(ts, -3), "
               "SUBSTRING(1.5, 1), LEFT(NULL, 1), LEFT(s, 4) FROM t")
    assert got == [("2024", "345", ".000", "2024", "700", ".5", "-0500", "100", ":12", "tr", "2024", "345", "1.5",
                    None, "2024")]


def test_left_of_a_text_value_is_left_as_it_was():
    assert "LIST_TRANSFORM" not in sf.translate("SELECT LEFT('abc', 2), RIGHT(UPPER(c), 1), SUBSTR(c::VARCHAR, 2) "
                                                "FROM t").duckdb_sql


def test_a_binary_value_is_refused_by_name():
    # substr: base_expr is "VARCHAR or BINARY", and a BINARY value gives BINARY, counted in bytes; DuckDB's text of a
    # BINARY value escapes its bytes, so the answer would differ.
    report = sf.translate("SELECT SUBSTR(TO_BINARY('abcd', 'HEX'), 1, 1)").statements[0]
    assert report.verdict == "not_translatable" and report.notes[0].id == "SFN-034"
    assert "HEX_ENCODE" in report.notes[0].reason
    with pytest.raises(duckdb.Error, match=r"Cutting a BINARY value with LEFT, RIGHT or SUBSTR isn't run on this PC "
                                           r"\(SFN-034\), so nothing ran\. Run it in Snowflake"):
        _run("SELECT LEFT(b, 1) FROM t")


@pytest.mark.parametrize("make", [
    lambda inner: f"TO_TIMESTAMP_LTZ(CAST(TO_TIMESTAMP_TZ({inner}) AS VARCHAR))",
    lambda inner: f"LEFT(LENGTH(SUBSTR({inner}, 1, 30)), 3)",
])
def test_the_rewrites_grow_the_sql_linearly(make):
    sizes = []
    for depth in range(1, 6):
        expression = "c"
        for _ in range(depth):
            expression = make(expression)
        sizes.append(len(sf.translate(f"SELECT {expression} FROM t").duckdb_sql))
    steps = [b - a for a, b in zip(sizes, sizes[1:], strict=False)]
    assert max(steps) <= 1.2 * min(steps), sizes


@pytest.mark.parametrize("sql", ["SELECT TO_TIMESTAMP_LTZ(RANDOM()) FROM t", "SELECT LEFT(RANDOM(), 3) FROM t"])
def test_the_value_is_read_once(sql):
    assert sf.translate(sql).duckdb_sql.upper().count("RANDOM()") == 1
