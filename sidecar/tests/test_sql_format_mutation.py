"""Mutation testing batch 9 (docs/quality/mutation-testing.md): Format SQL's checks.

Each test pins a behavior a surviving mutant changed without any other test noticing: finding the
``--`` comments outside strings, quoted names and block comments (doubled quotes, a string or block
comment that is never closed, a comment at the very end), the row-limit check that stops LIMIT
turning into TOP, and how a lost comment is named in the warning."""

from __future__ import annotations

import pytest

from fabric_desktop import sql_format


@pytest.mark.parametrize("query, found", [
    ("SELECT 1 -- tail", {"tail"}),                     # no newline after it
    ("SELECT 1 --x\n", {"x"}),                          # no space after the dashes
    ("SELECT '-- not' AS a -- real\n", {"real"}),       # a string holding dashes
    ("SELECT 'a' -- yes", {"yes"}),                     # right after a string
    ("SELECT 'it''s -- no' -- yes", {"yes"}),           # a doubled quote inside the string
    ("SELECT 'a''' -- yes", {"yes"}),                   # a doubled quote just before the end
    ('SELECT "a--b" -- yes', {"yes"}),                  # a quoted name holding dashes
    ("SELECT [a--b] -- yes", {"yes"}),                  # a bracketed name holding dashes
    ("SELECT a[b[1]] -- yes", {"yes"}),                 # nested indexes: "]]" isn't a doubled bracket
    ("SELECT 1 /* a */-- b", {"b"}),                    # right after a block comment
    ("SELECT 1 /**/ -- b", {"b"}),                      # an empty block comment
    ("SELECT 1 /*/ -- inside */ -- out", {"out"}),      # dashes inside a block comment
    ("SELECT 1 /* it' */ -- b", {"b"}),                 # a quote inside a block comment
    ("SELECT 1 /* open -- not", set()),                 # a block comment never closed
    ("SELECT 'open -- not", set()),                     # a string never closed
])
def test_line_comments_are_found_only_outside_strings_names_and_block_comments(query, found):
    assert sql_format._line_comments(query) == found


def test_a_line_comment_after_a_string_with_dashes_is_kept_as_a_line_comment():
    got = sql_format.format_sql("select 'a -- b' as x from t -- the note\n", "duckdb")
    assert got["warning"] == ""
    assert "-- the note" in got["sql"] and "'a -- b'" in got["sql"]


def test_no_difference_is_the_empty_text():
    assert sql_format._difference("SELECT 1", "select 1", "duckdb") == ""


def test_a_lost_comment_is_named_by_its_first_40_characters():
    got = sql_format._difference("SELECT 1 /* " + "x" * 60 + " */", "SELECT 1", "duckdb")
    assert got == f"remove a comment ({'x' * 40!r})"


def test_formatting_that_turns_limit_into_top_leaves_the_query_alone(monkeypatch):
    # The LIMIT-keeping layout is what stops this; without it the row-limit check must.
    monkeypatch.setattr(sql_format, "_layout_dialect", lambda q, name: sql_format._TSQLLayout if name == "tsql" else name)
    got = sql_format.format_sql("select a from t limit 5", "warehouse")
    assert got == {"sql": "select a from t limit 5", "changed": False,
                   "warning": "Format SQL left the query as it was, because formatting would remove the row limit 'LIMIT'."}


def test_t_sql_limit_is_kept_as_limit():
    assert sql_format.format_sql("select a from t limit 5", "warehouse") == {
        "sql": "SELECT\n  a\nFROM t\nLIMIT 5", "changed": True, "warning": ""}
