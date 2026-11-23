"""Spark's text for a decimal and an interval, its text read as a double, and round() to places worked out
from an expression, on this PC. Each was a silent wrong answer here:

- CAST(CAST(0.00000001 AS DECIMAL(9,8)) AS STRING) was '0.00000001' where Spark writes Java's
  BigDecimal.toString, '1E-8' (and '.5' for a DECIMAL(1,1), where Java writes '0.5');
- CAST(make_dt_interval(0, 0, 0, 5) AS STRING) was DuckDB's '00:00:05' where Spark writes
  INTERVAL '0 00:00:05' DAY TO SECOND, and -make_dt_interval(...) lost its sign;
- round(0.285D, 1 + 1) rounded the binary value, 0.28, where Spark folds the places and rounds the decimal
  text, 0.29; round(x, n) with a column n, which Spark stops on, answered;
- CAST('1.5d' AS DOUBLE) was NULL and CAST('1_000' AS DOUBLE) 1000, where Java's Double.parseDouble gives
  1.5 and Spark NULL.

The expected values were read from Apache Spark 3.5.3 (local[1], UTC, ANSI mode off), with typeof().
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def row(spark, query: str) -> tuple:
    return tuple(spark.sql(query).collect()[0])


def column(spark, query: str) -> list:
    return [r[0] for r in spark.sql(query).collect()]


def size(query: str) -> int:
    return len(sql.duckdb_sql(sql.parse(query)[0]))


def grows_linearly(sizes: list[int]) -> bool:
    return len({b - a for a, b in zip(sizes, sizes[1:], strict=False)}) == 1


_DEC = "CAST(0.00000001 AS DECIMAL(9,8))"

# (query, Spark 3.5.3's row)
DECIMAL_TEXT = [
    (f"SELECT CAST({_DEC} AS STRING), typeof(CAST({_DEC} AS STRING))", ("1E-8", "string")),
    ("SELECT CAST(CAST(0.0000001 AS DECIMAL(9,8)) AS STRING)", ("1.0E-7",)),
    ("SELECT CAST(CAST(0.000001 AS DECIMAL(9,8)) AS STRING)", ("0.00000100",)),
    ("SELECT CAST(CAST(0 AS DECIMAL(9,8)) AS STRING), CAST(CAST(0 AS DECIMAL(38,10)) AS STRING)", ("0E-8", "0E-10")),
    ("SELECT CAST(CAST(-0.00000012 AS DECIMAL(10,9)) AS STRING)", ("-1.20E-7",)),
    ("SELECT CAST(CAST(1.5 AS DECIMAL(10,9)) AS STRING)", ("1.500000000",)),
    ("SELECT CAST(CAST(0.00000001 AS DECIMAL(38,20)) AS STRING)", ("1.000000000000E-8",)),
    ("SELECT CAST(0.00000001BD AS STRING), CAST(0.00000001 AS STRING)", ("1E-8", "1E-8")),
    ("SELECT CAST(CAST(0.5 AS DECIMAL(1,1)) AS STRING), CAST(CAST(-0.5 AS DECIMAL(1,1)) AS STRING)", ("0.5", "-0.5")),
    ("SELECT CAST(CAST(-0.000001 AS DECIMAL(8,8)) AS STRING)", ("-0.00000100",)),
    ("SELECT CAST(CAST(123.000000001 AS DECIMAL(20,9)) AS STRING), CAST(CAST(12345 AS DECIMAL(10,0)) AS STRING)",
     ("123.000000001", "12345")),
    (f"SELECT concat('x', {_DEC}), 'x' || {_DEC}, CAST(1e-8 AS DECIMAL(20,10)) || ''", ("x1E-8", "x1E-8", "1.00E-8")),
    (f"SELECT upper({_DEC}), length({_DEC}), replace({_DEC}, '1', '2'), lpad({_DEC}, 6, '*')",
     ("1E-8", 4, "2E-8", "**1E-8")),
    (f"SELECT string({_DEC}), CAST({_DEC} AS VARCHAR(10)), concat_ws(',', {_DEC}), format_string('%s', {_DEC})",
     ("1E-8", "1E-8", "1E-8", "1E-8")),
    (f"SELECT reverse({_DEC}), octet_length({_DEC}), initcap({_DEC}), array_join(array({_DEC}), ',')",
     ("8-E1", 4, "1e-8", "1E-8")),
    (f"SELECT {_DEC} = '1E-8', '1E-8' IN ({_DEC}, 'a')", (True, True)),
    ("SELECT sum(x) || '', CAST(avg(x) AS STRING) FROM VALUES (CAST(0.00000001 AS DECIMAL(9,8))) t(x)",
     ("1E-8", "1.0000E-8")),
    ("SELECT reverse(1e20D), reverse(CAST(1.5 AS FLOAT)), octet_length(1e20D)", ("02E0.1", "5.1", 6)),
]


@pytest.mark.parametrize(("query", "expected"), DECIMAL_TEXT)
def test_a_decimal_as_text_is_javas_bigdecimal_text(spark, query, expected):
    assert row(spark, query) == expected


def test_a_decimal_column_as_text_is_javas(spark):
    got = column(spark, "SELECT CAST(x AS STRING) FROM VALUES (CAST(0.00000001 AS DECIMAL(9,8))), "
                        "(CAST(0.5 AS DECIMAL(9,8))), (NULL) t(x)")
    assert got == ["1E-8", "0.50000000", None]


def test_column_cast_to_string_writes_a_decimal_as_spark(spark):
    df = spark.sql(f"SELECT {_DEC} AS x")
    assert df.select(df.x.cast("string").alias("s")).collect()[0].s == "1E-8"


INTERVAL_TEXT = [
    ("SELECT CAST(make_dt_interval(0,0,0,5) AS STRING)", "INTERVAL '0 00:00:05' DAY TO SECOND"),
    ("SELECT CAST(make_dt_interval(1,12,30,1.001001) AS STRING)", "INTERVAL '1 12:30:01.001001' DAY TO SECOND"),
    ("SELECT CAST(make_dt_interval(-1,-12,0,0) AS STRING)", "INTERVAL '-1 12:00:00' DAY TO SECOND"),
    ("SELECT CAST(make_dt_interval(100,0,0,0.5) AS STRING)", "INTERVAL '100 00:00:00.5' DAY TO SECOND"),
    ("SELECT CAST(make_dt_interval(0,25,0,0) AS STRING)", "INTERVAL '1 01:00:00' DAY TO SECOND"),
    ("SELECT CAST(make_dt_interval() AS STRING)", "INTERVAL '0 00:00:00' DAY TO SECOND"),
    ("SELECT CAST(-make_dt_interval(0,0,0,5) AS STRING)", "INTERVAL '-0 00:00:05' DAY TO SECOND"),
    ("SELECT CAST(make_dt_interval(0,0,0,-0.000001) AS STRING)", "INTERVAL '-0 00:00:00.000001' DAY TO SECOND"),
    ("SELECT CAST(make_dt_interval(106751991,0,0,0) AS STRING)", "INTERVAL '106751991 00:00:00' DAY TO SECOND"),
    ("SELECT CAST(INTERVAL '5' SECOND AS STRING)", "INTERVAL '05' SECOND"),
    ("SELECT CAST(INTERVAL '1.5' SECOND AS STRING)", "INTERVAL '01.5' SECOND"),
    ("SELECT CAST(INTERVAL '100' SECOND AS STRING)", "INTERVAL '100' SECOND"),
    ("SELECT CAST(INTERVAL '12.000100' SECOND AS STRING)", "INTERVAL '12.0001' SECOND"),
    ("SELECT CAST(INTERVAL '0' SECOND AS STRING)", "INTERVAL '00' SECOND"),
    ("SELECT CAST(INTERVAL '-5' SECOND AS STRING)", "INTERVAL '-05' SECOND"),
    ("SELECT CAST(-INTERVAL '5' SECOND AS STRING)", "INTERVAL '-05' SECOND"),
    ("SELECT CAST(INTERVAL '3' DAY AS STRING)", "INTERVAL '3' DAY"),
    ("SELECT CAST(INTERVAL 2 WEEKS AS STRING)", "INTERVAL '14' DAY"),
    ("SELECT CAST(INTERVAL '3' HOUR AS STRING)", "INTERVAL '03' HOUR"),
    ("SELECT CAST(INTERVAL '25' HOUR AS STRING)", "INTERVAL '25' HOUR"),
    ("SELECT CAST(INTERVAL '90' MINUTE AS STRING)", "INTERVAL '90' MINUTE"),
    ("SELECT CAST(INTERVAL 5 MILLISECONDS AS STRING)", "INTERVAL '00.005' SECOND"),
    ("SELECT CAST(INTERVAL '2' YEAR AS STRING)", "INTERVAL '2' YEAR"),
    ("SELECT CAST(INTERVAL '14' MONTH AS STRING)", "INTERVAL '14' MONTH"),
    ("SELECT CAST(INTERVAL 1 YEAR 2 MONTH AS STRING)", "INTERVAL '1-2' YEAR TO MONTH"),
    ("SELECT CAST(INTERVAL '1 year 2 months' AS STRING)", "INTERVAL '1-2' YEAR TO MONTH"),
    ("SELECT CAST(INTERVAL '1 day 2 hours' AS STRING)", "INTERVAL '1 02' DAY TO HOUR"),
    ("SELECT CAST(INTERVAL '1 hour 30 minutes 2.5 seconds' AS STRING)", "INTERVAL '01:30:02.5' HOUR TO SECOND"),
    ("SELECT CAST(INTERVAL 1 HOUR 30 MINUTES AS STRING)", "INTERVAL '01:30' HOUR TO MINUTE"),
    ("SELECT CAST(INTERVAL '1' DAY - INTERVAL '2' HOUR AS STRING)", "INTERVAL '0 22' DAY TO HOUR"),
    ("SELECT CAST(INTERVAL '1' MINUTE + INTERVAL '1' DAY AS STRING)", "INTERVAL '1 00:01' DAY TO MINUTE"),
    ("SELECT CAST(INTERVAL '1' DAY * 2 AS STRING)", "INTERVAL '2 00:00:00' DAY TO SECOND"),
    ("SELECT CAST(INTERVAL '1' DAY AS VARCHAR(100))", "INTERVAL '1' DAY"),
    ("SELECT CAST(TIMESTAMP'2024-01-02 03:04:05' - TIMESTAMP'2024-01-01 00:00:00' AS STRING)",
     "INTERVAL '1 03:04:05' DAY TO SECOND"),
    ("SELECT CAST(TIMESTAMP'2024-01-01 00:00:00' - TIMESTAMP'2024-01-02 03:04:05.5' AS STRING)",
     "INTERVAL '-1 03:04:05.5' DAY TO SECOND"),
    ("SELECT CAST(TIMESTAMP'2024-01-02 03:04:05' - DATE'2024-01-01' AS STRING)", "INTERVAL '1 03:04:05' DAY TO SECOND"),
    ("SELECT CAST(timestamp_ntz'2024-01-02 00:00:00' - timestamp_ntz'2024-01-01 00:00:00' AS STRING)",
     "INTERVAL '1 00:00:00' DAY TO SECOND"),
    ("SELECT CAST(to_timestamp('2024-01-02') - to_timestamp('2024-01-01') AS STRING)",
     "INTERVAL '1 00:00:00' DAY TO SECOND"),
    ("SELECT CAST(DATE'2024-01-01' - DATE'2024-03-02' AS STRING)", "INTERVAL '-61' DAY"),
    ("SELECT CAST(x - y AS STRING) FROM VALUES (TIMESTAMP'2024-01-02 03:04:05', TIMESTAMP'2024-01-01 00:00:00') t(x, y)",
     "INTERVAL '1 03:04:05' DAY TO SECOND"),
    ("SELECT CAST(x - y AS STRING) FROM VALUES (DATE'2024-01-02', TIMESTAMP'2024-01-01 12:00:00') t(x, y)",
     "INTERVAL '0 12:00:00' DAY TO SECOND"),
    ("SELECT CAST(x - y AS STRING) FROM VALUES (DATE'2024-01-02', DATE'2024-01-01') t(x, y)", "INTERVAL '1' DAY"),
    ("SELECT concat('x', make_dt_interval(0,0,0,5))", "xINTERVAL '0 00:00:05' DAY TO SECOND"),
    ("SELECT 'x' || INTERVAL 14 MONTHS", "xINTERVAL '14' MONTH"),
    ("SELECT 'took ' || (TIMESTAMP'2024-01-02 03:04:05' - TIMESTAMP'2024-01-01 00:00:00')",
     "took INTERVAL '1 03:04:05' DAY TO SECOND"),
    ("SELECT CAST(make_interval(1,2,3,4,5,6,7.5) AS STRING)", "1 years 2 months 25 days 5 hours 6 minutes 7.5 seconds"),
    ("SELECT CAST(make_interval(0,0,0,0,0,0,0) AS STRING)", "0 seconds"),
    ("SELECT CAST(make_interval(-1,-2,0,-4,-5,-6,-7.5) AS STRING)",
     "-1 years -2 months -4 days -5 hours -6 minutes -7.5 seconds"),
    ("SELECT CAST(make_interval(0,0,0,0,0,0,0.25) AS STRING)", "0.25 seconds"),
]


@pytest.mark.parametrize(("query", "expected"), INTERVAL_TEXT)
def test_an_interval_as_text_is_sparks_interval_literal(spark, query, expected):
    assert row(spark, query) == (expected,)


def test_subtractions_that_are_not_of_times_keep_their_text(spark):
    assert row(spark, "SELECT CAST(x - y AS STRING) FROM VALUES (5, 3) t(x, y)") == ("2",)
    assert row(spark, "SELECT CAST(x - y AS STRING) FROM VALUES (CAST(1.5 AS DECIMAL(3,1)), "
                      "CAST(1 AS DECIMAL(5,3))) t(x, y)") == ("0.500",)
    assert row(spark, "SELECT CAST(x - INTERVAL 1 DAY AS STRING) FROM VALUES (DATE'2024-01-02') t(x)") == ("2024-01-01",)


def test_a_negated_or_multiplied_make_dt_interval_applies_to_all_of_it(spark):
    # -make_dt_interval(0, 0, 0, 5) negated only its first part, and * 2 multiplied only its seconds
    assert row(spark, "SELECT CAST(-make_dt_interval(1,2,0,0) AS STRING), CAST(make_dt_interval(1,2) * 2 AS STRING)") == (
        "INTERVAL '-1 02:00:00' DAY TO SECOND", "INTERVAL '2 04:00:00' DAY TO SECOND")


@pytest.mark.parametrize("query", [
    "SELECT CAST(x AS STRING) FROM VALUES (make_dt_interval(1,2,3,4)) t(x)",
    "SELECT CAST(x AS STRING) FROM (SELECT INTERVAL '5' SECOND AS x) t",
    "SELECT CAST(INTERVAL '1' DAY / 3 AS STRING)",
    "SELECT CAST(make_dt_interval(1) * 1.5 AS STRING)",
    "SELECT upper(make_dt_interval(0,0,0,5))",
    "SELECT length(make_dt_interval(0,0,0,5))",
    "SELECT format_string('%s', make_dt_interval(0,0,0,5))",
])
def test_an_interval_whose_fields_the_query_doesnt_show_is_refused_as_text(spark, query):
    # Spark writes these as interval literals of their fields, which DuckDB's INTERVAL doesn't carry
    with pytest.raises(Exception, match="interval") as caught:
        spark.sql(query).collect()
    assert "so nothing ran" in str(caught.value)


@pytest.mark.parametrize("query", ["SELECT CAST(INTERVAL 1 MONTH 2 DAY AS STRING)",
                                   "SELECT CAST(INTERVAL '1 day 1 month' AS STRING)",
                                   "SELECT CAST(INTERVAL '1' YEAR + INTERVAL '1' DAY AS STRING)"])
def test_an_interval_of_months_and_days_is_refused_as_text_as_spark_stops(spark, query):
    with pytest.raises(ValueError, match=r"mixes years or months with days or times, which Spark 3.5 stops on, so "
                                         r"nothing ran\. Use"):
        spark.sql(query)


ROUND_PLACES = [
    ("SELECT round(0.285D, 1+1), typeof(round(0.285D, 1+1))", (0.29, "double")),
    ("SELECT bround(0.285D, 1+1), bround(0.125D, 1+1)", (0.28, 0.12)),
    ("SELECT round(0.285D, 3-1), round(0.285D, CAST(2 AS INT)), round(0.285D, CAST('2' AS INT))", (0.29, 0.29, 0.29)),
    ("SELECT round(0.285D, -(-2)), round(0.285D, (2)), round(0.285D, abs(-2)), round(0.285D, length('ab'))",
     (0.29, 0.29, 0.29, 0.29)),
    ("SELECT round(0.285D, CAST(2 AS BIGINT)), round(0.285D, 2L), round(0.285D, 2S), round(0.285D, 2Y)",
     (0.29, 0.29, 0.29, 0.29)),
    ("SELECT round(0.285D, 2.0), round(0.285D, 2.7), round(0.285D, '2.7'), round(0.285D, '2')", (0.29, 0.29, 0.29, 0.29)),
    ("SELECT round(0.285D, CAST(NULL AS INT)), round(0.285D, 1/0)", (None, None)),
    ("SELECT round(1.005D, 1*2), round(2.5D, 0+0), round(125.0D, -(1+1)), bround(125.0D, 0-2)", (1.01, 3.0, 100.0, 100.0)),
    ("SELECT round(CAST(0.285 AS DECIMAL(5,3)), 1+1), typeof(round(CAST(0.285 AS DECIMAL(5,3)), 1+1))",
     (Decimal("0.29"), "decimal(5,2)")),
    ("SELECT round(x, 1+1) FROM VALUES (0.285D) t(x)", (0.29,)),
]


@pytest.mark.parametrize(("query", "expected"), ROUND_PLACES)
def test_round_to_places_worked_out_from_a_constant_gives_sparks_answer(spark, query, expected):
    assert row(spark, query) == expected


def test_round_of_a_float_to_folded_places_stays_a_float(spark):
    assert row(spark, "SELECT round(CAST(0.285 AS FLOAT), 1+1), typeof(round(CAST(0.285 AS FLOAT), 1+1))") == (
        pytest.approx(0.28, abs=1e-6), "float")


@pytest.mark.parametrize("query", [
    "SELECT round(x, n) FROM VALUES (0.285D, 2) t(x, n)",
    "SELECT bround(x, n) FROM VALUES (0.285D, 2) t(x, n)",
    "SELECT ceil(x, n) FROM VALUES (0.285D, 2) t(x, n)",
    "SELECT round(CAST(x AS DECIMAL(5,3)), n) FROM VALUES (0.285D, 2) t(x, n)",
    "SELECT round(0.285D, rand())",
    "SELECT round(0.285D, (SELECT 2))",
])
def test_round_to_places_that_arent_a_constant_is_refused_as_spark_stops(spark, query):
    with pytest.raises(ValueError, match=r"NON_FOLDABLE_INPUT\), so nothing ran\. Write the number of places"):
        spark.sql(query)


DOUBLE_TEXT = [
    ("SELECT CAST('1.5d' AS DOUBLE), CAST('1.5D' AS DOUBLE), CAST('1.5f' AS DOUBLE), CAST('1.5F' AS DOUBLE)",
     (1.5, 1.5, 1.5, 1.5)),
    ("SELECT CAST('Infinity' AS DOUBLE), CAST('-Infinity' AS DOUBLE), CAST('  1.5  ' AS DOUBLE), CAST('\t1.5' AS DOUBLE)",
     (math.inf, -math.inf, 1.5, 1.5)),
    ("SELECT CAST('infinity' AS DOUBLE), CAST('inf' AS DOUBLE), CAST('+Infinity' AS DOUBLE), CAST('+inf' AS DOUBLE)",
     (math.inf, math.inf, math.inf, math.inf)),
    ("SELECT CAST('1e5d' AS DOUBLE), CAST('1.5dd' AS DOUBLE), CAST('d' AS DOUBLE), CAST('.5' AS DOUBLE)",
     (100000.0, None, None, 0.5)),
    ("SELECT CAST('1.' AS DOUBLE), CAST('0x1p3' AS DOUBLE), CAST('1_000' AS DOUBLE), CAST('' AS DOUBLE)",
     (1.0, 8.0, None, None)),
    ("SELECT CAST('1.5 d' AS DOUBLE), CAST('1e400' AS DOUBLE), CAST('-0' AS DOUBLE), CAST('1.5e+3' AS DOUBLE)",
     (None, math.inf, -0.0, 1500.0)),
    ("SELECT CAST('Infinityd' AS DOUBLE), CAST('NaNd' AS DOUBLE), CAST('-nan' AS DOUBLE), CAST('1e' AS DOUBLE)",
     (None, None, None, None)),
    ("SELECT CAST('0x1.8p1' AS DOUBLE), CAST('-0x.8p0' AS DOUBLE), CAST('0X10P-2d' AS DOUBLE), CAST('0x0p0' AS DOUBLE)",
     (3.0, -0.5, 4.0, 0.0)),
    ("SELECT CAST('0xp3' AS DOUBLE), CAST('0x1' AS DOUBLE), CAST('0x1.fffffffffffffp1023' AS DOUBLE)",
     (None, None, 1.7976931348623157e308)),
    ("SELECT CAST('1.5d' AS FLOAT), CAST('Infinity' AS FLOAT), CAST('-inf' AS FLOAT), CAST('1e5f' AS FLOAT)",
     (1.5, math.inf, -math.inf, 100000.0)),
    ("SELECT double('1.5d'), float('1.5f'), try_cast('1.5d' AS DOUBLE), typeof(CAST('1.5d' AS FLOAT))",
     (1.5, 1.5, 1.5, "float")),
    ("SELECT '1.5d' = 1.5D, 'Infinity' = CAST('Infinity' AS DOUBLE), '1_000' = 1000D", (True, True, None)),
    ("SELECT CAST(CAST('NaN' AS DOUBLE) AS INT), CAST(CAST('1.5d' AS DOUBLE) AS STRING)", (0, "1.5")),
    ("SELECT round(CAST('0.285' AS DOUBLE), 2)", (0.29,)),
]


@pytest.mark.parametrize(("query", "expected"), DOUBLE_TEXT)
def test_text_cast_to_a_double_is_read_as_java_reads_it(spark, query, expected):
    got = row(spark, query)
    assert len(got) == len(expected)
    for g, e in zip(got, expected, strict=True):
        assert g == e and (not isinstance(e, float) or math.copysign(1, g) == math.copysign(1, e)), (got, expected)


def test_nan_text_is_nan(spark):
    got = row(spark, "SELECT CAST('NaN' AS DOUBLE), CAST(' NaN ' AS FLOAT), CAST('nan' AS DOUBLE), CAST('-NaN' AS DOUBLE), "
                     "CAST('+NaN' AS DOUBLE)")
    assert all(math.isnan(v) for v in got)


def test_a_text_column_cast_to_a_double_is_read_as_java_reads_it(spark):
    got = column(spark, "SELECT CAST(x AS DOUBLE) FROM VALUES ('1.5d'), ('Infinity'), ('  2.5  '), ('1.5f'), ('x'), "
                        "('-Infinity'), ('INF'), (NULL) t(x)")
    assert got == [1.5, math.inf, 2.5, 1.5, None, -math.inf, math.inf, None]
    df = spark.sql("SELECT * FROM VALUES ('1.5d'), (' 2 '), ('1_000') t(s)")
    assert [r.d for r in df.select(df.s.cast("double").alias("d")).collect()] == [1.5, 2.0, None]
    assert [r.f for r in df.select(df.s.cast("float").alias("f")).collect()] == [1.5, 2.0, None]


def test_hexadecimal_text_past_a_double_is_refused(spark):
    with pytest.raises(Exception, match=r"hexadecimal .* so nothing ran\. Write the number in decimal"):
        spark.sql("SELECT CAST('0x1p-1100' AS DOUBLE)").collect()


def test_databricks_sql_stops_on_text_that_isnt_a_double():
    import duckdb

    from fabric_desktop.engines.local_sql import _databricks
    con = duckdb.connect()
    [statement] = _databricks("SELECT CAST('1.5d' AS DOUBLE), CAST(' 2 ' AS DOUBLE)")
    assert con.execute(sql.duckdb_sql(statement)).fetchone() == (1.5, 2.0)
    [statement] = _databricks("SELECT CAST('1_000' AS DOUBLE)")
    with pytest.raises(duckdb.Error, match="CAST_INVALID_INPUT"):
        con.execute(sql.duckdb_sql(statement)).fetchone()


# Each rewrite reads its argument once, so nesting adds the same amount of SQL at every level.

def test_nested_decimal_text_grows_its_sql_linearly():
    sizes = []
    for depth in range(1, 5):
        e = "CAST(x AS DECIMAL(20,10))"
        for _ in range(depth):
            e = f"CAST(upper(CAST({e} AS STRING)) AS DECIMAL(20,10))"
        sizes.append(size(f"SELECT CAST({e} AS STRING) FROM t"))
    assert grows_linearly(sizes), sizes


def test_nested_interval_text_grows_its_sql_linearly():
    sizes = []
    for depth in range(1, 5):
        e = "t - s"
        for _ in range(depth):
            e = f"CAST(CAST({e} AS STRING) AS TIMESTAMP) - s"
        sizes.append(size(f"SELECT CAST({e} AS STRING) FROM t"))
    assert grows_linearly(sizes), sizes
    sizes = [size("SELECT CAST(" + " + ".join(["INTERVAL 1 DAY"] * n) + " AS STRING)") for n in range(1, 7)]
    assert grows_linearly(sizes), sizes


def test_nested_double_casts_and_folded_places_grow_their_sql_linearly():
    sizes = []
    for depth in range(1, 7):
        e = "s"
        for _ in range(depth):
            e = f"CAST(CAST({e} AS DOUBLE) AS STRING)"
        sizes.append(size(f"SELECT {e} FROM t"))
    assert grows_linearly(sizes), sizes
    sizes = []
    for depth in range(1, 7):
        e = "x"
        for _ in range(depth):
            e = f"round({e}, 1 + 1)"
        sizes.append(size(f"SELECT {e} FROM t"))
    assert grows_linearly(sizes), sizes
    sizes = []
    for depth in range(1, 7):
        e = "x"
        for _ in range(depth):
            e = f"octet_length(reverse({e}))"
        sizes.append(size(f"SELECT {e} FROM t"))
    assert grows_linearly(sizes), sizes
