"""N'...' text in local T-SQL is text, as in a warehouse: it compares with ANSI padding, LIKE reads its
[a-c] ranges, and + joins it. sqlglot reads it as exp.National, which those passes once missed, so
WHERE col = N'abc' found no row whose col was 'abc  ' (found by tests/test_tsql_rules_differential.py)."""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("national") / "app" / "local-data")
    e = local.engine(local.create("National")["id"])
    e.execute("CREATE TABLE dbo.t (name VARCHAR(10))", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES ('abc  '), ('Abc'), ('b')", dialect="tsql")
    return e


def rows(engine, sql: str) -> list:
    return engine.execute(sql, dialect="tsql")["rows"]


def test_a_filter_on_n_text_ignores_trailing_spaces(engine) -> None:
    assert rows(engine, "SELECT name FROM dbo.t WHERE name = N'abc'") == [["abc  "]]
    assert rows(engine, "SELECT name FROM dbo.t WHERE name <> N'abc' ORDER BY name") == [["Abc"], ["b"]]


@pytest.mark.parametrize(("expression", "want"), [
    ("CASE WHEN N'a' = N'a  ' THEN 1 ELSE 0 END", 1),
    ("CASE WHEN N'a' < N'a ' THEN 1 ELSE 0 END", 0),
    ("CASE WHEN N'a' = N'A' THEN 1 ELSE 0 END", 0),
    ("CASE WHEN N'b' LIKE N'[a-c]%' THEN 1 ELSE 0 END", 1),
    ("CASE WHEN N'b' LIKE N'[^b]%' THEN 1 ELSE 0 END", 0),
    ("CASE WHEN N'abc  ' LIKE N'%c' THEN 1 ELSE 0 END", 1),
    ("N'a' + N'b'", "ab"),
    ("N'a' + NULL", None),
    ("N'it''s'", "it's"),
    ("DATALENGTH(N'ab')", 4),
    ("DATALENGTH('ab')", 2),
    ("DATALENGTH(N'a' + N'b')", 2),  # T-SQL gives 4 (nvarchar); docs/sql-compatibility.md says so
    ("UNICODE(N'é')", 233),
])
def test_n_text_answers_as_plain_text(engine, expression: str, want) -> None:
    assert rows(engine, f"SELECT {expression}")[0][0] == want


def test_like_n_pattern_on_a_column(engine) -> None:
    assert rows(engine, "SELECT name FROM dbo.t WHERE name LIKE N'[aA]%' ORDER BY name") == [["Abc"], ["abc  "]]
