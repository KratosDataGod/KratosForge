"""Spark SQL built-ins that gave a silent wrong answer here, each named after its case in the differential test
(tests/spark_differential.py), whose KNOWN list no longer names them.

The first answer in each test is Apache Spark 3.5.3's, from tests/fixtures/spark_3_5_3_differential.json (local
mode, UTC, ANSI off). The other answers were worked out from Spark 3.5's source for that function (named in each
test) and were not run on Spark: UNVERIFIED on Spark itself.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop import sql


def one(expression: str):
    return next(iter(sql.run("SELECT " + expression).to_pylist()[0].values()))


def spark_answer(case_sql: str):
    """Spark 3.5.3's recorded answer for a differential case."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import spark_differential as diff
    (case,) = [c for c in diff.load() if c["sql"] == case_sql]
    return diff.decode(case["spark"])


def check(case_sql: str) -> None:
    assert one(case_sql) == spark_answer(case_sql), case_sql


@pytest.mark.parametrize(("expression", "want"), [
    ("levenshtein('ÄbÖ', 'aÖü')", 3), ("levenshtein('', 'ÄÖü')", 3), ("levenshtein('😀a', 'a')", 1),
    ("levenshtein('kitten', 'sitting', 2)", -1), ("levenshtein('kitten', 'sitting', 3)", 3),
    ("levenshtein('é', 'e', CAST(NULL AS INT))", None), ("levenshtein(12, '1é2')", 1),
])
def test_levenshtein_non_ascii(expression: str, want) -> None:
    """Spark counts characters (UTF8String.levenshteinDistance), DuckDB bytes; a third argument is a threshold."""
    check("levenshtein('12', 'ÄÖü')")
    assert one(expression) == want


@pytest.mark.parametrize(("expression", "want"), [
    ("bit_count(-1L)", 64), ("bit_count(true)", 1), ("bit_count(CAST(NULL AS INT))", None),
    ("bit_count(5)", 2), ("typeof(bit_count(-1))", "int"),
])
def test_bit_count_negative_int(expression: str, want) -> None:
    """BitwiseCount is Long.bitCount of the value as a long, so a negative int counts 64 bits."""
    check("bit_count(-7)")
    check("bit_count(if(CAST(NULL AS BOOLEAN), 7, -1))")
    assert one(expression) == want


def test_bit_count_of_a_double() -> None:
    """Spark's ceil of a double is a bigint, so bit_count takes it."""
    check("bit_count(ceil(0.0D))")


def test_pmod_double_by_zero() -> None:
    """pmod's template copies its arguments; an inner % by zero is NULL in every copy."""
    check("(pmod(power(1, -1), (months_between(DATE'1999-12-31', DATE'2023-12-31') / (pmod(0.5D, 0.0D)))))")
    assert one("pmod(1.0D, 1.0D / pmod(0.5D, 0.0D))") is None


@pytest.mark.parametrize(("expression", "want"), [
    ("CAST(-7 AS DECIMAL(5,1)) div CAST(2 AS DECIMAL(3,1))", -3), ("1.5 div 0", None), ("typeof(1.5 div 1)", "bigint"),
])
def test_div_of_a_decimal(expression: str, want) -> None:
    """IntegralDivide: a bigint, the quotient cut toward zero, NULL for a zero divisor (ANSI off)."""
    check("(floor(-1.25) div if(true, 7, 100))")
    assert one(expression) == want


def test_isnan_of_null() -> None:
    """IsNaN isn't nullable: NULL is false."""
    check("isnan(CAST(NULL AS DOUBLE))")
    check("isnan(CAST('MiXeD' AS DOUBLE))")
    assert one("isnan(CAST('NaN' AS DOUBLE))") is True


@pytest.mark.parametrize(("expression", "want"), [
    ("to_date('24-02-29')", None), ("to_date(' 2024-1-5T12:00 ')", dt.date(2024, 1, 5)),
    ("to_date('+2024-01-05')", dt.date(2024, 1, 5)), ("to_date('2024')", dt.date(2024, 1, 1)),
    ("to_date('2024-7')", dt.date(2024, 7, 1)), ("to_date('2024-02-30')", None), ("to_date('2024-01-')", None),
    ("to_date('2024-01-05 anything')", dt.date(2024, 1, 5)), ("to_date(CAST(NULL AS STRING))", None),
    ("to_date(TIMESTAMP'2024-01-05 23:00:00')", dt.date(2024, 1, 5)),
    ("CAST('24-02-29' AS DATE)", None), ("CAST(' 2024-1-5 x' AS DATE)", dt.date(2024, 1, 5)),
    ("date('24-02-29')", None), ("CAST('2024-01-05' AS DATE) + 1", dt.date(2024, 1, 6)),
    ("DATE'2024-01-05'", dt.date(2024, 1, 5)),
])
def test_to_date_short_year(expression: str, want) -> None:
    """DateTimeUtils.stringToDate: a year of 4 to 7 digits, then -month and -day of 1 or 2, then ' ' or 'T' and
    anything; CAST(text AS DATE) reads text the same way."""
    check("to_date(substring('2024-02-29', 3))")
    check("to_date(rtrim(right('2024-02-29', 8)))")
    assert one(expression) == want


def test_cast_long_text_to_date() -> None:
    """'-7-7-7-7-7' isn't a date to Spark; DuckDB read it as a date before year 1, which couldn't be fetched."""
    check("CAST(repeat('-7', 5) AS DATE)")


@pytest.mark.parametrize(("expression", "want"), [
    ("date_format(DATE'2024-02-29', 'yyyy')", "2024"), ("date_format(make_date(10000, 1, 1), 'yyyy')", "+10000"),
    ("date_format(make_date(10000, 1, 1), 'yyyyy')", "10000"), ("date_format(make_date(10000, 1, 1), 'y')", "10000"),
])
def test_date_format_past_9999(expression: str, want) -> None:
    """Java's DateTimeFormatter: four or more y letters print + before a year with more digits than letters."""
    check("date_format(greatest(add_months(DATE'2024-02-29', 1000000), to_date('x_y%z', 'dd/MM/yyyy')), 'yyyyMM')")
    assert one(expression) == want


@pytest.mark.parametrize("case_sql", [
    "shiftleft(-7, 3)", "shiftleft(10, -2)", "shiftleft(-7, -1)", "shiftleft(1000000, -4)",
    "shiftleft(if(true, -100, 1000000), -4)", "shiftleft(CAST(1.0E3D AS BIGINT), -4)", "shiftright(-3, -4)",
    "(- shiftright(7, 8))", "shiftright(floor(1.25), 2)", "shiftleft(sign((1 % 7)), 3)", "shiftright(sign(7), 4)",
    "bit_count(shiftleft(-100, 8))", "shiftright(shiftleft(10, -6), 4)",
])
def test_shift_java_rules(case_sql: str) -> None:
    """ShiftLeft and ShiftRight shift as Java does: the count mod 32 for an int and mod 64 for a long."""
    check(case_sql)


@pytest.mark.parametrize(("expression", "want"), [
    ("shiftleft(3000000000, 1)", 6000000000), ("shiftleft(-1L, 63)", -9223372036854775808),
    ("shiftright(-9223372036854775807L - 1, 63)", -1), ("shiftleft(1, 31)", -2147483648),
    ("shiftleft(CAST(NULL AS INT), 2)", None), ("shiftleft(1, CAST(NULL AS INT))", None),
    ("typeof(shiftleft(1, 2))", "int"), ("typeof(shiftleft(1L, 2))", "bigint"),
])
def test_shift_java_rules_on_longs(expression: str, want) -> None:
    check("shiftleft(-7, 3)")
    assert one(expression) == want


@pytest.mark.parametrize(("case_sql", "spark"), [
    ("(ceil(1.0E3D) div if(false, 10, 7))", 142),
    ("CAST(ceil(1.0E3D) AS STRING)", "1000"),
    ("((10 ^ ceil(CAST(NULL AS DOUBLE))) / -100)", None),
])
def test_ceil_floor_of_a_double(case_sql: str, spark) -> None:
    """Spark's ceil and floor of a double or a whole number are a bigint (math.ceil(d).toLong). These answers are
    Spark 3.5.3's from the SQL differential thread's 3,000-case run (seed 99); that fixture isn't checked in."""
    assert one(case_sql) == spark


@pytest.mark.parametrize(("expression", "want"), [
    ("typeof(floor(-2.5D))", "bigint"), ("floor(-2.5D)", -3), ("ceil(CAST('NaN' AS DOUBLE))", 0),
    ("ceil(1e30D)", 9223372036854775807), ("typeof(ceil(7))", "bigint"), ("floor(CAST(NULL AS DOUBLE))", None),
])
def test_ceil_floor_of_a_double_edges(expression: str, want) -> None:
    assert one(expression) == want


@pytest.mark.parametrize("text", ["abc", "2024-02-30", "24-02-29"])
def test_to_date_of_bad_text_stops_in_a_databricks_cell(text: str) -> None:
    """Databricks SQL runs with ANSI on, so to_date of text that isn't a date stops with CAST_INVALID_INPUT, as
    Databricks does; Spark SQL with ANSI off gives NULL (above)."""
    from fabric_desktop.engines.contract import CellRequest, SessionConfig
    from fabric_desktop.engines.local_sql import DuckDBSession
    session = DuckDBSession(SessionConfig("nb"))
    try:
        bad = session.execute(CellRequest(code=f"SELECT to_date('{text}') AS d", language="sql", dialect="databricks"))
        assert bad.status != "ok" and "[CAST_INVALID_INPUT]" in bad.outputs[0]["evalue"]
        good = session.execute(CellRequest(code="SELECT to_date('2024-02-29') AS d, to_date(CAST(NULL AS STRING)) AS n",
                                           language="sql", dialect="databricks", result_rows=10))
        assert good.status == "ok" and good.table.to_pylist() == [{"d": dt.date(2024, 2, 29), "n": None}]
    finally:
        session.close()
