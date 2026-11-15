"""SQL best practices: the edges mutation testing (batch 23) found unchecked. TOP (n) PERCENT before
a star, UPDATE ... SET with and without FROM, LIKE inside IIF and inside grouping brackets, aliases in
correlated subqueries, function calls in FROM and in subqueries, CTE names, and statements that end
early (each must be read without an error). Reads text only: nothing runs and nothing is written."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.sqlcompat import practices as p


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def ids(query, **kw):
    return [f["rule_id"] for f in p.check(query, **kw)["findings"]]


def test_rules_are_frozen():
    rule = p.RULES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        rule.severity = "Error"


@pytest.mark.parametrize("query, expected", [
    # the star after TOP n, TOP (n) and TOP n PERCENT
    ("CREATE VIEW dbo.v AS SELECT TOP 10 * FROM dbo.t", ["SQ001", "SQ008"]),
    ("CREATE VIEW dbo.v AS SELECT TOP 10 PERCENT * FROM dbo.t", ["SQ001", "SQ008"]),
    # UPDATE t SET without FROM: t is a table; with FROM: an alias
    ("UPDATE orders SET a = 1", ["SQ005"]),
    ("UPDATE orders SET a = 1 WHERE b = 2", ["SQ005"]),
    ("UPDATE o SET a = 1 FROM dbo.orders AS o", []),
    # LIKE '%x' grouped in WHERE counts; inside IIF(...) it doesn't
    ("SELECT a FROM dbo.t WHERE (b = 1 OR c LIKE '%x')", ["SQ016"]),
    ("SELECT a FROM dbo.t WHERE IIF(c LIKE '%x', 1, 0) = 1", []),
    # NOT IN over an expression: can't tell that NULLs are dropped
    ("SELECT a FROM dbo.t WHERE a NOT IN (SELECT b + 1 FROM dbo.u)", ["SQ012"]),
    ("SELECT a FROM dbo.t WHERE a NOT IN (SELECT b FROM dbo.u WHERE b IS NOT NULL)", []),
    # a column called row_number isn't ROW_NUMBER()
    ("INSERT INTO dbo.t SELECT row_number AS id_key FROM dbo.u", []),
    ("INSERT INTO dbo.t SELECT ROW_NUMBER() OVER (ORDER BY a) AS id_key FROM dbo.u", ["DM002"]),
    # a star in a subquery doesn't feed an INSERT
    ("SELECT x FROM (SELECT * FROM dbo.t) AS s", []),
    # a JOIN without ON inside brackets
    ("SELECT a FROM (SELECT a FROM dbo.t JOIN dbo.u) AS s", ["SQ006"]),
    ("CROSS JOIN dbo.t", ["SQ006"]),
    # a table-valued function isn't a table without its schema
    ("SELECT a FROM my_fn(1) AS f", []),
    # CTE names aren't tables
    ("WITH x AS (SELECT a FROM dbo.t) SELECT a FROM x", []),
    # subqueries in the SELECT list: aliases with and without AS, schema-qualified tables, calls
    ("SELECT a, (SELECT MAX(x.v) FROM dbo.u AS x) AS m FROM dbo.t", []),
    ("SELECT a, (SELECT MAX(x.v) FROM dbo.u x) AS m FROM dbo.t", []),
    ("SELECT a, (SELECT MAX(u.v) FROM dbo.u) AS m FROM dbo.t", []),
    ("SELECT a, (SELECT MAX(v) FROM dbo.u) AS m FROM dbo.t", []),
    ("SELECT a, (SELECT dbo.fn(u.v) FROM dbo.u) AS m FROM dbo.t", []),
    ("SELECT a, (SELECT MAX(u.v) FROM dbo.u WHERE u.k = o.k) AS m FROM dbo.t AS o", ["SQ015"]),
    ("SELECT a, (SELECT MAX(u.v) FROM dbo.u WHERE o.k = u.k) AS m FROM dbo.t AS o", ["SQ015"]),
    # pasted fragments: a WHERE clause on its own, and the end of a CREATE VIEW ... AS (...)
    ("WHERE c LIKE '%x'", ["SQ016"]),
    ("AS (SELECT 1) SELECT a FROM sales", ["SQ005"]),
    # TRUNCATE then INSERT without INTO is still a reload
    ("TRUNCATE TABLE dbo.t; INSERT dbo.t SELECT a FROM dbo.u", ["WH001"]),
])
def test_edges(query, expected):
    assert sorted(ids(query)) == sorted(expected)


@pytest.mark.parametrize("query", [
    "TRUNCATE",
    "TRUNCATE TABLE dbo.t; INSERT",
    "SELECT a AS",
    "SELECT a FROM dbo.t WHERE a NOT IN (",
    "SELECT TOP 5 a FROM dbo.t ORDER",
    "SELECT a FROM dbo.t WHERE YEAR(d)",
    "CREATE TABLE dbo.t (a VARCHAR(MAX",
])
def test_statements_that_end_early_are_read_without_an_error(query):
    found = ids(query)
    assert "SQ002" not in found and "SQ003" not in found and "WH001" not in found
