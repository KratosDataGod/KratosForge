"""Spark SQL and the DataFrame API on this PC compare text with a number as Spark does (WR-254).

Spark (ANSI off) casts the text to the number's type: '1.5' = 1 is true, 0 = '' and '1e2' = 100 are NULL. Text and
a decimal are compared as doubles, and IN with text and numbers compares them all as text. DuckDB rounded '1.5' to
2, read '1e2' as 100 and stopped on '' or 'abc'. Every expected value below was read from Apache Spark 3.5.3
(local[1]) for the same query.
"""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    session = Kernel().namespace["spark"]
    session.createDataFrame([(1, "1.5", 1.5, " 2 "), (0, "", 0.0, "abc")],
                            "i int, t string, d double, u string").createOrReplaceTempView("kf_c254")
    return session


def one(spark, expression: str):
    return spark.sql(f"SELECT {expression} AS v").collect()[0][0]


SPARK = [
    ("'1.5' = 1", True),
    ("0 = ''", None),
    ("'1.5' = 1.5", True),
    ("'1.5' = 1.5D", True),
    ("' 1 ' = 1", True),
    ("'abc' = 1", None),
    ("'1e2' = 100", None),
    ("'2' > 10", False),
    ("'10' > 9", True),
    ("'1.9' < 2", True),
    ("'1' = 1L", True),
    ("'1.0' IN (1, 2)", False),
    ("1 IN ('1.0', '2')", False),
    ("1.5D IN ('1.5')", True),
    ("'5' BETWEEN 1 AND 10", True),
    ("'1.5' BETWEEN 1 AND 1", True),
    ("1 <=> ''", False),
    ("'abc' <=> 1", False),
    ("1 <=> 'abc'", False),
    ("'1' <=> 1", True),
    ("1 <=> '1'", True),
    ("'1.5' <=> 1", True),
    ("'1' <=> 1L", True),
    ("2L <=> 'abc'", False),
    ("'1.5' <=> 1.5D", True),
    ("1.5D <=> 'x'", False),
    ("'1.50' <=> 1.5", True),
    ("1.5 <=> 'abc'", False),
    ("CAST(NULL AS STRING) <=> 1", False),
    ("1 <=> CAST(NULL AS STRING)", False),
    ("CAST(NULL AS STRING) <=> CAST(NULL AS INT)", True),
    ("CAST(NULL AS BIGINT) <=> CAST(NULL AS STRING)", True),
    ("'abc' <=> CAST(NULL AS INT)", True),
    ("CAST(NULL AS DOUBLE) <=> 'abc'", True),
    ("'9223372036854775808' = 1", None),
    ("'1.55' = 1.6", False),
    ("'1.55' = 1.5", False),
    ("' 1.5 ' = 1.5D", True),
    ("'1.5' = CAST(1 AS TINYINT)", True),
    ("'300' = CAST(44 AS TINYINT)", None),
    ("'1.5' = 1.5F", True),
    ("'1e2' = 100.0", True),
    ("'x' <> 1", None),
    ("CASE 1 WHEN '1.5' THEN 'y' ELSE 'n' END", "y"),
    ("'.5' = 0", True),
    ("'1.' = 1", True),
    ("'+1' = 1", True),
    ("'NaN' = CAST('NaN' AS DOUBLE)", True),
    ("'1.5d' = 1.5D", True),
    ("1 = '1.5' AND '2' = 2", True),
    ("'true' = true", True),
    ("'2024-01-01' = DATE'2024-01-01'", True),
    ("'1.5' BETWEEN 1 AND 1.4D", False),
    ("'1.5' BETWEEN 1 AND 1.6D", True),
    ("2 BETWEEN '1.5' AND 3", True),
    ("CASE '1.5' WHEN 1 THEN 'a' WHEN 2 THEN 'b' ELSE 'c' END", "a"),
]


@pytest.mark.parametrize(("expression", "expected"), SPARK)
def test_text_compared_with_a_number_gives_sparks_answer(spark, expression, expected):
    assert one(spark, expression) == expected


QUERIES = [
    ("SELECT i FROM kf_c254 WHERE t = i ORDER BY i", [(1,)]),
    ("SELECT i, t = 1, u = 2, t = d, t > i FROM kf_c254 ORDER BY i",
     [(0, None, None, None, None), (1, True, True, True, False)]),
    ("SELECT count(*) FROM kf_c254 WHERE t = 1", [(1,)]),
    ("SELECT i FROM kf_c254 WHERE i IN ('1', '1.0') ORDER BY i", [(1,)]),
    ("SELECT i FROM kf_c254 WHERE t IN (1, 2) ORDER BY i", []),
    ("SELECT a.i FROM kf_c254 a JOIN kf_c254 b ON a.t = b.d ORDER BY 1", [(1,)]),
    ("SELECT i FROM kf_c254 WHERE t BETWEEN 1 AND 2", [(1,)]),
]


@pytest.mark.parametrize(("query", "expected"), QUERIES)
def test_text_columns_compared_with_numbers_give_sparks_rows(spark, query, expected):
    assert [tuple(r) for r in spark.sql(query).collect()] == expected


def test_the_dataframe_api_compares_text_with_a_number_as_spark(spark):
    from pyspark.sql import functions as F  # noqa: N812 - PySpark's own name for it

    frame = spark.createDataFrame([(1, "1.5"), (0, ""), (2, "1e2"), (3, ".5")], "i int, t string")
    for condition, expected in [(F.col("t") == 1, [1]), (F.col("t") == 0, [3]), (F.col("t").isin(1, 2), []),
                                (F.lit("1.5") == F.col("i"), [1])]:
        assert sorted(r.i for r in frame.filter(condition).collect()) == expected
    rows = frame.selectExpr("i", "t = 1").orderBy("i").collect()
    assert [tuple(r) for r in rows] == [(0, None), (1, True), (2, None), (3, False)]
    assert rows[0].asDict() == {"i": 0, "(t = 1)": None}


@pytest.mark.parametrize(("text", "expected"), [
    (".5", 0), (".", 0), ("-.5", 0), ("+", None), ("1.", 1), (" 1 ", 1), ("-", None), ("1.2.3", None),
    ("\t1\n", 1), ("99999999999", None), ("", None),
])
def test_text_cast_to_an_int_reads_as_spark_reads_it(spark, text, expected):
    escaped = text.replace("\t", "\\t").replace("\n", "\\n")
    assert one(spark, f"CAST('{escaped}' AS INT)") == expected


def test_text_compared_with_text_and_numbers_with_numbers_are_unchanged(spark):
    assert one(spark, "'10' < '9'") is True
    assert one(spark, "10 < 9.5") is False
    assert one(spark, "CAST(1 AS DECIMAL(10, 2)) = 1") is True


def _rewritten_size(query: str) -> int:
    import sqlglot
    from sqlglot.schema import MappingSchema

    from fabric_desktop.spark_text_compare import spark_text_comparisons
    from fabric_desktop.sql import duckdb_sql
    schema = MappingSchema({"t": {"s": "VARCHAR", "n": "INTEGER"}}, dialect="duckdb", normalize=False)
    return len(duckdb_sql(spark_text_comparisons(sqlglot.parse_one(query, read="duckdb"), None, schema)))


def test_nested_comparisons_grow_the_sql_linearly():
    # Each text side is read once (bound through a lambda), so nesting adds a fixed amount per level; it doubled.
    sizes = []
    for depth in range(1, 9):
        condition = "s = 1"
        for _ in range(depth):
            condition = f"CAST(({condition}) AS VARCHAR) = 1"
        sizes.append(_rewritten_size(f"SELECT {condition} FROM t"))
    steps = {b - a for a, b in zip(sizes, sizes[1:], strict=False)}
    assert len(steps) == 1, sizes


def test_a_nested_simple_case_casts_its_operand_once():
    # The operand was copied into every WHEN, so four WHENs nested four deep made a 380-character query 1 MB.
    ratios = []
    for depth in range(1, 5):
        value = "'x'"
        for _ in range(depth):
            value = f"CASE s WHEN 1 THEN {value} WHEN 2 THEN {value} WHEN 3 THEN {value} WHEN 4 THEN {value} END"
        query = f"SELECT {value} FROM t"
        ratios.append(_rewritten_size(query) / len(query))
    assert max(ratios) < 5, ratios


def test_a_column_named_like_the_rewrites_lambda_is_left_as_it_was(spark):
    spark.createDataFrame([("1", 1)], "kf_text_cmp string, i int").createOrReplaceTempView("kf_c254_lambda")
    try:
        assert spark.sql("SELECT kf_text_cmp = i FROM kf_c254_lambda").collect()[0][0] is True
    finally:  # while it is a view, no statement on the connection is rewritten
        spark.catalog.dropTempView("kf_c254_lambda")


_NULL_SAFE = [(0, "1", 1, 1, 1.0, "1.00"), (1, "abc", 1, 1, 1.0, "1.00"), (2, None, None, None, None, None),
              (3, None, 1, 1, 1.0, "1.00"), (4, "abc", None, None, None, None), (5, "1.5", 1, 2, 1.5, "1.50")]


@pytest.mark.parametrize(("number", "expected"), [
    ("i", [True, False, True, False, True, True]),
    ("l", [True, False, True, False, True, False]),
    ("d", [True, False, True, False, True, True]),
    ("m", [True, False, True, False, True, True]),
])
def test_eq_null_safe_of_text_and_a_number_gives_sparks_answer(spark, number, expected):
    # Spark casts the text to the number's type as = does ('abc' and '' are NULL; text and a decimal compare as
    # doubles), then <=>: NULL <=> NULL is true, NULL <=> 1 false. DuckDB stopped on 'abc'.
    from pyspark.sql import functions as F  # noqa: N812 - PySpark's own name for it

    frame = spark.createDataFrame(_NULL_SAFE, "id int, t string, i int, l bigint, d double, m string").withColumn(
        "m", F.col("m").cast("decimal(10,2)"))
    frame.createOrReplaceTempView("kf_c254_ns")
    for left, right in ((frame.t, frame[number]), (frame[number], frame.t), (F.col("t"), F.col(number)),
                        (F.col(number), F.col("t"))):
        rows = frame.select("id", left.eqNullSafe(right).alias("v")).orderBy("id").collect()
        assert [r.v for r in rows] == expected
        assert sorted(r.id for r in frame.filter(left.eqNullSafe(right)).collect()) == [
            i for i, e in enumerate(expected) if e]
    for query in (f"SELECT t <=> {number} AS v FROM kf_c254_ns ORDER BY id",
                  f"SELECT {number} <=> t AS v FROM kf_c254_ns ORDER BY id"):
        assert [r.v for r in spark.sql(query).collect()] == expected


def test_eq_null_safe_of_text_and_a_literal_number_keeps_sparks_name(spark):
    frame = spark.createDataFrame([("1",), ("abc",), (None,)], "t string")
    rows = frame.select(frame.t.eqNullSafe(1), frame["t"].eqNullSafe(1.5)).collect()
    assert [tuple(r) for r in rows] == [(True, False), (False, False), (False, False)]
    assert frame.select(frame.t.eqNullSafe(1)).columns == ["(t <=> 1)"]
