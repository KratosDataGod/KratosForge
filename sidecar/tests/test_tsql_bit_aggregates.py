"""WR-703: SUM, AVG, MIN and MAX of a bit stop as in T-SQL, and GROUPING and GROUPING_ID have T-SQL's types.

Microsoft Learn: SUM and AVG take a number "except for the bit data type", and MIN and MAX "not ... bit columns";
SQL Server stops with "Msg 8117, Level 16: Operand data type bit is invalid for sum operator." (avg, min, max).
COUNT of a bit works, and so does SUM(CAST(b AS INT)). GROUPING returns a tinyint and GROUPING_ID an int.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql_errors

PIVOT = "SELECT * FROM (SELECT g, h, f FROM t) s PIVOT ({agg} FOR h IN ([x], [y])) p"


def refused(operator: str) -> str:
    return f"Msg 8117, Level 16: Operand data type bit is invalid for {operator} operator."


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Bits")["id"])
    e.execute("CREATE TABLE t (g VARCHAR(5), h VARCHAR(5), f BIT, i INT)", dialect="tsql")
    e.execute("INSERT INTO t VALUES ('a', 'x', 1, 1), ('a', 'y', 1, 2), ('b', 'x', 0, 3), ('b', 'x', 1, 4)",
              dialect="tsql")
    yield e
    data.close_all()


def result(engine, sql):
    out = engine.execute(sql, dialect="tsql")
    return [c["type"] for c in out["columns"]], out["rows"]


@pytest.mark.parametrize("sql, operator", [
    ("SELECT SUM(f) FROM t", "sum"),
    ("SELECT AVG(f) FROM t", "avg"),
    ("SELECT MIN(f) FROM t", "min"),
    ("SELECT MAX(f) FROM t", "max"),
    ("SELECT g, SUM(f) s FROM t GROUP BY g", "sum"),
    ("SELECT SUM(DISTINCT f) FROM t", "sum"),
    ("SELECT g FROM t GROUP BY g ORDER BY SUM(f)", "sum"),
    ("SELECT COUNT(*) c, SUM(f) s FROM t", "sum"),
    # a CAST or CONVERT to BIT, and a CASE of bits
    ("SELECT SUM(CAST(i AS BIT)) FROM t", "sum"),
    ("SELECT MAX(CONVERT(BIT, i)) FROM t", "max"),
    ("SELECT SUM(CASE WHEN i > 1 THEN f ELSE CAST(0 AS BIT) END) FROM t", "sum"),
    # read again from a CTE, a derived table or a view
    ("WITH c AS (SELECT f FROM t) SELECT SUM(f) FROM c", "sum"),
    ("WITH c AS (SELECT CAST(i AS BIT) b FROM t) SELECT AVG(b) FROM c", "avg"),
    ("SELECT MIN(x.f) FROM (SELECT f FROM t) x", "min"),
    ("SELECT SUM(x.f) FROM (VALUES (CAST(1 AS BIT))) x(f)", "sum"),
    # OVER (...) and PIVOT
    ("SELECT SUM(f) OVER () FROM t", "sum"),
    ("SELECT AVG(f) OVER (PARTITION BY g) FROM t", "avg"),
    ("SELECT MAX(f) OVER (ORDER BY i) FROM t", "max"),
    (PIVOT.format(agg="SUM(f)"), "sum"),
    (PIVOT.format(agg="MAX(f)"), "max"),
    # inside a subquery, and in an UPDATE's SET
    ("SELECT g FROM t WHERE i > (SELECT SUM(f) FROM t)", "sum"),
    ("UPDATE t SET i = (SELECT SUM(f) FROM t)", "sum"),
])
def test_an_aggregate_of_a_bit_is_refused_with_8117(engine, sql, operator):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    assert str(caught.value) == refused(operator)


def test_a_refused_update_changes_nothing(engine):
    with pytest.raises(Exception, match="Msg 8117"):
        engine.execute("UPDATE t SET i = (SELECT SUM(f) FROM t)", dialect="tsql")
    assert result(engine, "SELECT SUM(i) FROM t") == (["int32"], [[10]])


def test_a_view_of_a_bit_is_refused_too(engine):
    engine.execute("CREATE VIEW dbo.v AS SELECT f FROM t", dialect="tsql")
    with pytest.raises(Exception) as caught:
        engine.execute("SELECT SUM(f) FROM dbo.v", dialect="tsql")
    assert str(caught.value) == refused("sum")


@pytest.mark.parametrize("sql, types, rows", [
    # COUNT of a bit works
    ("SELECT COUNT(f) c, COUNT(DISTINCT f) d, COUNT_BIG(f) cb FROM t", ["int32", "int32", "int64"], [[4, 2, 4]]),
    # casting the bit first is T-SQL's way to count the ones
    ("SELECT SUM(CAST(f AS INT)) s, AVG(CAST(f AS INT)) a FROM t", ["int32", "int32"], [[3, 0]]),
    ("SELECT SUM(CONVERT(TINYINT, f)) FROM t", ["int32"], [[3]]),
    ("SELECT SUM(CAST(f AS INT)) OVER () s FROM t ORDER BY i", ["int32"], [[3], [3], [3], [3]]),
    ("SELECT MAX(CAST(f AS INT)) m FROM t", ["int32"], [[1]]),
    # a bit compared, not aggregated
    ("SELECT COUNT(*) FROM t WHERE f = 1", ["int32"], [[3]]),
])
def test_what_t_sql_runs_with_a_bit_runs(engine, sql, types, rows):
    assert result(engine, sql) == (types, rows)


@pytest.mark.parametrize("sql, types, rows", [
    ("SELECT g, GROUPING(g) gg FROM t GROUP BY ROLLUP(g) ORDER BY g",
     ["string", "uint8"], [[None, 1], ["a", 0], ["b", 0]]),
    ("SELECT g, h, GROUPING_ID(g, h) gi FROM t GROUP BY ROLLUP(g, h) ORDER BY gi DESC, g, h",
     ["string", "string", "int32"],
     [[None, None, 3], ["a", None, 1], ["b", None, 1], ["a", "x", 0], ["a", "y", 0], ["b", "x", 0]]),
    (("SELECT GROUPING(g) gg, GROUPING(h) gh, GROUPING_ID(g, h) gi FROM t GROUP BY CUBE(g, h) "
      "HAVING GROUPING(g) = 1 AND GROUPING(h) = 1"),
     ["uint8", "uint8", "int32"], [[1, 1, 3]]),
    ("SELECT g, GROUPING(g) gg FROM t GROUP BY g WITH ROLLUP ORDER BY g",
     ["string", "uint8"], [[None, 1], ["a", 0], ["b", 0]]),
    ("SELECT g, GROUPING(g) gg FROM t GROUP BY GROUPING SETS ((g), ()) ORDER BY g",
     ["string", "uint8"], [[None, 1], ["a", 0], ["b", 0]]),
    # tinyint and an int literal give an int in T-SQL, so arithmetic doesn't stop at 0 or 255
    ("SELECT g, GROUPING(g) - 1 m, GROUPING(g) + 255 p, GROUPING_ID(g) * 2 t FROM t GROUP BY ROLLUP(g) ORDER BY g",
     ["string", "int32", "int32", "int32"], [[None, 0, 256, 2], ["a", -1, 255, 0], ["b", -1, 255, 0]]),
    # read again from a CTE: an int here, where T-SQL keeps a tinyint (the docs say so)
    ("WITH c AS (SELECT g, GROUPING(g) gg FROM t GROUP BY ROLLUP(g)) SELECT g, gg - 1 m FROM c ORDER BY g",
     ["string", "int32"], [[None, 0], ["a", -1], ["b", -1]]),
])
def test_grouping_is_a_tinyint_and_grouping_id_an_int(engine, sql, types, rows):
    assert result(engine, sql) == (types, rows)


def test_8117_is_numbered_for_its_own_words_only():
    for operator in ("sum", "avg", "min", "max"):
        words = f"Operand data type bit is invalid for {operator} operator."
        assert tsql_errors.numbered(words) == f"Msg 8117, Level 16: {words}"
    for other in ("Operand data type bit is invalid for sum operator.\n",
                  "Operand data type bit is invalid for sum operator. And more",
                  "Operand data type bit is invalid for count operator."):
        assert tsql_errors.numbered(other) == other
