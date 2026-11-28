"""sql_dialects.py: parse_one's refusals for a bad number and for a query too deep for Python's stack,
and the small helpers around them."""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot.errors import ParseError

from fabric_desktop import sql_dialects


def test_parse_one_refuses_a_number_spark_cant_read_as_final():
    with pytest.raises(ParseError, match="isn't written the way Spark SQL reads one") as caught:
        sql_dialects.parse_one("SELECT 1e101.5BD", read="spark")
    assert caught.value.final is True


@pytest.mark.parametrize("entry", ["parse", "parse_one"])
def test_running_out_of_stack_is_refused_as_too_deep(monkeypatch, entry):
    def deep(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(sqlglot, entry, deep)
    with pytest.raises(ParseError, match="nested too deeply") as caught:
        getattr(sql_dialects, entry)("SELECT 1", read="spark")
    assert caught.value.too_deep is True and str(caught.value) == sql_dialects.TOO_DEEP


def test_type_calls_at_the_limit_pass_and_one_more_is_refused():
    depth = sql_dialects.TYPE_CALL_DEPTH
    ok = "SELECT " + "string(" * depth + "1" + ")" * depth + " + (((((((((((((1)))))))))))))"
    assert sql_dialects.parse_one(ok, read="spark") is not None  # plain brackets don't count
    with pytest.raises(ParseError, match="nested too deeply"):
        sql_dialects.parse_one("SELECT " + "string(" * (depth + 1) + "1" + ")" * (depth + 1), read="spark")


def test_a_stray_closing_bracket_is_left_for_sqlglot():
    # More ) than ( in the tokens: the depth check doesn't go negative, sqlglot refuses the text itself.
    with pytest.raises(ParseError):
        sql_dialects.parse_one("SELECT 1))" + "(" * 13 + "x", read="spark")


def test_text_that_cant_be_tokenized_is_left_for_sqlglot():
    with pytest.raises(sqlglot.errors.SqlglotError):
        sql_dialects.parse("SELECT " + "(" * 14 + "'unclosed", read="spark")


def test_parse_problem_drops_terminal_escapes():
    assert sql_dialects.parse_problem(ValueError("near \x1b[4mFROM\x1b[0m here")) == "near FROM here"


def test_dialects_by_name_and_the_default():
    assert sql_dialects.get(None) == {"id": "spark", **sql_dialects.DIALECTS["spark"]}
    assert sql_dialects.get("")["id"] == sql_dialects.DEFAULT
    assert [sql_dialects.sqlglot_name(d) for d in ("warehouse", "endpoint", "sqldb", "duckdb")] == \
        ["tsql", "tsql", "tsql", "duckdb"]
    with pytest.raises(ValueError, match=r"Unknown SQL dialect 'oracle'\. Dialects: spark, warehouse"):
        sql_dialects.get("oracle")


def test_other_dialects_skip_the_spark_argument_check():
    # T-SQL's DATEDIFF with a missing argument is sqlglot's own to judge; only Spark SQL names it.
    sql_dialects._check_arguments([None, sqlglot.parse_one("SELECT 1")], "tsql")
    sql_dialects._check_arguments(["not an expression"], "spark")
