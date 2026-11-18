"""Spark 3.5's array and string functions at their edges on this PC, value-checked against Spark 3.5.3 (see
spark_values.py).

array_join, size and array_size, reverse, array_insert, split, locate and instr, substring_index, concat_ws,
format_number, contains, upper and lower, format_string and array_distinct: the queries in
tools/spark_value_cases/arrays_strings.txt, each answered by Spark 3.5.3 in
tests/fixtures/spark_3_5_3_arrays_strings.json. What this PC can't answer as Spark does is refused by name
below, and listed in docs/sql-compatibility.md."""

from __future__ import annotations

import pytest
from spark_values import Refused, check, load

from fabric_desktop.notebook import Kernel

REFUSED = [
    Refused("split empty match", r"split\(.*'(\||x\*|b\*)'\)", r"pattern that can match empty text",
            "`split` with a pattern written in the query that can match empty text"),
    Refused("format_number pattern", r"format_number\(.*, '", r"format_number\(\) with a pattern",
            "`format_number` with a pattern"),
    Refused("format_string array", r"format_string\('%s', array\(", r"array, map or struct isn't written as text",
            "`format_string` and `printf` with `%s` of an array, map or struct"),
]


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_array_and_string_functions_answer_as_spark_3_5_or_are_refused_by_name(spark):
    check(spark, load("arrays_strings"), REFUSED)
