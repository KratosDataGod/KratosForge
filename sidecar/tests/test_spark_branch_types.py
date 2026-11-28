"""Spark SQL on this PC gives CASE, if, coalesce, nvl, nvl2, greatest, least and UNION Spark's result type (WR-259).

Spark (ANSI off) widens text with a number, a date or a timestamp to STRING, an integer with a decimal to the decimal
that holds both (a BIGINT counts as decimal(20,0)), and stops on a boolean mixed with a number or text. DuckDB cast
the text to the number instead: CASE WHEN c THEN 1 ELSE 'a' END was the int 1 (or a stop on 'a'), coalesce(1L, 2.5)
a decimal(20,1), and a boolean mixed with an int became 1. Every expected value and type below was read from Apache
Spark 3.5.3 (local[1], UTC, ANSI off) for the same query.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel
from fabric_desktop.spark_decimals import Refused


@pytest.fixture(scope="module")
def spark():
    session = Kernel().namespace["spark"]
    session.createDataFrame([(1, "1.5", 1.5, None, 7, "2024-01-02 03:04:05", True, "1.25"),
                             (0, "abc", 0.0, 10, 9223372036854775807, "2024-05-06 07:08:09", False, "3.5")],
                            "i int, t string, d double, n int, b bigint, tss string, f boolean, decs string"
                            ).createOrReplaceTempView("kf_c259_raw")
    session.sql("CREATE OR REPLACE TEMP VIEW kf_c259 AS SELECT i, t, d, n, b, CAST(tss AS TIMESTAMP) AS ts, f, "
                "CAST(decs AS DECIMAL(5,2)) AS dec FROM kf_c259_raw")
    return session


SPARK = [
    ("CASE WHEN true THEN 1 ELSE 'a' END", "1", "string"),
    ("CASE WHEN false THEN 1 ELSE 'a' END", "a", "string"),
    ("CASE WHEN true THEN 1 ELSE '2' END", "1", "string"),
    ("CASE WHEN true THEN 1.0D ELSE 'a' END", "1.0", "string"),
    ("CASE WHEN true THEN 1e20D ELSE 'a' END", "1.0E20", "string"),
    ("CASE WHEN true THEN CAST(1.5 AS FLOAT) ELSE 'a' END", "1.5", "string"),
    ("CASE WHEN true THEN 1.50 ELSE 'a' END", "1.50", "string"),
    ("CASE WHEN true THEN TIMESTAMP'2024-01-02 03:04:05.120' ELSE 'a' END", "2024-01-02 03:04:05.12", "string"),
    ("CASE WHEN true THEN DATE'2024-01-02' ELSE 'x' END", "2024-01-02", "string"),
    ("CASE WHEN true THEN CAST(1 AS BIGINT) ELSE 1.5 END", Decimal("1.0"), "decimal(21,1)"),
    ("CASE WHEN true THEN 12345678901 ELSE 1.25 END", Decimal("12345678901.00"), "decimal(22,2)"),
    ("CASE WHEN true THEN 'b' WHEN false THEN 2 ELSE 3.5 END", "b", "string"),
    ("CASE WHEN true THEN 2 WHEN false THEN 3.5 ELSE 'b' END", "2", "string"),
    ("CASE WHEN true THEN 1 ELSE CAST(1.5 AS DECIMAL(38,37)) END", Decimal("1"), "decimal(38,37)"),
    ("CASE WHEN true THEN 1000 ELSE CAST(1.5 AS DECIMAL(38,37)) END", None, "decimal(38,37)"),
    ("CASE WHEN true THEN NULL ELSE 1 END", None, "int"),
    ("CASE WHEN true THEN CAST(1 AS TINYINT) ELSE CAST(300 AS SMALLINT) END", 1, "smallint"),
    ("CASE WHEN true THEN 1.5BD ELSE 1e0 END", 1.5, "double"),
    ("CASE WHEN true THEN CAST(1.5 AS FLOAT) ELSE 2.25 END", 1.5, "double"),
    ("CASE WHEN true THEN 10 ELSE 'a' END < '9'", True, "boolean"),
    ("length(coalesce(12345, 'x'))", 5, "int"),
    ("CASE WHEN true THEN CASE WHEN true THEN 1 ELSE 'a' END ELSE 2.5 END", "1", "string"),
    ("coalesce(1, 'a')", "1", "string"),
    ("coalesce(NULL, 'a', 1)", "a", "string"),
    ("coalesce(CAST(NULL AS INT), '7')", "7", "string"),
    ("coalesce(1, 2.5, 'x')", "1", "string"),
    ("coalesce(1L, 2.5)", Decimal("1.0"), "decimal(21,1)"),
    ("coalesce(DATE'2024-01-02', 'x')", "2024-01-02", "string"),
    ("coalesce(CAST(NULL AS DOUBLE), '7')", "7", "string"),
    ("nvl(1, 'a')", "1", "string"),
    ("ifnull(2.5D, 'a')", "2.5", "string"),
    ("nvl2(1, 2, 'x')", "2", "string"),
    ("nvl2(1, CAST(1 AS BIGINT), 1.5)", Decimal("1.0"), "decimal(21,1)"),
    ("if(true, 1, 'a')", "1", "string"),
    ("if(false, 1, 'a')", "a", "string"),
    ("if(true, CAST(1 AS BIGINT), 1.5)", Decimal("1.0"), "decimal(21,1)"),
    ("greatest(CAST(1 AS BIGINT), 1.5)", Decimal("1.5"), "decimal(21,1)"),
    ("least(CAST(2 AS BIGINT), 1.5)", Decimal("1.5"), "decimal(21,1)"),
    ("greatest('a', 'b')", "b", "string"),
    ("least(1, 2.5D)", 1.0, "double"),
    ("CASE WHEN true THEN true ELSE false END", True, "boolean"),
    ("coalesce(NULL, true)", True, "boolean"),
]


@pytest.mark.parametrize(("expression", "value", "kind"), SPARK)
def test_a_mixed_case_gives_sparks_value_and_type(spark, expression, value, kind):
    got = spark.sql(f"SELECT {expression} AS r, typeof({expression}) AS t").collect()[0]
    assert (got[0], got[1]) == (value, kind)
    assert type(got[0]) is type(value)


QUERIES = [
    ("SELECT i, CASE WHEN i > 0 THEN i ELSE t END AS r FROM kf_c259 ORDER BY i", [(0, "abc"), (1, "1")]),
    ("SELECT i, coalesce(n, t) AS r FROM kf_c259 ORDER BY i", [(0, "10"), (1, "1.5")]),
    ("SELECT i, if(i > 0, d, t) AS r FROM kf_c259 ORDER BY i", [(0, "abc"), (1, "1.5")]),
    ("SELECT i, coalesce(b, dec) AS r, typeof(coalesce(b, dec)) AS ty FROM kf_c259 ORDER BY i",
     [(0, Decimal("9223372036854775807.00"), "decimal(22,2)"), (1, Decimal("7.00"), "decimal(22,2)")]),
    ("SELECT i, CASE WHEN i > 0 THEN ts ELSE t END AS r FROM kf_c259 ORDER BY i", [(0, "abc"), (1, "2024-01-02 03:04:05")]),
    ("SELECT max(CASE WHEN i > 0 THEN i ELSE t END) AS r FROM kf_c259", [("abc",)]),
    ("SELECT i FROM kf_c259 WHERE coalesce(n, t) = '10' ORDER BY i", [(0,)]),
    ("SELECT r FROM (SELECT i AS r FROM kf_c259 UNION ALL SELECT t FROM kf_c259) ORDER BY r",
     [("0",), ("1",), ("1.5",), ("abc",)]),
    ("SELECT r, typeof(r) FROM (SELECT b AS r FROM kf_c259 UNION ALL SELECT dec FROM kf_c259) ORDER BY r",
     [(Decimal("1.25"), "decimal(22,2)"), (Decimal("3.50"), "decimal(22,2)"), (Decimal("7.00"), "decimal(22,2)"),
      (Decimal("9223372036854775807.00"), "decimal(22,2)")]),
    ("SELECT r FROM (SELECT d AS r FROM kf_c259 UNION SELECT t FROM kf_c259) ORDER BY r", [("0.0",), ("1.5",), ("abc",)]),
    ("SELECT i FROM kf_c259 UNION ALL SELECT 2.5 ORDER BY 1", [(Decimal("0.0"),), (Decimal("1.0"),), (Decimal("2.5"),)]),
]


@pytest.mark.parametrize(("query", "rows"), QUERIES)
def test_columns_of_mixed_types_give_sparks_rows(spark, query, rows):
    assert [tuple(r) for r in spark.sql(query).collect()] == rows


UNIONS = [
    ("SELECT 1 AS a UNION ALL SELECT 'a'", [("1", "string"), ("a", "string")]),
    ("SELECT 1.0D AS a UNION ALL SELECT 'x'", [("1.0", "string"), ("x", "string")]),
    ("SELECT 1e20D AS a UNION ALL SELECT 'x'", [("1.0E20", "string"), ("x", "string")]),
    ("SELECT TIMESTAMP'2024-01-02 03:04:05' AS a UNION ALL SELECT 'x'", [("2024-01-02 03:04:05", "string"), ("x", "string")]),
    ("SELECT CAST(1 AS BIGINT) AS a UNION ALL SELECT 2.5", [(Decimal("1.0"), "decimal(21,1)"), (Decimal("2.5"), "decimal(21,1)")]),
    ("SELECT CAST(1 AS BIGINT) AS a UNION ALL SELECT CAST(1.5 AS DECIMAL(38,1))",
     [(Decimal("1.0"), "decimal(38,1)"), (Decimal("1.5"), "decimal(38,1)")]),
    ("SELECT 1 AS a UNION ALL SELECT CAST(1.5 AS DECIMAL(38,37))",
     [(Decimal("1"), "decimal(38,37)"), (Decimal("1.5"), "decimal(38,37)")]),
    ("SELECT 1L AS a UNION ALL SELECT 1.5 UNION ALL SELECT 'x'", [("1.0", "string"), ("1.5", "string"), ("x", "string")]),
    ("SELECT 10 AS a UNION ALL SELECT '9'", [("10", "string"), ("9", "string")]),
    ("SELECT 1L AS a UNION SELECT 1.5", [(Decimal("1.0"), "decimal(21,1)"), (Decimal("1.5"), "decimal(21,1)")]),
    ("SELECT 1L AS a INTERSECT SELECT 1.0", [(Decimal("1.0"), "decimal(21,1)")]),
    ("SELECT 1L AS a EXCEPT SELECT 1.5", [(Decimal("1.0"), "decimal(21,1)")]),
    ("SELECT NULL AS a UNION ALL SELECT true", [(None, "boolean"), (True, "boolean")]),
    ("SELECT 1 AS a UNION ALL (SELECT 2.5 UNION ALL SELECT 'x')", [("1", "string"), ("2.5", "string"), ("x", "string")]),
    ("SELECT 1.5D AS a, 'k' AS b UNION ALL SELECT 'z', 2", [("1.5", "string"), ("z", "string")]),
]


@pytest.mark.parametrize(("query", "rows"), UNIONS)
def test_a_set_operation_widens_its_columns_as_spark(spark, query, rows):
    got = spark.sql(f"SELECT a AS r, typeof(a) AS t FROM ({query}) ORDER BY 1").collect()
    assert [tuple(r) for r in got] == rows


REFUSED = [
    "SELECT CASE WHEN true THEN true ELSE 'x' END", "SELECT CASE WHEN true THEN true ELSE 1 END",
    "SELECT coalesce(true, 'x')", "SELECT ifnull(true, 1)", "SELECT if(true, true, 1)", "SELECT nvl2(1, true, 2)",
    "SELECT greatest(1, '2')", "SELECT least('a', 1.5D)", "SELECT CASE WHEN true THEN X'01' ELSE 'a' END",
    "SELECT true AS a UNION ALL SELECT 1", "SELECT 'a' AS a UNION SELECT 1 UNION SELECT true",
    "SELECT CAST(NULL AS BOOLEAN) AS a UNION ALL SELECT 1", "SELECT 1 AS a INTERSECT SELECT true",
    "SELECT 1 AS a EXCEPT SELECT X'01'", "SELECT CASE WHEN i > 0 THEN f ELSE i END FROM kf_c259",
    "SELECT f FROM kf_c259 UNION ALL SELECT t FROM kf_c259", "SELECT CASE WHEN true THEN DATE'2024-01-02' ELSE 1 END",
]


@pytest.mark.parametrize("query", REFUSED)
def test_a_mix_spark_stops_on_is_refused_by_name(spark, query):
    # Spark 3.5.3 stops on each with DATATYPE_MISMATCH.DATA_DIFF_TYPES or INCOMPATIBLE_COLUMN_TYPE.
    with pytest.raises(ValueError, match="Spark has no common type for .* so nothing ran.* Cast the values to one type"):
        spark.sql(query).collect()


def test_sql_run_gives_sparks_types_too():
    row = sql.run("SELECT coalesce(1, 'a') AS a, typeof(coalesce(1L, 2.5)) AS b, "
                  "CASE WHEN false THEN 1 ELSE 'x' END AS c").to_pylist()[0]
    assert row == {"a": "1", "b": "decimal(21,1)", "c": "x"}
    with pytest.raises(Refused, match="A UNION's column 1 mixes"):
        sql.run("SELECT true AS a UNION ALL SELECT 1")


def test_the_translators_own_case_keeps_its_types():
    # pmod and bround are written as a CASE here; only the query's own CASE is retyped.
    row = sql.run("SELECT pmod(-7, 3) AS a, bround(2.5) AS b, nvl(NULL, 1.5D) AS c").to_pylist()[0]
    assert row == {"a": 2, "b": Decimal("2"), "c": 1.5}


def test_nested_cases_grow_the_sql_linearly():
    from fabric_desktop.sql import _decimal_types, _connect, duckdb_sql, parse
    sizes = []
    for depth in range(1, 6):
        expression = "1.0D"
        for _ in range(depth):
            expression = f"CASE WHEN rand() > 0.5 THEN {expression} ELSE 'a' END"
        statement = parse(f"SELECT {expression}")[0]
        sizes.append(len(duckdb_sql(_decimal_types(statement, _connect(), "spark"))))
    steps = [b - a for a, b in zip(sizes, sizes[1:], strict=False)]
    assert max(steps) <= 1.2 * min(steps), sizes


def test_a_column_named_like_the_rewrites_lambda_is_left_as_it_was(spark):
    got = spark.sql("SELECT coalesce(kf_branch_text, 'x') AS r FROM VALUES (1) AS t(kf_branch_text)").collect()
    assert got[0][0] == 1  # DuckDB's own type, as before: the rewrite would read the column as its lambda


def test_a_timestamp_with_text_is_the_timestamp_written_as_text(spark):
    # Read from Spark 3.5.3: the string '2024-01-01 10:00:00', typeof string.
    row = spark.sql("SELECT CASE WHEN true THEN CAST('2024-01-01 10:00:00' AS TIMESTAMP) ELSE 'a' END AS v, "
                    "typeof(CASE WHEN true THEN CAST('2024-01-01 10:00:00' AS TIMESTAMP) ELSE 'a' END) AS t").collect()[0]
    assert (row.v, row.t) == ("2024-01-01 10:00:00", "string")
