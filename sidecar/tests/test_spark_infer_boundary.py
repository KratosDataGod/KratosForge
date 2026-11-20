"""spark_infer at its exact edges (boundary re-score of batch 10, mutation batch 159,
docs/quality/mutation-testing.md): bigint's two ends, decimal(38,18)'s limit, and rows with fewer
names or values than fields."""

from __future__ import annotations

import decimal

import pytest
from duckdb.experimental.spark.sql import types as T

from fabric_desktop import spark_infer as si


def test_bigints_two_ends_are_longs_and_one_past_is_refused():
    assert si._infer(-2**63) == T.LongType()
    assert si._infer(2**63 - 1) == T.LongType()
    for past in (-2**63 - 1, 2**63):
        with pytest.raises(si._Unsupported):
            si._infer(past)


def test_bigints_two_ends_share_a_shape_and_one_past_does_not():
    assert si._shape(-2**63) is int and si._shape(2**63 - 1) is int
    assert si._shape(-2**63 - 1) == "big" and si._shape(2**63) == "big"


def test_a_decimal_of_exactly_twenty_whole_digits_is_out_of_range():
    with pytest.raises(ArithmeticError, match="NUMERIC_VALUE_OUT_OF_RANGE"):
        si._spark_decimal(decimal.Decimal(10) ** 20)
    just_under = decimal.Decimal(10) ** 20 - 1
    assert si._spark_decimal(just_under) == just_under


def test_as_many_names_as_values_is_a_row():
    t = si._row_type([1, "x"], ["a", "b"])
    assert [f.name for f in t.fields] == ["a", "b"]
    with pytest.raises(IndexError):
        si._row_type([1], ["a", "b"])


def test_fewer_names_than_values_name_the_rest_by_place():
    assert [f.name for f in si._struct((1, 2), ["a"]).fields] == ["a", "_2"]


def test_fewer_values_than_fields_fill_with_null():
    t = T.StructType([T.StructField("a", T.LongType(), True), T.StructField("b", T.LongType(), True)])
    assert si._fit([1], t) == (1, None)
