"""spark_api at its exact edges (boundary re-score of batch 10, mutation batch 159,
docs/quality/mutation-testing.md): a weight of 0, weights adding up to 0, exactly 10000 crosstab
columns, an empty regex in backticks, and fractions of exactly 0 and 1."""

from __future__ import annotations

import pytest
from duckdb.experimental.spark.errors import IllegalArgumentException
from duckdb.experimental.spark.exception import ContributionsAcceptedError

from fabric_desktop import spark_api, spark_local


@pytest.fixture(scope="module")
def spark():
    spark_local._alias_pyspark()
    return spark_local.session()


def test_a_weight_of_zero_is_allowed(spark):
    df = spark.createDataFrame([(1, 2)], ["a", "b"])
    assert [p.count() for p in spark_api.randomSplit(df, [0.0, 1.0], seed=1)] == [0, 1]


def test_weights_adding_up_to_zero_are_refused(spark):
    df = spark.createDataFrame([(1, 2)], ["a", "b"])
    with pytest.raises(IllegalArgumentException, match="Sum of weights must be positive"):
        spark_api.randomSplit(df, [0.0], seed=1)


def test_a_crosstab_of_exactly_10000_values_is_made(spark):
    df = spark.createDataFrame([(1, i) for i in range(10000)], ["a", "b"])
    out = spark_api.crosstab(df, "a", "b")
    assert out.count() == 1 and len(out.columns) == 10001


def test_two_backquotes_are_an_empty_regex(spark):
    df = spark.createDataFrame([(1, 2)], ["a", "b"])
    with pytest.raises(ValueError, match="no column matches ``"):
        spark_api.colRegex(df, "``")


def test_fractions_of_exactly_zero_and_one(spark):
    df = spark.createDataFrame([(1, 2)], ["a", "b"])
    assert spark_api.sampleBy(df, "a", {1: 1.0}, seed=1).count() == 1
    assert spark_api.sampleBy(df, "a", {1: 0.0}, seed=1).count() == 0


def _twenty_five_lines():
    steps = [
        "step 1",
        "step 2",
        "step 3",
        "step 4",
        "step 5",
        "step 6",
        "step 7",
        "step 8",
        "step 9",
        "step 10",
        "step 11",
        "step 12",
        "step 13",
        "step 14",
        "step 15",
        "step 16",
        "step 17",
        "step 18",
        "step 19",
        "step 20",
        "step 21",
    ]
    raise ContributionsAcceptedError(", ".join(steps))  # exactly 25 lines: a real body


def test_a_body_of_exactly_25_lines_is_a_real_body():
    import inspect
    assert inspect.getsource(_twenty_five_lines).count("\n") == 25
    assert spark_api._not_implemented(_twenty_five_lines, ContributionsAcceptedError) is False
