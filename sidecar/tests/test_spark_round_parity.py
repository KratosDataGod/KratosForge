"""round, bround, ceil and floor as Spark 3.5 types them: a Python float's F.lit is a DOUBLE, a double's round
in a frame with no double column is Spark's, and text is cast to a double first (ImplicitTypeCasts)."""
from __future__ import annotations

import math

import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def spark():
    return Kernel().namespace["spark"]


def test_round_of_a_double_in_a_frame_of_whole_numbers_is_sparks(spark):
    from pyspark.sql import functions as F  # noqa: N812 - PySpark's own name for it
    frame = spark.createDataFrame([(1,)], "n int")
    row = frame.select(F.round(F.lit(0.285).cast("double"), 2).alias("r"), F.bround(F.lit(0.285), 2).alias("b"),
                       F.round(F.lit(2.5), 0).alias("h")).collect()[0]
    assert (row.r, row.b, row.h) == (0.29, 0.28, 3.0)


def test_a_float_lit_is_a_double_and_keeps_sparks_name(spark):
    from pyspark.sql import functions as F  # noqa: N812 - PySpark's own name for it
    frame = spark.createDataFrame([(1.0,)], "x double")
    out = frame.select(F.lit(0.285), F.round(F.lit(0.285), 2), F.lit(1.0) + F.col("x"), F.lit(float("nan")),
                       F.lit(float("-inf")), F.lit(1e20), F.lit(1), F.lit(True))
    assert out.columns == ["0.285", "round(0.285, 2)", "(1.0 + x)", "NaN", "-Infinity", "1.0E20", "1", "true"]
    assert [t for _, t in out.dtypes] == ["double"] * 6 + ["int", "boolean"]
    row = out.collect()[0]
    assert row[0] == 0.285 and isinstance(row[1], float) and row[1] == 0.29 and row[2] == 2.0
    assert math.isnan(row[3]) and row[4] == float("-inf") and row[5] == 1e20


@pytest.mark.parametrize("query, kind, value", [
    ("SELECT round('0.285', 2) AS v", "double", 0.29),
    ("SELECT bround('0.285', 2) AS v", "double", 0.28),
    ("SELECT round(s, 2) AS v FROM VALUES ('0.285') t(s)", "double", 0.29),
    ("SELECT ceil('1.5') AS v", "bigint", 2),
    ("SELECT floor(' -1.5 ') AS v", "bigint", -2),
    ("SELECT round('abc', 2) AS v", "double", None),        # not a number: NULL, as Spark's cast with ANSI off
    ("SELECT round(CAST(NULL AS STRING), 2) AS v", "double", None),
])
def test_text_is_cast_to_a_double_first(spark, query, kind, value):
    out = spark.sql(query)
    assert out.dtypes == [("v", kind)]
    assert out.collect()[0].v == value
