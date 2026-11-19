"""The names Spark gives unnamed columns of a Spark SQL query (spark_sql_names), for the expressions the
rest of the suite leaves out: intervals, struct and map constructors, trim with characters, window
orderings and calls written the less usual ways. Each name is the one Spark 3.5 shows."""

from __future__ import annotations

import pytest

from fabric_desktop import spark_sql_names
from fabric_desktop import sql


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("SELECT INTERVAL '1 day'", "INTERVAL '1' DAY"),
        ("SELECT struct(1, 'a')", "struct(1, a)"),
        ("SELECT named_struct('a', 1)", "named_struct(a, 1)"),
        ("SELECT map(1, 2)", "map(1, 2)"),
        ("SELECT btrim('xax', 'x')", "btrim(xax, x)"),
        ("SELECT trim(BOTH 'x' FROM 'xax')", "TRIM(BOTH x FROM xax)"),
        (
            "SELECT row_number() OVER (ORDER BY id DESC) FROM range(2)",
            "row_number() OVER (ORDER BY id DESC NULLS LAST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        ),
        (
            "SELECT row_number() OVER (ORDER BY id NULLS LAST) FROM range(2)",
            "row_number() OVER (ORDER BY id ASC NULLS LAST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        ),
        (
            "SELECT unix_timestamp()",
            "unix_timestamp(current_timestamp(), yyyy-MM-dd HH:mm:ss)",
        ),
        (
            "SELECT to_date(CAST('2020-01-01' AS TIMESTAMP))",
            "to_date(CAST(2020-01-01 AS TIMESTAMP))",
        ),
        ("SELECT 'a' || 'b'", "concat(a, b)"),
    ],
)
def test_the_column_is_named_as_spark_names_it(query, name):
    assert sql.run(query).column_names == [name]


def test_text_that_cant_be_tokenized_keeps_no_names():
    import sqlglot

    tree = sqlglot.parse_one("SELECT abs(1)", read="spark")
    spark_sql_names.remember([tree], "SELECT 'unclosed")
    assert spark_sql_names.kept(tree.expressions[0]) == {}


def name_of_tree(text: str) -> str | None:
    """The name for an expression read without remember(): it comes from the tree alone."""
    import sqlglot

    return spark_sql_names.column_name(sqlglot.parse_one(text, read="spark"))


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("struct(1, a AS b, c)", "struct(1 AS col1, a AS b, c)"),
        ("named_struct('a', 1)", "struct(1 AS a)"),
        ("map(1, 2)", "map(1, 2)"),
        ("btrim('xax', 'x')", "btrim(xax, x)"),
        ("count(*)", "count(1)"),
        (
            "row_number() OVER (ORDER BY a)",
            "row_number() OVER (ORDER BY a ASC NULLS FIRST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        ),
        (
            "sum(a) OVER (PARTITION BY b ORDER BY a DESC)",
            "sum(a) OVER (PARTITION BY b ORDER BY a DESC NULLS LAST RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        ),
        ("foo(1)", "foo(1)"),
        (
            "to_date(x)",
            "x",
        ),  # with no name kept, to_date reads as the wrapper sqlglot adds, named by its value
    ],
)
def test_a_tree_read_without_its_text_is_named_from_the_tree(text, name):
    assert name_of_tree(text) == name


@pytest.mark.parametrize(
    "text",
    [
        "1 IN (SELECT 1)",
        "INTERVAL x DAY",
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY a, b)",
        "s.f",
    ],
)
def test_an_expression_it_cant_name_is_left_unnamed(text):
    assert name_of_tree(text) is None


def test_an_ordering_takes_sparks_default_null_order():
    from sqlglot import exp

    printer = spark_sql_names._Printer(set())
    assert printer.ordered(exp.column("a")) == "a ASC NULLS FIRST"
    assert (
        printer.ordered(exp.Ordered(this=exp.column("a"), desc=True))
        == "a DESC NULLS LAST"
    )
    assert (
        printer.ordered(exp.Ordered(this=exp.column("a"), desc=False))
        == "a ASC NULLS FIRST"
    )
