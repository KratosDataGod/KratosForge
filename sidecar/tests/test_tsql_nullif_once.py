"""NULLIF on text reads its first value once (WR-254).

T-SQL's NULLIF(a, b) on text ignores trailing spaces, as = does (WR-246), and gives a back as it was. The rewrite
used to name a twice, once compared through RTRIM and once shown, so each level of nested NULLIFs doubled the SQL:
twelve levels came to about 7000 characters. Now a is bound once, in LIST_TRANSFORM([a], __kf_nullif -> ...)[1],
and the SQL grows by the same amount at each level. The answers are checked as values, so a run on the pinned
DuckDB and one on DuckDB 2 must agree on them.
"""
from __future__ import annotations

import re

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate

COLUMNS = {"dbo": {"t": {"s": "VARCHAR", "g": "VARCHAR", "n": "INTEGER"}}}


def nested(depth: int, inner: str = "s") -> str:
    sql = inner
    for i in range(depth):
        sql = f"NULLIF({sql}, 'x{i % 10}')"
    return sql


def size(depth: int) -> int:
    out, notes = translate(f"SELECT {nested(depth)} FROM t", COLUMNS)
    assert not notes, notes
    return len(out[0])


def test_the_value_is_named_once():
    out, _ = translate("SELECT NULLIF(s, 'a') FROM t", COLUMNS)
    assert "__kf_nullif" in out[0]
    assert len(re.findall(r"\bs\b", out[0])) == 1  # the column, in the bound list only


def test_nested_nullifs_grow_linearly():
    steps = [size(d + 1) - size(d) for d in range(1, 12)]
    assert len(set(steps)) == 1, steps  # the same each level, not doubling
    assert size(12) < 1600  # was about 7000


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("nullif") / "app" / "local-data")
    e = local.engine(local.create("Nullif")["id"])
    e.execute("CREATE TABLE t (s VARCHAR(20), g VARCHAR(20), n INT)", dialect="tsql")
    e.execute("INSERT INTO t VALUES ('a', 'a', 1), ('a  ', 'a', 2), ('a', 'a   ', 3), ('b ', 'a', 4), "
              "(NULL, 'a', 5), ('a', NULL, 6), ('', ' ', 7), (' a', 'a', 8)", dialect="tsql")
    return e


def run(engine, sql):
    r = engine.execute(sql, dialect="tsql")
    assert not (r.get("translation") or {}).get("notes"), r.get("translation")
    return [list(x) for x in r["rows"]]


def test_equal_without_trailing_spaces_is_null_and_otherwise_the_value_as_it_was(engine):
    assert run(engine, "SELECT n, NULLIF(s, g) FROM t ORDER BY n") == [
        [1, None], [2, None], [3, None], [4, "b "], [5, None], [6, "a"], [7, None], [8, " a"]]


def test_nested_nullifs_give_the_same_answers(engine):
    got = run(engine, f"SELECT n, {nested(12)} FROM t ORDER BY n")
    assert [r[1] for r in got] == ["a", "a  ", "a", "b ", None, "a", "", " a"]
    got = run(engine, "SELECT n, NULLIF(NULLIF(s, 'b'), g) FROM t ORDER BY n")
    assert [r[1] for r in got] == [None, None, None, None, None, "a", None, " a"]


def test_null_handling(engine):
    assert run(engine, "SELECT NULLIF(s, NULL) FROM t WHERE n = 2") == [["a  "]]
    assert run(engine, "SELECT NULLIF(CAST(NULL AS VARCHAR(5)), 'a')") == [[None]]


def test_a_subquery_as_the_second_value(engine):
    got = run(engine, "SELECT n, NULLIF(s, (SELECT MIN(g) FROM t)) FROM t ORDER BY n")
    assert [r[1] for r in got] == ["a", "a  ", "a", "b ", None, "a", None, " a"]  # MIN(g) is ' '


def test_an_aggregate_value(engine):
    assert run(engine, "SELECT NULLIF(MAX(s), 'b') FROM t") == [[None]]
    assert run(engine, "SELECT NULLIF(MIN(s), 'b') FROM t") == [[""]]


def test_plus_on_text_inside_still_joins(engine):
    assert run(engine, "SELECT NULLIF(s + 'z', 'az') FROM t WHERE n IN (1, 4) ORDER BY n") == [[None], ["b z"]]
