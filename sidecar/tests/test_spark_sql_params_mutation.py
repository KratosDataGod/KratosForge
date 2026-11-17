"""spark.sql parameter binding edges that mutation testing found untested (spark_sql_params)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fabric_desktop import spark_sql_params as p


class Widgets:
    """A stand-in for dbutils.widgets with one widget, x = 'X'."""

    defined = {"x": ""}
    passed: dict = {}

    def get(self, name):
        if name != "x":
            raise ValueError(f"No widget named {name}.")
        return "X"

    def getArgument(self, name, default=None):
        return "X" if name == "x" else default


@pytest.mark.parametrize("value, want", [
    (2**31 - 1, "2147483647"), (2**31, "2147483648L"),
    (-(2**31), "-2147483648"), (-(2**31) - 1, "-2147483649L"),
])
def test_an_int_is_int_inside_32_bits_and_bigint_outside(value, want):
    assert p.literal(value, "x") == want


@pytest.mark.parametrize("value, want", [
    (float("nan"), "CAST('NaN' AS DOUBLE)"),
    (float("inf"), "CAST('Infinity' AS DOUBLE)"),
    (float("-inf"), "CAST('-Infinity' AS DOUBLE)"),
])
def test_nan_and_infinities_are_spark_double_literals(value, want):
    assert p.literal(value, "x") == want


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_a_decimal_spark_cant_hold_is_refused(value):
    with pytest.raises(ValueError, match="DECIMAL"):
        p.literal(value, "args['d']")


def test_a_question_mark_inside_a_string_isnt_a_marker():
    assert p.bind("SELECT '?' AS q, ? AS v", [1]) == "SELECT '?' AS q, 1 AS v"


@pytest.mark.parametrize("query", ["SELECT (x) FROM t", "SELECT 1 :", "a:b"])
def test_only_a_colon_right_before_a_name_with_no_path_before_it_is_a_marker(query):
    assert p.bind(query, {}) == query


def test_a_colon_with_a_space_before_the_name_isnt_a_marker():
    # Spark needs ":name" written together; ": lim" is a stray colon and a column
    assert p._markers("SELECT : lim") == []


def test_a_marker_at_the_very_start_binds():
    assert p.bind(":a", {"a": 1}) == "1"


def test_one_dataframe_used_twice_is_registered_once():
    df, other = object(), object()
    registered = []
    out = p.format_query("SELECT * FROM {a} JOIN {b} USING (k) JOIN {c} USING (k)", {"a": df, "b": df, "c": other},
                         lambda d, name: registered.append((d, name)), lambda v: v in (df, other), lambda v: False)
    assert [d for d, _ in registered] == [df, other]
    first, second = registered[0][1], registered[1][1]
    assert first != second
    assert out == f"SELECT * FROM {first} JOIN {first} USING (k) JOIN {second} USING (k)"


def test_get_argument_in_unreadable_sql_leaves_the_query_as_it_is():
    query = "SELECT getArgument('x"
    assert p.bind_widgets(query, Widgets()) == query


@pytest.mark.parametrize("query", [
    "SELECT getArgument", "SELECT getArgument('x'", "SELECT getArgument('x', 'd'",
])
def test_a_get_argument_call_cut_short_is_left_alone(query):
    assert p.bind_widgets(query, Widgets()) == query


def test_only_get_argument_calls_are_filled():
    assert p.bind_widgets("SELECT upper('x'), getArgument('x') AS v", Widgets()) == "SELECT upper('x'), 'X' AS v"
