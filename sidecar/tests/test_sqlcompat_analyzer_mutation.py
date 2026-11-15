"""Mutation-testing gaps in sqlcompat/analyzer.py (docs/quality/mutation-testing.md, batch 32).

Each test pins a behaviour that a surviving mutant changed while every existing test still passed.
The analyzer only reads SQL text: nothing here runs SQL or reaches Fabric.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fabric_desktop.sqlcompat import analyzer as A


# ---- size limits

def test_sql_over_the_limit_is_not_checked():
    report = A.analyze("SELECT 1" + " " * (A.MAX_QUERY - 7), "spark")
    assert report["outcome"]["status"] == "not_checked"
    assert "longer than 500,000 characters" in report["outcome"]["text"]
    assert report["truncated"] is False and report["statements"] == 0 and report["diagnostics"] == []


def test_sql_at_the_limit_is_checked():
    report = A.analyze("SELECT 1" + " " * (A.MAX_QUERY - 8), "spark")
    assert report["outcome"]["status"] != "not_checked" and report["statements"] == 1


def test_the_report_lists_the_first_thousand_findings_and_counts_them_all():
    report = A.analyze(";".join(["DELETE FROM t"] * 1001), "warehouse")
    assert len(report["diagnostics"]) == 1000 and report["truncated"] is True
    assert report["counts"]["warning"] == 1001
    report = A.analyze(";".join(["DELETE FROM t"] * 1000), "warehouse")
    assert len(report["diagnostics"]) == 1000 and report["truncated"] is False


# ---- SQL that can't be tokenized

def test_sql_that_cannot_be_read_says_why_at_its_start():
    stmts, problem = A.split("SELECT 'abc", "spark")
    assert stmts == [] and problem.startswith("The SQL couldn't be read: Error tokenizing")
    (d,) = A.analyze("SELECT 'abc", "spark")["diagnostics"]
    assert (d["rule"], d["from"], d["to"], d["statement"]) == ("SYN-007", 0, 1, 0)


# ---- splitting at T-SQL's GO

@pytest.mark.parametrize("query", ["SELECT 1\nGO\nSELECT 2", "SELECT 1\n  go  \nSELECT 2"])
def test_go_on_its_own_line_splits_t_sql(query):
    assert [s.text for s in A.split(query, "warehouse")[0]] == ["SELECT 1", "SELECT 2"]


def test_go_does_not_split_spark_sql():
    assert len(A.split("SELECT 1\nGO\nSELECT 2", "spark")[0]) == 1


def _tok(query, word):
    start = query.index(word)
    return SimpleNamespace(start=start, end=start + len(word) - 1)  # sqlglot's end is the last character


@pytest.mark.parametrize("query, alone", [("GO", True), ("a\nGO\nb", True), ("a\n  go \nb", True), ("a\nGO", True),
                                          ("a\nGO x\nb", False), ("a GO\nb", False), ("x\nGO GO", False)])
def test_go_alone_on_its_line(query, alone):
    assert A._alone_on_line(query, _tok(query, "GO" if "GO" in query else "go")) is alone


def test_a_column_alias_called_go_does_not_split_t_sql():
    assert len(A.split("SELECT x AS go FROM t", "warehouse")[0]) == 1
    assert len(A.split("SELECT go FROM t", "warehouse")[0]) == 1


def test_go_with_a_count_separates_batches_and_the_count_stays_with_it():
    statements, _ = A.split("SELECT 1\n  go 5  \nSELECT 2", "warehouse")
    assert [s.text for s in statements] == ["SELECT 1", "SELECT 2"]


@pytest.mark.parametrize("go", ["GO -- done", "GO /* a */ /* b */", "GO 5 -- again"])
def test_go_with_a_comment_on_its_line_still_separates_batches(go):
    statements, _ = A.split(f"SELECT 1\n{go}\nSELECT 2", "warehouse")
    assert [s.text for s in statements] == ["SELECT 1", "SELECT 2"]


def test_go_followed_by_a_semicolon_is_not_a_batch_separator():
    # sqlcmd and SSMS want GO alone on its line; GO; is left to the statement it ends
    statements, _ = A.split("SELECT 1\nGO;\nSELECT 2", "warehouse")
    assert [s.text for s in statements] == ["SELECT 1\nGO", "SELECT 2"]


# ---- tokens

def test_a_two_word_token_keeps_each_words_place():
    q = "SELECT a FROM t ORDER BY a"
    (stmt,) = A.split(q, "spark")[0]
    order, by = stmt.toks[-3], stmt.toks[-2]
    assert (order.raw, order.start, order.end) == ("ORDER", q.index("ORDER"), q.index("ORDER") + 5)
    assert (by.raw, by.start, by.end) == ("BY", q.index(" BY") + 1, q.index(" BY") + 3)


# ---- parse errors

def test_a_parse_error_keeps_sqlglots_description():
    stmt = A.split("SELECT 1;\nSELECT a b c d FROM", "warehouse")[0][1]
    A.parse(stmt, "tsql")
    assert stmt.error == "Invalid expression / Unexpected token"


def test_a_parse_error_drops_the_token_dump():
    stmt = A.split("SELECT FROM WHERE x", "spark")[0][0]
    A.parse(stmt, "spark")
    assert stmt.error == "Expected table name but got"


def test_a_syntax_finding_points_at_the_token_that_failed():
    q = "SELECT 1;\nSELECT FROM WHERE x"
    (d,) = A.analyze(q, "spark")["diagnostics"]
    assert d["rule"] == "SYN-007" and d["from"] == q.index("WHERE")


def test_a_syntax_finding_on_a_later_line_of_a_t_sql_batch_points_at_the_token():
    q = "SELECT 1\nGO\nSELECT a,\n  FROM t WHERE"
    (d,) = A.analyze(q, "warehouse")["diagnostics"]
    assert d["rule"] == "SYN-007" and d["from"] == q.rindex("WHERE") and d["line"] == 4


# ---- the exported report

def test_the_exported_report_is_indented_and_keeps_characters_as_written():
    query = "SELECT 'café'"
    text = A.report_json(A.analyze(query, "spark"), query)
    assert '\n  "schema": "sql-compat-report@1"' in text and "café" in text
    assert json.loads(text)["sql"] == query
