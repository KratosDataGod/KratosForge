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


def _one(spark, sql: str) -> object:
    return spark.sql(sql).collect()[0][0]


@pytest.mark.parametrize("position", [2_000_000_000, -2_000_000_000, 10_000_003])
def test_array_insert_refuses_padding_past_ten_million_nulls(spark, position: int):
    # Spark pads up to its array limit; a billion NULLs here would use up the PC's memory, so it stops by name.
    with pytest.raises(Exception, match="array_insert would pad the array with more than 10,000,000 NULLs"):
        _one(spark, f"SELECT array_insert(array(1), {position}, 5)")


def test_array_insert_still_pads_within_the_bound(spark):
    assert len(_one(spark, "SELECT array_insert(array(1), 1000, 5)")) == 1000


@pytest.mark.parametrize("places", ["19", "50", "100000000", "1" + "0" * 5000])
def test_format_number_refuses_more_than_eighteen_places_by_name(spark, places: str):
    with pytest.raises(Exception, match="format_number with more than 18 decimal places isn't written here"):
        _one(spark, f"SELECT format_number(1.5, {places})")


def test_format_number_at_eighteen_places(spark):
    assert _one(spark, "SELECT format_number(1.5, 18)") == "1.500000000000000000"


@pytest.mark.parametrize("call", ["substring_index({0}, 'aa', 2)", "substring_index({0}, '.', 2)",
                                  "array_insert({0}, 1, 0)"])
def test_nested_calls_grow_the_sql_linearly(call: str):
    from fabric_desktop import sql
    sizes = []
    for depth in range(1, 6):
        expression = "array(1)" if call.startswith("array") else "s"
        for _ in range(depth):
            expression = call.format(expression)
        sizes.append(len(sql.parse(f"SELECT {expression} FROM t")[0].sql(dialect=sql.KfDuckDB)))
    steps = [b - a for a, b in zip(sizes, sizes[1:], strict=False)]
    assert max(steps) <= 1.2 * min(steps), sizes


def test_crosstab_on_an_array_column_still_labels_its_rows(spark):
    # format_string's %s and array_join refuse an array by name; crosstab's labels keep DuckDB's text of it,
    # which is what Spark gives there.
    rows = spark.sql("SELECT array(id % 2, 1) a, id % 3 b FROM range(4)").crosstab("a", "b").collect()
    assert sorted(r[0] for r in rows) == ["[0, 1]", "[1, 1]"]


def test_initcap_keeps_title_case_on_both_paths(spark):
    assert _one(spark, "SELECT initcap('hello wORLD')") == "Hello World"
    assert _one(spark, "SELECT initcap('ßa \u01c6ungla')") == "ßa \u01c5ungla"  # the one-letter dž is ǅ
