"""sql_params.py paths that mutation testing (batch 76, docs/quality/mutation-testing.md) showed no test
pinned: the limits and their edges, each refusal of a malformed parameter list, which dialect each call
reads by default, and the exact edges of strings, comments and quoted names where no parameter is looked for."""

from __future__ import annotations

import pytest

from fabric_desktop import sql_params


def p(name="a", kind="text", value="x"):
    return {"name": name, "type": kind, "value": value}


def test_the_limits_are_50_parameters_of_4000_characters():
    assert sql_params.MAX_PARAMS == 50 and sql_params.MAX_VALUE == 4000


def test_fifty_parameters_are_taken_and_fifty_one_refused():
    assert len(sql_params.typed([p(f"p{i}") for i in range(50)])) == 50
    with pytest.raises(ValueError, match="at most 50 parameters"):
        sql_params.typed([p(f"p{i}") for i in range(51)])


def test_a_value_of_4000_characters_is_taken_and_4001_refused():
    assert sql_params.typed([p(value="x" * 4000)]) == {"a": "x" * 4000}
    with pytest.raises(ValueError, match="at most 4000 characters"):
        sql_params.typed([p(value="x" * 4001)])


@pytest.mark.parametrize("params, match", [
    ("a", "must be a list"), ({"name": "a"}, "must be a list"),
    ([1], "Each parameter is"), (["a"], "Each parameter is"),
    ([p(name=5)], "isn't a parameter name"), ([p(name="1a")], "isn't a parameter name"),
    ([p(name="a b")], "isn't a parameter name"),
    ([p(value=5)], "must be text"), ([p(value=None)], "must be text"),
])
def test_a_malformed_parameter_list_is_refused_by_name(params, match):
    with pytest.raises(ValueError, match=match):
        sql_params.typed(params)


def test_names_and_colons_read_spark_by_default_and_positional_reads_tsql():
    text = "SELECT [x :b], :c FROM t"  # [...] is a quoted name only in T-SQL
    assert [n for _a, _b, n in sql_params.find(text)] == ["b", "c"]
    assert sql_params.names(text) == ["b", "c"]
    assert sql_params.as_colons("SELECT [x {{b}}]") == "SELECT [x :b]"
    assert sql_params.as_colons("SELECT [x {{b}}]", tsql=True) == "SELECT [x {{b}}]"
    assert sql_params.as_positional("SELECT [x :b]", {}) == ("SELECT [x :b]", [])


@pytest.mark.parametrize("text, expected", [
    ("':x', :y", ["y"]),            # a string at the very start
    ("/*/ :x", []),                 # "/*/" opens a comment, it doesn't close one
    ("/**/ :x", ["x"]),             # the shortest comment
    ("/* :a*/", []),                # a parameter just before the comment ends
    ("/* */:b", ["b"]),             # a parameter just after it ends
    ("SELECT ':x", []),             # a string that never ends runs to the end
    ("'a :x'", []),
    ("'a :x', :y", ["y"]),
    ("'it''s :x', :y", ["y"]),      # '' inside a string is one quote
    (":y 'a'", ["y"]),              # a string closing on the last character
    ("'a''' :y", ["y"]),            # a doubled quote right before the closing one
    ("'a\\' :x', :y", ["y"]),       # \' inside a Spark or DuckDB string
    ("'\\'' :y", ["y"]),
    ("'a':x", ["x"]),               # a parameter right after a string ends
])
def test_strings_and_comments_end_exactly_where_they_end(text, expected):
    assert sql_params.names(text) == expected


def test_a_backslash_is_an_ordinary_character_in_a_tsql_string():
    assert sql_params.names("'a\\' :x", tsql=True) == ["x"]
