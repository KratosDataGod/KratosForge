"""WR-230: local T-SQL's common table expressions as a Fabric Warehouse has them, or refused by name.

Microsoft's T-SQL surface area page for Warehouse in Microsoft Fabric lists recursive queries as unsupported and
nested CTEs as a preview. A recursive CTE must stop by name before it runs (never DuckDB's binder error, never a
query that runs until memory runs out), and the forms a warehouse runs must give its rows.
"""

from __future__ import annotations

import time

import pytest

from fabric_desktop.localdata import LocalData, tsql, tsql_ctes


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Ctes")["id"])
    e.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, s)")
    yield e
    data.close_all()


def rows(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"]


def refused(engine, sql, says):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    text = str(caught.value)
    assert says in text and "Binder Error" not in text and "Parser Error" not in text, text
    return text


# ---- recursion: refused by name before anything runs

@pytest.mark.parametrize("sql", [
    "WITH n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n WHERE i < 5) SELECT i FROM n",
    "WITH n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n WHERE i < 200) SELECT COUNT(*) FROM n",
    "WITH N AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM [n] WHERE i < 5) SELECT i FROM N",  # names ignore case
    ("WITH a AS (SELECT 1 AS x), n AS (SELECT x AS i FROM a UNION ALL SELECT i + 1 FROM n WHERE i < 3) "
     "SELECT i FROM n"),  # the second CTE of two
    "WITH n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n WHERE i < 5) INSERT INTO t (id) SELECT i FROM n",
    "WITH n AS (SELECT 1 AS i UNION ALL SELECT n.i + 1 FROM t JOIN n ON t.id = n.i) SELECT i FROM n",
    "WITH n AS (SELECT 1 AS i UNION ALL SELECT i FROM (SELECT i + 1 AS i FROM n) q WHERE i < 5) SELECT i FROM n",
])
def test_a_recursive_cte_is_refused_by_name_as_a_warehouse_refuses_it(engine, sql):
    text = refused(engine, sql, "names itself, which makes it recursive")
    assert "a Fabric Warehouse doesn't run recursive queries" in text and "Unroll" in text


def test_a_recursive_cte_with_no_end_stops_at_once_and_changes_nothing(engine):
    started = time.monotonic()
    refused(engine, "WITH n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n) INSERT INTO t (id) SELECT i FROM n",
            "makes it recursive")
    assert time.monotonic() - started < 10
    assert rows(engine, "SELECT COUNT(*) FROM t") == [[3]]


@pytest.mark.parametrize("sql", [
    "WITH n AS (SELECT 1 AS i) SELECT i FROM n OPTION (MAXRECURSION 10)",
    "WITH n AS (SELECT 1 AS i) SELECT i FROM n OPTION (MAXRECURSION 0)",
])
def test_maxrecursion_is_refused_by_name(engine, sql):
    refused(engine, sql, "OPTION (MAXRECURSION ...) isn't carried yet")


def test_with_recursive_is_refused_as_not_t_sql():
    with pytest.raises(ValueError, match="WITH RECURSIVE isn't T-SQL"):
        tsql_ctes.check(tsql.sqlglot.parse_one("WITH RECURSIVE n AS (SELECT 1 AS i) SELECT i FROM n",
                                               read="duckdb"))


@pytest.mark.parametrize("sql", [
    "WITH t AS (SELECT id FROM dbo.t) SELECT COUNT(*) FROM t",  # a schema-qualified t is the table
    "WITH a AS (WITH a AS (SELECT 1 AS id) SELECT id FROM a) SELECT id FROM a",  # the inner a is its own CTE
])
def test_a_cte_that_reads_a_table_or_inner_cte_of_its_name_isnt_recursive(engine, sql):
    assert rows(engine, sql) in ([[3]], [[1]])


# ---- the forms a warehouse runs

def test_several_ctes_each_see_the_ones_before(engine):
    sql = ("WITH a AS (SELECT id FROM t WHERE id > 1), b AS (SELECT id * 10 AS v FROM a), "
           "c AS (SELECT MAX(v) + MIN(v) AS total FROM b) SELECT total FROM c")
    assert rows(engine, sql) == [[50]]


def test_a_cte_column_list_names_the_columns(engine):
    assert rows(engine, "WITH a (p, q) AS (SELECT id, s FROM t WHERE id = 2) SELECT q, p FROM a") == [["b", 2]]


def test_a_cte_read_twice_gives_the_same_rows(engine):
    sql = "WITH a AS (SELECT id FROM t) SELECT COUNT(*) FROM a x CROSS JOIN a y"
    assert rows(engine, sql) == [[9]]


def test_a_cte_before_insert_adds_its_rows(engine):
    engine.execute("WITH a AS (SELECT id + 10 AS id, s FROM t WHERE id < 3) INSERT INTO t SELECT id, s FROM a",
                   dialect="tsql")
    assert rows(engine, "SELECT id FROM t ORDER BY id") == [[1], [2], [3], [11], [12]]


def test_a_cte_before_update_from_changes_the_joined_rows(engine):
    engine.execute("WITH a AS (SELECT 2 AS id, 'two' AS s) UPDATE t SET s = a.s FROM t JOIN a ON t.id = a.id",
                   dialect="tsql")
    assert rows(engine, "SELECT s FROM t ORDER BY id") == [["a"], ["two"], ["c"]]


def test_a_cte_before_delete_removes_the_rows_it_names(engine):
    engine.execute("WITH a AS (SELECT id FROM t WHERE s <> 'b') DELETE FROM t WHERE id IN (SELECT id FROM a)",
                   dialect="tsql")
    assert rows(engine, "SELECT id FROM t") == [[2]]


def test_a_nested_cte_runs_and_says_its_a_preview_in_a_warehouse(engine):
    result = engine.execute("WITH a AS (WITH b AS (SELECT id FROM t) SELECT MAX(id) AS m FROM b) SELECT m FROM a",
                            dialect="tsql")
    assert result["rows"] == [[3]]
    assert tsql_ctes.NESTED_NOTE in tsql.translate(
        "WITH a AS (WITH b AS (SELECT 1 AS id) SELECT id FROM b) SELECT id FROM a")[1]


def test_a_plain_cte_has_no_note():
    assert tsql.translate("WITH a AS (SELECT 1 AS id) SELECT id FROM a")[1] == []


# ---- refused by name

@pytest.mark.parametrize("sql, verb", [
    ("WITH a AS (SELECT * FROM t WHERE id = 3) UPDATE a SET s = 'x'", "UPDATE"),
    ("WITH a AS (SELECT * FROM t WHERE id = 3) DELETE FROM a", "DELETE"),
    ("WITH a AS (SELECT * FROM t WHERE id = 3) DELETE a WHERE id = 3", "DELETE"),
    ("WITH c AS (SELECT * FROM t WHERE id = 3) UPDATE a SET s = 'x' FROM c AS a", "UPDATE"),
    ("WITH c AS (SELECT * FROM t WHERE id = 3) DELETE a FROM c a", "DELETE"),
])
def test_a_change_through_a_cte_is_refused_by_name_and_changes_nothing(engine, sql, verb):
    refused(engine, sql, f"{verb} through the CTE '")
    assert rows(engine, "SELECT id, s FROM t ORDER BY id") == [[1, "a"], [2, "b"], [3, "c"]]


def test_two_ctes_of_one_name_stop_with_sql_servers_words(engine):
    refused(engine, "WITH a AS (SELECT 1 AS id), A AS (SELECT 2 AS id) SELECT id FROM a",
            "Duplicate common table expression name 'A' was specified.")
