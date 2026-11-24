"""DIV0, DIV0NULL, ZEROIFNULL, NULLIFZERO and LENGTH read their argument once, so nesting them grows the
DuckDB SQL by about the same amount at each level. sqlglot built the first four as an IF that repeats its
arguments, and DuckDB's writer spells LENGTH out as CASE TYPEOF(x) ... x ... x, so each level doubled (or
tripled) the SQL: twelve levels of ZEROIFNULL(DIV0(LENGTH(...), 2)) took minutes to translate."""
from __future__ import annotations

import time

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf


def _nested(template: str, depth: int) -> str:
    expr = "a"
    for _ in range(depth):
        expr = template.replace("x", expr)
    return f"SELECT {expr} AS r FROM t"


@pytest.mark.parametrize("template", ["LENGTH(x)", "LEN(x)", "DIV0(x, 2)", "DIV0NULL(x, 2)", "ZEROIFNULL(x)",
                                      "NULLIFZERO(x)", "ZEROIFNULL(DIV0(LENGTH(x), 2))", "DIV0(2, x + 1)"])
def test_each_level_adds_about_the_same(template: str) -> None:
    sizes = [len(sf.translate(_nested(template, d)).duckdb_sql) for d in range(2, 8)]
    steps = [b - a for a, b in zip(sizes, sizes[1:], strict=False)]
    assert max(steps) <= 1.2 * min(steps) + 20, sizes


def test_a_deep_nest_translates_quickly() -> None:
    started = time.monotonic()
    sql = sf.translate(_nested("ZEROIFNULL(DIV0(LENGTH(x), 2))", 12)).duckdb_sql
    assert time.monotonic() - started < 10 and len(sql) < 20_000


@pytest.mark.parametrize(("query", "want"), [
    ("SELECT DIV0(LENGTH(a), 2) AS r FROM t", [2.0, None, 0.0]),
    ("SELECT ZEROIFNULL(LENGTH(a)) AS r FROM t", [4, 0, 0]),
    ("SELECT NULLIFZERO(LENGTH(a)) AS r FROM t", [4, None, None]),
    ("SELECT DIV0NULL(LENGTH(a) + 1, LENGTH(a)) AS r FROM t", [1.25, 0.0, 0.0]),
    ("SELECT DIV0(DIV0(LENGTH(a), 2), 0) AS r FROM t", [0.0, None, 0.0]),
    ("SELECT LENGTH(LENGTH(a)) AS r FROM t", [1, None, 1]),
])
def test_the_answers_are_snowflakes(query: str, want: list[object]) -> None:
    # functions/div0: 0 when the divisor is 0 (NULL when the dividend is NULL); div0null: 0 when it is 0 or
    # NULL; zeroifnull, nullifzero; length counts characters
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'abcd'), (2, NULL), (3, '')) v(i, a)")
    rows = con.execute(f"SELECT r FROM ({sf.translate(query.replace('FROM t', ', i FROM t')).duckdb_sql}) ORDER BY i")
    assert [r[0] for r in rows.fetchall()] == want


@pytest.mark.parametrize(("query", "want"), [
    ("SELECT DIV0(a + 1, (SELECT MAX(b) FROM t)) AS r FROM t", [0.2, 0.3, 0.4]),
    ("SELECT DIV0(a + 1, (SELECT MAX(b) FROM t u WHERE u.a = t.a)) AS r FROM t", [0.2, 0.0, None]),
    ("SELECT DIV0NULL(CASE WHEN EXISTS (SELECT 1 FROM t u WHERE u.b = t.a) THEN 1 ELSE 2 END, a - 1) AS r FROM t",
     [0.0, 2.0, 1.0]),
    ("SELECT ZEROIFNULL((SELECT MAX(b) FROM t u WHERE u.a = t.a)) AS r FROM t", [10, 0, 0]),
    ("SELECT DIV0(DIV0(a + 1, b + 1), DIV0(b, a + 1)) AS r FROM t", [2 / 11 / 5, 0.0, None]),
])
def test_subqueries_in_any_argument_still_run(query: str, want: list[object]) -> None:
    """Every bound value is worked out outside the lambdas: DuckDB refuses a subquery inside one."""
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 10), (2, 0), (3, NULL)) v(a, b)")
    sql = sf.translate(query.replace(" AS r FROM t", " AS r, a AS i FROM t")).duckdb_sql
    assert [r[0] for r in con.execute(f"SELECT r FROM ({sql}) ORDER BY i").fetchall()] == pytest.approx(want)
