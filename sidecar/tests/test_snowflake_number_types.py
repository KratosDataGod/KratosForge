"""Snowflake SQL on this PC answers with Snowflake's types for the gaps PR #3171's parity cases found
(rules 2026.11.21): NUMBER division with Snowflake's scale (SFX-053), whole-number arithmetic as NUMBER
(SFX-052), SUBSTR with a start before the string (SFX-050), and DATEADD and DATE_TRUNC of a table's DATE
column (SFX-051). Each test checks the value and the result type."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pyarrow as pa
import pytest

from fabric_desktop import snowflake_sql as sf
from fabric_desktop.engines import registry
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture
def session():
    s = registry.adapter("local-duckdb").open_session(SessionConfig("nb"))
    create = ("CREATE TABLE t (a INT, b INT, d1 NUMBER(10,2), d2 NUMBER(10,2), s VARCHAR, dday DATE, "
              "f DOUBLE, ts TIMESTAMP_NTZ, big NUMBER(38,0))")
    insert = ("INSERT INTO t VALUES (7, 2, 1.00, 3.00, 'Spark SQL', '2023-01-31', 2.0, "
              "'2024-05-09 08:50:57', 10), (-7, 2, 10.10, 2.10, 'mystring', '2024-05-09', 4.0, NULL, 0)")
    for statement in (create, insert):
        assert s.execute(CellRequest(statement, "sql", "snowflake")).status == "ok"
    yield s
    s.close()


def run(session, code: str) -> pa.Table:
    result = session.execute(CellRequest(code, "sql", "snowflake", result_rows=100))
    assert result.status == "ok", result.outputs
    assert result.table is not None
    return result.table


def labels(session, code: str) -> list[str]:
    result = session.execute(CellRequest(code, "sql", "snowflake", result_rows=100))
    return [n["id"] for st in result.outputs[0]["statements"] for n in st["notes"]]


def error(session, code: str) -> str:
    result = session.execute(CellRequest(code, "sql", "snowflake"))
    assert result.status == "error", result.outputs
    return str(result.outputs[-1]["evalue"])


# ---- 1. division of NUMBERs (operators-arithmetic: L = L1 + S2, S = max(S1, min(S1 + 6, 12)), P = L + S)

@pytest.mark.parametrize("code, kind, value", [
    ("SELECT 7/2 AS q", pa.decimal128(7, 6), Decimal("3.500000")),            # the task's 7/2, scale 6
    # the page's own examples
    ("SELECT 2/7 AS q", pa.decimal128(7, 6), Decimal("0.285714")),
    ("SELECT 10.1/2.1 AS q", pa.decimal128(10, 7), Decimal("4.8095238")),
    ("SELECT 10.001/0.001 AS q", pa.decimal128(14, 9), Decimal("10001.000000000")),
    ("SELECT 0.1/0.0000000000001 AS q", pa.decimal128(20, 7), Decimal("1000000000000.0000000")),
    ("SELECT 1/3 AS q", pa.decimal128(7, 6), Decimal("0.333333")),
    ("SELECT -7/2 AS q", pa.decimal128(7, 6), Decimal("-3.500000")),
    ("SELECT CAST(1 AS NUMBER(10,2)) / CAST(3 AS NUMBER(10,2)) AS q", pa.decimal128(18, 8), Decimal("0.33333333")),
])
def test_division_of_numbers_written_in_the_query_has_snowflakes_scale(session, code, kind, value):
    table = run(session, code)
    assert table.schema.types == [kind]
    assert table.column(0).to_pylist() == [value]
    assert "SFD-003" not in labels(session, code)


def test_division_of_number_columns_has_snowflakes_scale(session):
    table = run(session, "SELECT a / b AS q, d1 / d2 AS r FROM t ORDER BY a DESC")
    # INT is NUMBER(38,0): L = 38, S = 6, P kept to 38. NUMBER(10,2) / NUMBER(10,2) is NUMBER(18,8).
    assert table.schema.types == [pa.decimal128(38, 6), pa.decimal128(18, 8)]
    assert table.to_pylist() == [{"q": Decimal("3.500000"), "r": Decimal("0.33333333")},
                                 {"q": Decimal("-3.500000"), "r": Decimal("4.80952381")}]
    assert labels(session, "SELECT a / b, d1 / d2 FROM t") == []


def test_division_rounds_to_the_scale_and_keeps_its_unnamed_column_name(session):
    table = run(session, "SELECT 2/3, 1/16")
    assert table.column_names == ["(2 / 3)", "(1 / 16)"]
    assert table.to_pylist()[0] == {"(2 / 3)": Decimal("0.666667"), "(1 / 16)": Decimal("0.062500")}


def test_division_by_a_float_stays_a_double_with_no_label(session):
    table = run(session, "SELECT a / f AS q FROM t ORDER BY a DESC")
    assert table.schema.types == [pa.float64()]
    assert table.column(0).to_pylist() == [3.5, -1.75]
    assert labels(session, "SELECT a / f FROM t") == []


def test_division_by_zero_still_stops_and_null_stays_null(session):
    assert "Division by zero" in error(session, "SELECT a / (b - 2) FROM t")
    table = run(session, "SELECT CAST(NULL AS INT) / b AS q FROM t")
    assert table.schema.types == [pa.decimal128(38, 6)] and table.column(0).to_pylist() == [None, None]


def test_division_past_38_digits_stops_by_name(session):
    message = error(session, "SELECT CAST('123456789012345678901234567890123' AS NUMBER(38,0)) / 1")
    assert "SFN-050" in message and sf.stopped_by_name(Exception(message))
    table = run(session, "SELECT CAST('12345678901234567890123456789012' AS NUMBER(38,0)) / 1 AS q")
    assert table.schema.types == [pa.decimal128(38, 6)]
    assert table.column(0).to_pylist() == [Decimal("12345678901234567890123456789012.000000")]


def test_division_whose_operand_type_is_not_known_keeps_the_label(session):
    # a subquery's column: its DuckDB type isn't read here, so DuckDB's DOUBLE and SFD-003 stay
    code = "SELECT x / 2 AS q FROM (SELECT a + 0.5 AS x FROM t) ORDER BY q DESC"
    assert labels(session, code) == ["SFD-003"]
    assert run(session, code).schema.types == [pa.float64()]


# ---- 5. whole-number arithmetic is NUMBER(38,0) arithmetic (data-types-numeric: INT is NUMBER(38,0))

def test_integer_literal_arithmetic_past_int32_answers(session):
    table = run(session, "SELECT 2147483647 + 1 AS n, 2147483647 * 2 AS m, -2147483648 - 1 AS k")
    # + and -: L = max(L1, L2) + 1; *: L = L1 + L2 (2147483647 is NUMBER(10,0), 2 is NUMBER(1,0))
    assert table.schema.types == [pa.decimal128(11, 0), pa.decimal128(11, 0), pa.decimal128(11, 0)]
    assert table.to_pylist() == [{"n": Decimal("2147483648"), "m": Decimal("4294967294"),
                                  "k": Decimal("-2147483649")}]


def test_integer_column_arithmetic_past_int32_answers(session):
    table = run(session, "SELECT a + 2147483647 AS n, a * 10000000000 AS m FROM t ORDER BY a DESC")
    assert table.schema.types == [pa.decimal128(38, 0), pa.decimal128(38, 0)]
    assert table.to_pylist() == [{"n": Decimal("2147483654"), "m": Decimal("70000000000")},
                                 {"n": Decimal("2147483640"), "m": Decimal("-70000000000")}]


def test_arithmetic_past_38_digits_still_stops(session):
    assert "Overflow" in error(session, "SELECT 99999999999999999999999999999999999999 + 1")
    assert "Overflow" in error(session, "SELECT big * 10000000000000000000000000000000000000 FROM t WHERE big > 0")


def test_arithmetic_inside_a_function_that_takes_a_whole_number_keeps_duckdbs_type(session):
    # SUBSTR's start is a whole number in DuckDB, which doesn't take a DECIMAL
    table = run(session, "SELECT SUBSTR(s, a - 6, 3) AS x FROM t ORDER BY a DESC")
    assert table.column(0).to_pylist() == ["Spa", ""]


# ---- 2. SUBSTR with a start before the string (functions/substr: outside the range, an empty value)

@pytest.mark.parametrize("start, length, want", [
    (-10, 5, ""), (-9, 5, "Spark"), (-1, 3, "L"), (0, 3, "Spa"), (20, 3, ""), (-10, None, ""), (-3, None, "SQL"),
])
def test_substr_with_a_start_outside_the_string_is_empty(session, start, length, want):
    args = f"{start}" if length is None else f"{start}, {length}"
    table = run(session, f"SELECT SUBSTR(s, {args}) AS x FROM t WHERE a = 7")
    assert table.schema.types == [pa.string()]
    assert table.column(0).to_pylist() == [want]


def test_substr_keeps_null_and_the_pages_examples(session):
    table = run(session, "SELECT SUBSTR(s, -10, NULL) AS x, SUBSTR(s, -5, 3) AS y, SUBSTR(s, -7, 3) AS z, "
                         "SUBSTRING(s, -100) AS w FROM t WHERE a = -7")
    assert table.to_pylist() == [{"x": None, "y": "tri", "z": "yst", "w": ""}]


# ---- 3 and 4. DATEADD and DATE_TRUNC of a DATE column are a DATE (functions/dateadd, functions/date_trunc)

def test_dateadd_of_a_date_column_by_a_day_or_longer_is_a_date(session):
    table = run(session, "SELECT DATEADD(day, 1, dday) AS d, DATEADD(week, 1, dday) AS w, DATEADD(month, 1, dday) AS m, "
                         "DATEADD(quarter, 1, dday) AS q, DATEADD(year, 1, dday) AS y FROM t WHERE a = 7")
    assert table.schema.types == [pa.date32()] * 5
    assert table.to_pylist() == [{"d": dt.date(2023, 2, 1), "w": dt.date(2023, 2, 7), "m": dt.date(2023, 2, 28),
                                  "q": dt.date(2023, 4, 30), "y": dt.date(2024, 1, 31)}]
    assert labels(session, "SELECT DATEADD(month, 1, dday) FROM t") == []


def test_dateadd_of_a_date_column_by_an_hour_is_a_timestamp(session):
    # "with 00:00:00.000 as the starting time for the date" (the page's DATE plus 2 hours)
    table = run(session, "SELECT DATEADD(hour, 2, dday) AS h FROM t WHERE a = -7")
    assert table.schema.types == [pa.timestamp("us")]
    assert table.column(0).to_pylist() == [dt.datetime(2024, 5, 9, 2, 0)]


def test_date_trunc_of_a_date_column_is_a_date_for_every_part(session):
    table = run(session, "SELECT DATE_TRUNC('YEAR', dday) AS y, DATE_TRUNC('MONTH', dday) AS m, "
                         "DATE_TRUNC('WEEK', dday) AS w, DATE_TRUNC('DAY', dday) AS d, DATE_TRUNC('HOUR', dday) AS h, "
                         "DATE_TRUNC('QUARTER', dday) AS q FROM t WHERE a = -7")
    assert table.schema.types == [pa.date32()] * 6
    # the page's mydate 2024-05-09: year 2024-01-01, month 2024-05-01, week 2024-05-06, day 2024-05-09
    assert table.to_pylist() == [{"y": dt.date(2024, 1, 1), "m": dt.date(2024, 5, 1), "w": dt.date(2024, 5, 6),
                                  "d": dt.date(2024, 5, 9), "h": dt.date(2024, 5, 9), "q": dt.date(2024, 4, 1)}]
    assert labels(session, "SELECT DATE_TRUNC('WEEK', dday) FROM t") == []


def test_date_trunc_of_a_timestamp_column_stays_a_timestamp_with_no_label(session):
    table = run(session, "SELECT DATE_TRUNC('HOUR', ts) AS h FROM t WHERE a = 7")
    assert table.schema.types == [pa.timestamp("us")]
    assert table.column(0).to_pylist() == [dt.datetime(2024, 5, 9, 8, 0)]
    assert labels(session, "SELECT DATE_TRUNC('MONTH', ts) FROM t") == []


def test_a_table_made_in_the_same_cell_keeps_the_label():
    # its types aren't known when the cell is translated, so the result is DuckDB's and says so
    t = sf.translate("CREATE TABLE u (d DATE); SELECT DATEADD(day, 1, d) FROM u")
    assert [n.id for n in t.statements[1].notes] == ["SFD-006"]


def test_session_tables_reads_upper_case_names_and_types():
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE lower_t (x INTEGER, y DECIMAL(10,2), z DATE)")
    assert sf.session_tables(con) == {"LOWER_T": {"X": "INTEGER", "Y": "DECIMAL(10,2)", "Z": "DATE"}}


def test_the_rules_are_listed_with_this_version():
    table = sf.docs_table()
    assert sf.RULES_VERSION == "2026.11.21"
    for rule in ("SFX-050", "SFX-051", "SFX-052", "SFX-053", "SFN-050"):
        assert f"| {rule} |" in table
