"""WR-702: local T-SQL's aggregates give the value and the type T-SQL's SUM, AVG, COUNT, COUNT_BIG, MIN and MAX pages
give, in every shape: ROLLUP, CUBE, GROUPING SETS, PIVOT, a CTE or derived table read again, and OVER (...).

Microsoft Learn's return type tables: SUM and AVG of a tinyint, smallint or int are an int, and of a bigint a bigint;
AVG of integers is cut toward zero (AVG of 1 and 2 is 1), and a total past the type stops with error 8115. COUNT is an
int and COUNT_BIG a bigint. MIN and MAX keep their value's type. ``GROUP BY a WITH ROLLUP`` is ``ROLLUP(a)``.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData

INT_OVERFLOW = "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type int."
BIGINT_OVERFLOW = "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type bigint."
PIVOT = "SELECT * FROM (SELECT g, h, {v} FROM t) s PIVOT ({agg} FOR h IN ([x], [y])) p ORDER BY g"


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Aggregates")["id"])
    e.execute("CREATE TABLE t (g VARCHAR(5), h VARCHAR(5), i INT, b BIGINT, si SMALLINT, d DECIMAL(10, 2))",
              dialect="tsql")
    e.execute("INSERT INTO t VALUES ('a', 'x', 1, 10, 1, 1.50), ('a', 'y', 2, 20, 2, 2.25), "
              "('b', 'x', -3, 30, 4, 3.00), ('b', 'x', -4, 40, 4, 3.00), ('b', 'y', NULL, NULL, NULL, NULL)",
              dialect="tsql")
    yield e
    data.close_all()


def result(engine, sql):
    out = engine.execute(sql, dialect="tsql")
    return [c["type"] for c in out["columns"]], out["rows"]


def error(engine, sql):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    return str(caught.value)


@pytest.mark.parametrize("sql, types, rows", [
    # ROLLUP, CUBE and GROUPING SETS: the subtotal rows are ints too, not decimals
    ("SELECT g, SUM(i) s FROM t GROUP BY ROLLUP(g) ORDER BY g",
     ["string", "int32"], [[None, -4], ["a", 3], ["b", -7]]),
    ("SELECT g, SUM(i) s FROM t GROUP BY g WITH ROLLUP ORDER BY g",
     ["string", "int32"], [[None, -4], ["a", 3], ["b", -7]]),
    ("SELECT g, SUM(b) s FROM t GROUP BY ROLLUP(g) ORDER BY g", ["string", "int64"], [[None, 100], ["a", 30], ["b", 70]]),
    ("SELECT g, h, SUM(i) s FROM t GROUP BY CUBE(g, h) ORDER BY g, h",
     ["string", "string", "int32"],
     [[None, None, -4], [None, "x", -6], [None, "y", 2], ["a", None, 3], ["a", "x", 1], ["a", "y", 2],
      ["b", None, -7], ["b", "x", -7], ["b", "y", None]]),
    ("SELECT g, h, SUM(i) s FROM t GROUP BY g, h WITH CUBE HAVING g IS NULL AND h IS NULL",
     ["string", "string", "int32"], [[None, None, -4]]),
    ("SELECT g, SUM(i) s FROM t GROUP BY GROUPING SETS ((g), ()) ORDER BY g",
     ["string", "int32"], [[None, -4], ["a", 3], ["b", -7]]),
    ("SELECT g, AVG(i) a, COUNT(*) c, COUNT_BIG(*) cb, MAX(si) m FROM t GROUP BY ROLLUP(g) ORDER BY g",
     ["string", "int32", "int32", "int64", "int16"], [[None, -1, 5, 5, 4], ["a", 1, 2, 2, 2], ["b", -3, 3, 3, 4]]),
    # PIVOT: the aggregate's T-SQL type
    (PIVOT.format(v="i", agg="SUM(i)"), ["string", "int32", "int32"], [["a", 1, 2], ["b", -7, None]]),
    (PIVOT.format(v="b", agg="SUM(b)"), ["string", "int64", "int64"], [["a", 10, 20], ["b", 70, None]]),
    (PIVOT.format(v="i", agg="AVG(i)"), ["string", "int32", "int32"], [["a", 1, 2], ["b", -3, None]]),
    (PIVOT.format(v="i", agg="COUNT(i)"), ["string", "int32", "int32"], [["a", 1, 1], ["b", 2, 0]]),
    (PIVOT.format(v="i", agg="COUNT_BIG(i)"), ["string", "int64", "int64"], [["a", 1, 1], ["b", 2, 0]]),
    (PIVOT.format(v="i", agg="MAX(i)"), ["string", "int32", "int32"], [["a", 1, 2], ["b", -3, None]]),
    (PIVOT.format(v="d", agg="SUM(d)"), ["string", "decimal128(38, 2)", "decimal128(38, 2)"],
     [["a", "1.50", "2.25"], ["b", "6.00", None]]),
    ("SELECT g, x + y s FROM (SELECT g, h, i FROM t) s PIVOT (SUM(i) FOR h IN ([x], [y])) p ORDER BY g",
     ["string", "int32"], [["a", 3], ["b", None]]),
    # a CTE or derived table read again: its SUM is an int, so a SUM of it is an int too
    ("WITH c AS (SELECT g, SUM(i) s FROM t GROUP BY g) SELECT SUM(s) s FROM c", ["int32"], [[-4]]),
    ("SELECT SUM(s) s FROM (SELECT g, SUM(i) s FROM t GROUP BY g) x", ["int32"], [[-4]]),
    ("WITH c AS (SELECT g, SUM(i) s FROM t GROUP BY ROLLUP(g)) SELECT s FROM c WHERE g IS NULL", ["int32"], [[-4]]),
    ("WITH c AS (SELECT g, AVG(i) a FROM t GROUP BY g) SELECT SUM(a) s FROM c", ["int32"], [[-2]]),
    # OVER (...)
    ("SELECT g, SUM(i) OVER (PARTITION BY g) s FROM t WHERE i IS NOT NULL ORDER BY i",
     ["string", "int32"], [["b", -7], ["b", -7], ["a", 3], ["a", 3]]),
    ("SELECT AVG(i) OVER (PARTITION BY g) a FROM t WHERE g = 'b' AND i IS NOT NULL", ["int32"], [[-3], [-3]]),
    ("SELECT COUNT(*) OVER () c, COUNT_BIG(*) OVER () cb FROM t WHERE g = 'a'",
     ["int32", "int64"], [[2, 2], [2, 2]]),
    # CASE, and int + int
    ("SELECT SUM(CASE WHEN i > 0 THEN 1 ELSE 0 END) s FROM t", ["int32"], [[2]]),
    ("SELECT SUM(CASE WHEN i > 0 THEN i ELSE 0 END) s FROM t", ["int32"], [[3]]),
    ("SELECT SUM(i + i) s, SUM(i + si) t FROM t", ["int32", "int32"], [[-8, 7]]),
    ("SELECT SUM(si) s FROM t", ["int32"], [[11]]),
    # AVG of integers: an int, cut toward zero
    ("SELECT AVG(x) a FROM (VALUES (1), (2)) v(x)", ["int32"], [[1]]),
    ("SELECT AVG(x) a FROM (VALUES (-1), (-2)) v(x)", ["int32"], [[-1]]),
    ("SELECT AVG(i) a FROM t", ["int32"], [[-1]]),
    ("SELECT AVG(si) a FROM t", ["int32"], [[2]]),
    ("SELECT AVG(DISTINCT si) a FROM t", ["int32"], [[2]]),
    ("SELECT AVG(b) a FROM t", ["int64"], [[25]]),
    ("SELECT AVG(i) / 2 a FROM t WHERE g = 'b'", ["int32"], [[-1]]),  # an int divided as an int: -3 / 2
    ("SELECT AVG(i / 2) a FROM t WHERE i > 0", ["int32"], [[0]]),  # 1 / 2 and 2 / 2 are 0 and 1
    ("SELECT AVG(CAST(x AS BIGINT)) a FROM (VALUES (9007199254740993), (9007199254740993)) v(x)",
     ["int64"], [[9007199254740993]]),  # a float lost the last digit
    ("SELECT AVG(CASE WHEN i > 0 THEN 1 ELSE 0 END) a FROM t", ["int32"], [[0]]),
    ("SELECT x, AVG(x) OVER (ORDER BY x ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) a FROM (VALUES (1), (2), (4)) v(x)",
     ["int32", "int32"], [[1, 1], [2, 1], [4, 3]]),
    ("SELECT AVG(d) a FROM t", ["decimal128(38, 6)"], [["2.437500"]]),  # a decimal keeps its rule
    # COUNT is an int, COUNT_BIG a bigint
    ("SELECT COUNT(*) c, COUNT(i) ci, COUNT(DISTINCT g) cd, COUNT_BIG(*) cb, COUNT_BIG(DISTINCT g) cbd FROM t",
     ["int32", "int32", "int32", "int64", "int64"], [[5, 4, 2, 5, 2]]),
    ("SELECT COUNT(*) / 2 c FROM t", ["int32"], [[2]]),
    ("SELECT TOP 1 g, COUNT(*) c FROM t GROUP BY g ORDER BY COUNT(*) DESC", ["string", "int32"], [["b", 3]]),
    ("SELECT (SELECT COUNT(*) FROM t) c", ["int32"], [[5]]),
    # MIN and MAX keep their type
    ("SELECT MIN(i) a, MAX(b) b, MIN(si) c, MAX(d) d, MIN(g) e FROM t",
     ["int32", "int64", "int16", "decimal128(10, 2)", "string"], [[-4, 40, 1, "3.00", "a"]]),
])
def test_value_and_type(engine, sql, types, rows):
    assert result(engine, sql) == (types, rows)


@pytest.mark.parametrize("sql, says", [
    ("SELECT AVG(x) FROM (VALUES (2147483647), (1)) v(x)", INT_OVERFLOW),  # T-SQL's total is an int
    ("SELECT AVG(x) OVER () FROM (VALUES (2147483647), (1)) v(x)", INT_OVERFLOW),
    ("SELECT AVG(CAST(x AS BIGINT)) FROM (VALUES (9223372036854775807), (1)) v(x)", BIGINT_OVERFLOW),
    ("SELECT g, SUM(x) FROM (VALUES ('a', 2147483647), ('a', 1)) v(g, x) GROUP BY ROLLUP(g)", INT_OVERFLOW),
    ("SELECT * FROM (SELECT 'x' h, 2147483647 i UNION ALL SELECT 'x', 1) s PIVOT (SUM(i) FOR h IN ([x])) p",
     INT_OVERFLOW),
    (("SELECT * FROM (SELECT 'x' h, CAST(9223372036854775807 AS BIGINT) i UNION ALL SELECT 'x', 1) s "
      "PIVOT (SUM(i) FOR h IN ([x])) p"), BIGINT_OVERFLOW),
    ("WITH c AS (SELECT SUM(x) s FROM (VALUES (2147483647), (0)) v(x)) SELECT SUM(s + s) FROM c", INT_OVERFLOW),
])
def test_past_the_type_stops(engine, sql, says):
    assert says in error(engine, sql)


def test_avg_in_a_pivot_says_what_differs(engine):
    out = engine.execute(PIVOT.format(v="i", agg="AVG(i)"), dialect="tsql")
    assert any("AVG of an integer in a PIVOT" in n for n in out["translation"]["notes"])
