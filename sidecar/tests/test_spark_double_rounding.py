"""Spark's round and bround of a DOUBLE or FLOAT on this PC give Spark's answer (WR-252).

Spark's RoundBase reads a double as BigDecimal(Double.toString(x)), rounds that decimal half up (round)
or half even (bround) and turns it back into a double; a float is widened to a double first and narrowed
after. DuckDB's ROUND scales the double in binary, so round(0.285D, 2) was 0.28 here where Spark gives
0.29: a silent different answer. The expected values below were read from Spark 3.5.3 (local[1]); the
property test models Spark with Python's Decimal of the shortest text of the double, as Java's is.
"""

from __future__ import annotations

import math
import random
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

import pytest

from fabric_desktop.notebook import Kernel
from fabric_desktop.spark_decimals import Refused


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def one(spark, expression: str):
    return spark.sql(f"SELECT {expression} AS v").collect()[0][0]


# Read from Spark 3.5.3: SELECT <expression>.
SPARK = [
    ("round(0.285D, 2)", 0.29),
    ("round(1.005D, 2)", 1.01),
    ("round(-0.285D, 2)", -0.29),
    ("round(2.675D, 2)", 2.68),
    ("round(123.456D, -1)", 120.0),
    ("bround(0.285D, 2)", 0.28),
    ("bround(2.675D, 2)", 2.68),
    ("bround(0.125D, 2)", 0.12),
    ("round(-0.001D, 2)", 0.0),
    ("round(2.5D)", 3.0),
    ("bround(2.5D)", 2.0),
    ("bround(3.5D)", 4.0),
    ("round(0.285D)", 0.0),
    ("round(1.5e20D, -19)", 1.5e20),
    ("bround(2.5e19D, -19)", 2e19),
    ("bround(3.5e19D, -19)", 4e19),
    ("round(15.0D, -1)", 20.0),
    ("bround(25.0D, -1)", 20.0),
    ("round(1e-5D, 5)", 1e-5),
    ("bround(0.00125D, 4)", 0.0012),
    ("round(1.23456789e-15D, 18)", 1.235e-15),
    ("round(1e300D, 2)", 1e300),
    ("round(0.285F, 2)", 0.2800000011920929),  # the FLOAT 0.28, read back as a Python float
]


@pytest.mark.parametrize(("expression", "expected"), SPARK)
def test_round_and_bround_of_a_double_give_sparks_answer(spark, expression, expected):
    assert one(spark, expression) == expected


def test_nan_infinity_and_null_are_kept(spark):
    assert math.isnan(one(spark, "round(CAST('NaN' AS DOUBLE), 2)"))
    assert one(spark, "bround(CAST('Infinity' AS DOUBLE), 2)") == math.inf
    assert one(spark, "round(CAST(NULL AS DOUBLE), 1)") is None


def test_a_float_stays_a_float(spark):
    assert one(spark, "typeof(round(0.285F, 2))") == "float"
    assert one(spark, "typeof(bround(0.285D, 2))") == "double"


def model(x: float, places: int, mode: str) -> float:
    text = Decimal(repr(x))
    return float(text.quantize(Decimal(1).scaleb(-places), rounding=mode))


def test_columns_of_doubles_round_as_spark_models_them(spark):
    rng = random.Random(252)
    values = [rng.randint(-10 ** 6, 10 ** 6) / 1000 + 0.0005 for _ in range(60)]
    values += [rng.randint(-99999, 99999) * 10.0 ** rng.randint(-6, 2) + 0.5 * 10.0 ** rng.randint(-6, 2)
               for _ in range(60)]
    values += [rng.uniform(-1, 1) * 10.0 ** rng.randint(-12, 16) for _ in range(60)]
    rows = ", ".join(f"({i}, CAST('{v!r}' AS DOUBLE))" for i, v in enumerate(values))
    got = spark.sql(f"SELECT i, round(d, 2), bround(d, 2), round(d, -1), bround(d, 3) "
                    f"FROM VALUES {rows} AS t(i, d) ORDER BY i").collect()
    for (i, *answers), x in zip(got, values, strict=True):
        expected = [model(x, 2, ROUND_HALF_UP), model(x, 2, ROUND_HALF_EVEN), model(x, -1, ROUND_HALF_UP),
                    model(x, 3, ROUND_HALF_EVEN)]
        assert [a + 0.0 for a in answers] == [e + 0.0 for e in expected], (i, x)


def test_the_dataframe_api_rounds_a_double_column_as_spark(spark):
    from pyspark.sql import functions as F  # noqa: N812 - PySpark's own name for it

    frame = spark.createDataFrame([(0.285, 1.005), (2.675, -0.285)], "a double, b double")
    rows = frame.select(F.round("a", 2).alias("r"), F.bround("a", 2).alias("br"), F.round(F.col("b"), 2),
                        (F.round("a", 2) * 2).alias("x")).collect()
    assert [tuple(r) for r in rows] == [(0.29, 0.28, 1.01, 0.58), (2.68, 2.68, -0.29, 5.36)]
    assert frame.select(F.round("b", 2)).columns == ["round(b, 2)"]


def test_a_view_of_doubles_keeps_sparks_names_and_answers(spark):
    spark.createDataFrame([(0.285, 1.005), (2.675, -0.285)], "a double, b double").createOrReplaceTempView("kf_dr")
    rows = spark.sql("SELECT round(a, 2), bround(b, 2), round(a * 1, 2) + 1 FROM kf_dr").collect()
    assert [tuple(r) for r in rows] == [(0.29, 1.0, 1.29), (2.68, -0.28, 3.68)]
    assert rows[0].asDict() == {"round(a, 2)": 0.29, "bround(b, 2)": 1.0, "(round((a * 1), 2) + 1)": 1.29}


def test_more_places_than_written_here_are_refused_by_name(spark):
    with pytest.raises(Refused, match=r"round\(\) of a double to 25 places can\'t run on this PC.*round\(CAST\(x AS DECIMAL\(38, 30\)\), 25\)"):
        one(spark, "round(0.285D, 25)")
    with pytest.raises(Refused, match=r"bround\(CAST\(x AS DECIMAL\(38, 0\)\), -21\)"):
        one(spark, "bround(0.285D, -21)")


def test_a_decimal_still_rounds_as_a_decimal(spark):
    assert one(spark, "round(0.285, 2)") == Decimal("0.29")
    assert one(spark, "bround(0.285, 2)") == Decimal("0.28")
