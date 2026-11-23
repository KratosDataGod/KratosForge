"""A value whose type can't be told, in NULLIF, a simple CASE, COUNT(DISTINCT ...), a window or ORDER BY, is noted (WR-255).

T-SQL ignores trailing spaces when it compares text in these (WR-246), and the local engine does too when it knows a
value is text. When it can't tell the type (CHOOSE isn't typed, nor is a table it doesn't know), the value compares
as DuckDB does, with trailing spaces, and the translation's notes now say so instead of differing silently.
"""
from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import _COLLATED_NOTE, translate

COLUMNS = {"dbo": {"t": {"s": "VARCHAR", "n": "INTEGER"}}}


def notes(sql: str, columns=COLUMNS) -> list[str]:
    return translate(sql, columns)[1]


@pytest.mark.parametrize("sql", [
    "SELECT NULLIF(CHOOSE(1, s), 'a') FROM t",
    "SELECT NULLIF(s, CHOOSE(1, s)) FROM t",
    "SELECT CASE CHOOSE(1, s) WHEN 'a' THEN 1 END FROM t",
    "SELECT CASE s WHEN CHOOSE(1, s) THEN 1 END FROM t",
    "SELECT COUNT(DISTINCT CHOOSE(1, s)) FROM t",
    "SELECT RANK() OVER (ORDER BY CHOOSE(1, s)) FROM t",
    "SELECT ROW_NUMBER() OVER (PARTITION BY CHOOSE(1, s) ORDER BY n) FROM t",
    "SELECT n FROM t ORDER BY CHOOSE(1, s), n",
    "SELECT CHOOSE(1, s) AS k, n FROM t ORDER BY k, n",
    "SELECT n FROM nowhere ORDER BY a, b",
])
def test_an_unknown_type_is_noted(sql):
    assert _COLLATED_NOTE in notes(sql)


@pytest.mark.parametrize("sql", [
    "SELECT NULLIF(s, 'a') FROM t",
    "SELECT NULLIF(CAST(CHOOSE(1, s) AS VARCHAR(5)), 'a') FROM t",
    "SELECT NULLIF(s, NULL) FROM t",
    "SELECT NULLIF(n, 1) FROM t",
    "SELECT CASE s WHEN 'a' THEN 1 END FROM t",
    "SELECT CASE n WHEN 1 THEN 1 END FROM t",
    "SELECT CASE WHEN s = 'a' THEN 1 END FROM t",
    "SELECT COUNT(DISTINCT s), COUNT(DISTINCT n), COUNT(*) FROM t",
    "SELECT RANK() OVER (PARTITION BY s ORDER BY n) FROM t",
    "SELECT n FROM t ORDER BY CAST(CHOOSE(1, s) AS VARCHAR), n",
    "SELECT n FROM t ORDER BY n, CHOOSE(1, s)",  # the last key: its ties are in any order anyway
    "SELECT n FROM t ORDER BY CHOOSE(1, s)",
])
def test_a_known_type_or_no_comparison_has_no_note(sql):
    assert _COLLATED_NOTE not in notes(sql)


def test_the_note_is_given_once():
    sql = "SELECT NULLIF(CHOOSE(1, s), 'a'), COUNT(DISTINCT CHOOSE(1, s)) FROM t GROUP BY s ORDER BY CHOOSE(1, s), 1"
    assert notes(sql).count(_COLLATED_NOTE) == 1


def test_the_note_names_the_way_round_it_in_plain_words():
    assert "CAST" in _COLLATED_NOTE and "VARCHAR" in _COLLATED_NOTE
    assert ":" not in _COLLATED_NOTE


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("padded") / "app" / "local-data")
    e = local.engine(local.create("Padded")["id"])
    e.execute("CREATE TABLE t (s VARCHAR(20), n INT)", dialect="tsql")
    e.execute("INSERT INTO t VALUES ('a', 1), ('a ', 2), ('b', 3)", dialect="tsql")
    return e


def test_the_engine_passes_the_note_on_and_the_cast_gives_tsqls_answer(engine):
    r = engine.execute("SELECT COUNT(DISTINCT CHOOSE(1, s)) FROM t", dialect="tsql")
    assert _COLLATED_NOTE in r["translation"]["notes"]
    assert r["rows"] == [[3]]  # 'a' and 'a ' apart, as DuckDB compares them
    r = engine.execute("SELECT COUNT(DISTINCT CAST(CHOOSE(1, s) AS VARCHAR(5))) FROM t", dialect="tsql")
    assert _COLLATED_NOTE not in (r["translation"].get("notes") or [])
    assert r["rows"] == [[2]]  # 'a' and 'a ' are one value in T-SQL
