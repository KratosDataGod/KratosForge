"""localdata/tsql_merge: check() read directly on parsed MERGEs, for the sources whose columns aren't known
before the MERGE runs and the clauses check() leaves alone."""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from fabric_desktop.localdata import tsql_merge

COLUMNS = {"dbo": {"tgt": {"k": "INT", "v": "VARCHAR"}, "src": {"k": "INT", "w": "VARCHAR"}},
           "other": {"tgt": {"z": "INT"}}}


def merge(using: str, when: str = "WHEN MATCHED THEN UPDATE SET v = w") -> exp.Merge:
    tree = sqlglot.parse_one(f"MERGE INTO dbo.tgt AS t USING {using} ON t.k = s.k {when};", read="tsql")
    assert isinstance(tree, exp.Merge)
    return tree


def ambiguous(using: str) -> bool:
    """Whether check() placed the source's columns: k, which both have, is then ambiguous."""
    try:
        tsql_merge.check(merge(using, "WHEN MATCHED AND k > 1 THEN DELETE"), COLUMNS)
    except ValueError as exc:
        assert str(exc) == "Ambiguous column name 'k'."
        return True
    return False


def test_known_sources_are_placed():
    assert ambiguous("dbo.src AS s")
    assert ambiguous("(SELECT k, w FROM dbo.src) AS s")
    assert ambiguous("(SELECT * FROM dbo.src) AS s")
    assert ambiguous("(SELECT x.* FROM dbo.src AS x) AS s")


@pytest.mark.parametrize("using", [
    "(SELECT k + 1 FROM dbo.src) AS s",  # a projection with no name
    "(SELECT y.* FROM dbo.src AS x) AS s",  # y.* names no relation
    "(SELECT * FROM (SELECT * FROM dbo.nothere) AS q) AS s",  # an unknown table under the star
    "(SELECT * FROM OPENJSON(@j) AS q) AS s",  # a function: its columns come when it runs
    "#tmp AS s",  # a temporary table
    "dbo.unknown AS s",
])
def test_sources_whose_columns_are_not_known_are_left_to_the_engine(using):
    assert not ambiguous(using)


def test_a_nested_query_reads_through_to_its_select():
    assert tsql_merge._query(sqlglot.parse_one("((SELECT k FROM a))", read="tsql"), {}, None) == ["k"]
    assert tsql_merge._query(sqlglot.parse_one("SELECT k FROM a UNION SELECT j FROM b", read="tsql"), {}, None) == ["k"]
    assert tsql_merge._query(exp.Values(expressions=[]), {}, None) is None
    assert tsql_merge._relation(exp.Values(expressions=[]), {}, None) is None


def test_a_target_that_is_not_a_table_is_left_alone():
    tree = merge("dbo.src AS s")
    tree.set("this", sqlglot.parse_one("(SELECT 1)", read="tsql"))
    before = tree.sql(dialect="tsql")
    tsql_merge.check(tree, COLUMNS)
    assert tree.sql(dialect="tsql") == before
    tsql_merge.check(sqlglot.parse_one("SELECT 1", read="tsql"), COLUMNS)  # not a MERGE: nothing to do


def test_a_set_that_is_not_a_column_assignment_is_skipped():
    tree = merge("dbo.src AS s", "WHEN MATCHED THEN UPDATE SET t.v = s.w")
    update = tree.args["whens"].expressions[0].args["then"]
    update.append("expressions", exp.var("odd"))
    tsql_merge.check(tree, None)
    assert [a.sql() for a in update.expressions] == ["v = s.w", "odd"]  # t.v written as v; the rest left


def test_the_table_is_looked_up_in_its_own_schema():
    assert tsql_merge._columns_of(exp.to_table("other.tgt"), COLUMNS) == ["z"]
    assert tsql_merge._columns_of(exp.to_table("tgt"), COLUMNS) == ["k", "v"]  # dbo when no schema
    assert tsql_merge._columns_of(exp.to_table("nope.tgt"), COLUMNS) is None
    assert tsql_merge._columns_of(exp.to_table("dbo.none"), COLUMNS) is None
    assert tsql_merge._columns_of(exp.to_table("tgt"), None) is None
