"""A DuckDB query whose first value is a :name parameter is read, and runs with the value bound.

sqlglot's DuckDB parser takes ``word :`` as DuckDB's prefix alias (``SELECT v: 1``) and tried it on the
statement's first token, so ``SELECT :n + 1 AS v`` stopped with "Required keyword: 'this' missing" while
``SELECT 1 + :n AS v`` was read."""
from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql


@pytest.mark.parametrize(("query", "want"), [
    ("SELECT :n + 1 AS v", 3),
    ("SELECT :n AS v", 2),
    ("SELECT 1 + :n AS v", 3),
    ("WITH x AS (SELECT :n AS a) SELECT a * 10 AS v FROM x", 20),
    ("SELECT :n + 1 AS v UNION ALL SELECT 0 ORDER BY v DESC LIMIT 1", 3),
])
def test_a_parameter_anywhere_in_the_select_list_is_read_and_bound(query: str, want: int) -> None:
    [statement] = sql.parse(query, "duckdb")
    assert duckdb.connect().execute(sql.duckdb_sql(statement), {"n": 2}).fetchall() == [(want,)]


@pytest.mark.parametrize(("query", "want"), [
    ("SELECT v: 1", "SELECT 1 AS v"),
    ("SELECT v: :n + 1", "SELECT $n + 1 AS v"),
    ("SELECT * FROM t1: range(3)", "SELECT * FROM RANGE(0, 3) AS t1"),
])
def test_duckdbs_prefix_aliases_are_still_read(query: str, want: str) -> None:
    [statement] = sql.parse(query, "duckdb")
    assert sql.duckdb_sql(statement) == want
