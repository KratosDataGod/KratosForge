"""Mutation testing, batch 97: local T-SQL's CTE checks (localdata/tsql_ctes.py).

An UPDATE whose target is a table's alias in FROM, with a CTE joined beside it, changes the table and runs.
Only an alias of a CTE itself makes the target a change through a CTE.
"""

from __future__ import annotations

import pytest
import sqlglot

from fabric_desktop.localdata import LocalData, tsql_ctes


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    data = LocalData(tmp_path / "data")
    e = data.engine(data.create("CtesMutation")["id"])
    e.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, s)")
    try:
        yield e
    finally:
        data.close_all()


_TABLE_ALIAS_TARGET = ("WITH c AS (SELECT 2 AS id, 'two' AS s) "
                       "UPDATE x SET s = c.s FROM t AS x JOIN c ON x.id = c.id")


def test_a_tables_alias_beside_a_cte_is_not_a_change_through_the_cte():
    tree = sqlglot.parse_one(_TABLE_ALIAS_TARGET, read="tsql")
    assert tsql_ctes.check(tree) == []


def test_an_update_through_a_tables_alias_beside_a_cte_runs(engine):
    engine.execute(_TABLE_ALIAS_TARGET, dialect="tsql")
    got = engine.execute("SELECT id, s FROM t ORDER BY id", dialect="tsql")["rows"]
    assert [list(r) for r in got] == [[1, "a"], [2, "two"], [3, "c"]]


def test_a_ctes_alias_as_the_target_is_still_refused():
    tree = sqlglot.parse_one("WITH c AS (SELECT * FROM t) UPDATE x SET s = 'q' FROM c AS x", read="tsql")
    with pytest.raises(ValueError, match="UPDATE through the CTE 'x'"):
        tsql_ctes.check(tree)
