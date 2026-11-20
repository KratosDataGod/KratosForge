"""Spark on this PC at its boundaries (mutation batch 205, a boundary re-score of batch 8,
docs/quality/mutation-testing.md): a query profile lists exactly PROFILE_NODES steps and counts the rest,
and a row shorter than its schema has None for the missing fields, so a NOT NULL one is refused by name."""

from __future__ import annotations

import pytest
from duckdb.experimental.spark.sql import types as T

from fabric_desktop import spark_local


def _plan(operators: int) -> dict:
    node: dict = {"operator_name": "PROJECTION", "operator_timing": 0.001, "operator_cardinality": 1}
    root = node
    for _ in range(operators - 1):
        child = {"operator_name": "FILTER", "operator_timing": 0.001, "operator_cardinality": 1}
        node["children"] = [child]
        node = child
    return {"latency": 0.5, **root}


@pytest.mark.parametrize("operators, listed, more", [
    (spark_local.PROFILE_NODES, spark_local.PROFILE_NODES, 0),
    (spark_local.PROFILE_NODES + 1, spark_local.PROFILE_NODES, 1),
])
def test_a_profile_lists_up_to_profile_nodes_steps_and_counts_the_rest(operators, listed, more):
    got = spark_local._read_profile(_plan(operators))
    assert len(got["steps"]) == listed and got["more_steps"] == more


def test_a_row_shorter_than_its_schema_is_refused_by_the_field_it_lacks():
    schema = T.StructType([T.StructField("a", T.LongType(), True), T.StructField("b", T.LongType(), False)])
    with pytest.raises(ValueError, match=r"FIELD_NOT_NULLABLE_WITH_NAME\] field b:"):
        spark_local.check_not_null([(1,)], schema)
    spark_local.check_not_null([(1, 2)], schema)  # a full row passes
