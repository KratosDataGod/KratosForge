"""Mutation batch 150: what a warehouse user sees from local T-SQL's CTE checks (tsql_ctes.py).

Each test runs T-SQL through the local engine and checks the rows it gives, or the exact words it stops with.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Ctes")["id"])
    e.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, s)")
    yield e
    data.close_all()


def table(engine):
    return engine.execute("SELECT id, s FROM t ORDER BY id")["rows"]


def test_update_through_an_aliased_table_beside_a_cte_changes_the_table(engine):
    # x is an alias of the table t, not of the CTE c, so this is an UPDATE of t and runs
    engine.execute("WITH c AS (SELECT 2 AS id) UPDATE x SET s = 'y' FROM t AS x JOIN c ON x.id = c.id",
                   dialect="tsql")
    assert table(engine) == [[1, "a"], [2, "y"], [3, "c"]]
