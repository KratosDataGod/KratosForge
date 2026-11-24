"""spark_decimals.py on today's main: the boundary mutants a re-score of batch 163 found unchecked. A
whole-number literal is an INT from -2^31 to 2^31 - 1 and a BIGINT from -2^63 to 2^63 - 1 (Spark's parser
reads the sign with the digits), so arithmetic on the literals at each end wraps in that type with ANSI
mode off; and a double is rounded to anything from -20 to 20 places. The kernel runs on this PC only."""

from __future__ import annotations

import pytest
import sqlglot

from fabric_desktop import spark_decimals
from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def one(spark, expression: str):
    return spark.sql(f"SELECT {expression} AS v").collect()[0][0]


@pytest.mark.parametrize("expression, value", [
    ("-2147483648 - 1", 2147483647),                    # the lowest INT literal: an INT, so it wraps
    ("2147483648 + 1", 2147483649),                     # one past the highest INT: a BIGINT
    ("-9223372036854775808 - 1", 9223372036854775807),  # the lowest BIGINT literal: a BIGINT, so it wraps
    ("9223372036854775808 + 1", 9223372036854775809),   # one past the highest BIGINT: a decimal
])
def test_literals_at_each_end_of_int_and_bigint_have_spark_type(spark, expression, value):
    assert one(spark, expression) == value


@pytest.mark.parametrize("places, value", [(20, 1.5), (-20, 0.0)])
def test_a_double_rounds_to_twenty_places_either_way(spark, places, value):
    assert one(spark, f"round(1.5D, {places})") == value


def test_a_literal_is_an_int_or_a_bigint_up_to_each_types_ends():
    # Through spark.sql an earlier step casts such a literal, so this reads the rule itself.
    def rank(text):
        return spark_decimals._whole_rank(sqlglot.parse_one(text, read="spark"), ("int", 0))
    assert [rank(t) for t in ("-2147483648", "-2147483649", "-9223372036854775808", "-9223372036854775809")] == [
        10, 20, 20, None]
    assert [rank(t) for t in ("2147483647", "2147483648", "9223372036854775807", "9223372036854775808")] == [
        10, 20, 20, None]
