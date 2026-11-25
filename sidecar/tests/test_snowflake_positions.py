"""SFX-086 / SFN-085: $1, $2, ... read a column of the FROM clause by position (Snowflake's VALUES page), and
OBJECT_CONSTRUCT of a column whose name is the rewrite's own lambda variable."""
from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf


def _run(code: str) -> duckdb.DuckDBPyRelation:
    con = duckdb.connect()
    con.execute("CREATE TABLE t (x INT, y VARCHAR); INSERT INTO t VALUES (1, 'one'), (3, 'three')")
    translation = sf.translate(code)
    assert translation.runnable, translation.summary()
    for statement in translation.statements[:-1]:
        con.execute(statement.duckdb_sql)
    return con.sql(translation.statements[-1].duckdb_sql)


def test_the_values_pages_example_reads_each_column_by_position_and_names_it_so():
    result = _run("SELECT $1, $2 FROM VALUES (1, 'one'), (2, 'two'), (3, 'three')")
    assert result.columns == ["$1", "$2"]
    assert result.fetchall() == [(1, "one"), (2, "two"), (3, "three")]


@pytest.mark.parametrize("code,want", [
    ("SELECT $2 FROM t WHERE $1 = 3", [("three",)]),
    ("SELECT $1 + 1 AS n FROM t WHERE $1 = 1", [(2,)]),
    ("SELECT * FROM (SELECT $2 AS v FROM t) WHERE v = 'one'", [("one",)]),
    ("SELECT u.y FROM t JOIN t AS u ON $1 = u.x", [("one",), ("three",)]),  # $1 is t.x, the FROM's first
])
def test_a_position_in_the_select_list_where_and_on_reads_the_from_clause(code, want):
    assert sorted(_run(code).fetchall()) == want


@pytest.mark.parametrize("code", [
    "SELECT $2 FROM t ORDER BY $1",
    "SELECT $1, count(*) FROM t GROUP BY $1",
    "SELECT $1 FROM t GROUP BY x HAVING max($1) > 1",
    "SELECT $1 FROM t QUALIFY $1 > 1",
    "SELECT t.$1 FROM VALUES (1) t",
    "SELECT a.$2 FROM t AS a JOIN t AS b ON a.x = b.x WHERE b.$1 = 1",
])
def test_a_position_where_duckdb_reads_the_select_list_is_refused_by_name(code):
    translation = sf.translate(code)
    assert not translation.runnable and "SFN-085" in translation.summary(), translation.summary()


def test_a_session_variable_is_still_read_by_name():
    assert sf.translate("SELECT $v").duckdb_sql == "SELECT GETVARIABLE('V')"


@pytest.mark.parametrize("column", ["x", "kf", "v", "k"])
def test_object_construct_of_a_column_named_like_its_lambda_variable(column):
    con = duckdb.connect()
    con.execute(f'CREATE TABLE s ("{column}" INT); INSERT INTO s VALUES (1), (NULL)')
    out = con.sql(sf.translate(f"SELECT object_construct('a', {column}) FROM s").duckdb_sql).fetchall()
    assert [r[0] for r in out] == ['{"a":1}', "{}"]
