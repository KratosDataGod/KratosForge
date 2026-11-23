"""WR-704: local T-SQL's integer arithmetic has T-SQL's types, and a table the script made earlier is typed.

Microsoft Learn, "Data type precedence" and "+ (Addition)": an operator on two types gives the one with the higher
precedence (bigint, int, smallint, tinyint), and an integer literal is an int. So a tinyint times 100 is an int, a
smallint plus 1 an int, a tinyint plus a tinyint a tinyint (past 255, or below 0, error 8115), and a smallint plus a
tinyint a smallint. DuckDB kept the narrower type, since its integer literal takes the other operand's type, and
stopped with its own words past it.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


def overflow(kind: str) -> str:
    return f"Msg 8115, Level 16: Arithmetic overflow error converting expression to data type {kind}."


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("SmallInts")["id"])
    e.execute("CREATE TABLE t (si SMALLINT, i INT, b BIGINT)", dialect="tsql")
    e.execute("INSERT INTO t VALUES (32767, 2147483646, 9223372036854775806)", dialect="tsql")
    e.execute("CREATE TABLE #tt (ti TINYINT, si SMALLINT)", dialect="tsql")
    e.execute("INSERT INTO #tt VALUES (255, 32767)", dialect="tsql")
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
    # a tinyint or smallint with an int literal is an int
    ("SELECT CAST(255 AS TINYINT) * 100", ["int32"], [[25500]]),
    ("SELECT CAST(255 AS TINYINT) + 1, CAST(0 AS TINYINT) - 1", ["int32", "int32"], [[256, -1]]),
    ("SELECT CAST(32767 AS SMALLINT) + 1, CAST(-32768 AS SMALLINT) - 1", ["int32", "int32"], [[32768, -32769]]),
    ("SELECT ti * 100, ti + 1, ti % 7, ti / 2 FROM #tt", ["int32"] * 4, [[25500, 256, 3, 127]]),
    ("SELECT si + 1, si * 2, si - -1, si % 10, si / 2 FROM t", ["int32"] * 5, [[32768, 65534, 32768, 7, 16383]]),
    ("SELECT CAST(-32768 AS SMALLINT) / -1", ["int32"], [[32768]]),
    # with a wider column, the wider type
    ("SELECT ti + CAST(1 AS SMALLINT), ti + CAST(1 AS INT), ti + CAST(1 AS BIGINT) FROM #tt",
     ["int16", "int32", "int64"], [[256, 256, 256]]),
    ("SELECT si + CAST(1 AS INT), si + CAST(1 AS BIGINT), si - ti FROM #tt", ["int32", "int64", "int16"],
     [[32768, 32768, 32512]]),
    # a tinyint with a tinyint stays a tinyint, and a smallint with a smallint a smallint, inside the range
    ("SELECT CAST(200 AS TINYINT) + CAST(55 AS TINYINT), CAST(7 AS TINYINT) / CAST(2 AS TINYINT)",
     ["uint8", "uint8"], [[255, 3]]),
    ("SELECT CAST(200 AS TINYINT) + CAST(100 AS SMALLINT)", ["int16"], [[300]]),
    ("SELECT CAST(-32768 AS SMALLINT) / CAST(2 AS SMALLINT)", ["int16"], [[-16384]]),
    # nested: the inner int carries out
    ("SELECT (CAST(255 AS TINYINT) + 1) * 2", ["int32"], [[512]]),
    ("SELECT SUM(si * 10) FROM t", ["int32"], [[327670]]),
    # a decimal or a big literal keeps its own rules
    ("SELECT CAST(250 AS TINYINT) * 1.5", ["decimal128(6, 1)"], [["375.0"]]),
    ("SELECT CAST(250 AS TINYINT) + 3000000000", ["decimal128(11, 0)"], [["3000000250"]]),
])
def test_integer_arithmetic_has_tsql_type(engine, sql, types, rows):
    assert result(engine, sql) == (types, rows)


@pytest.mark.parametrize("sql, kind", [
    ("SELECT CAST(255 AS TINYINT) + CAST(1 AS TINYINT)", "tinyint"),
    ("SELECT CAST(1 AS TINYINT) - CAST(2 AS TINYINT)", "tinyint"),
    ("SELECT CAST(16 AS TINYINT) * CAST(16 AS TINYINT)", "tinyint"),
    ("SELECT ti + ti FROM #tt", "tinyint"),
    ("SELECT si + si FROM t", "smallint"),
    ("SELECT si * ti FROM #tt", "smallint"),
    ("SELECT CAST(-32768 AS SMALLINT) - CAST(1 AS SMALLINT)", "smallint"),
    ("SELECT CAST(-32768 AS SMALLINT) / CAST(-1 AS SMALLINT)", "smallint"),
    ("SELECT si + i * 1 + 2 FROM t", "int"),
    ("SELECT CAST(-2147483648 AS INT) / CAST(-1 AS INT)", "int"),
    ("SELECT CAST(-9223372036854775808 AS BIGINT) / -1", "bigint"),
    ("SELECT b + CAST(2 AS TINYINT) FROM t", "bigint"),
])
def test_past_the_type_is_8115(engine, sql, kind):
    assert error(engine, sql) == overflow(kind)


def test_division_still_stops_on_zero(engine):
    assert error(engine, "SELECT si / 0 FROM t") == "Msg 8134, Level 16: Divide by zero error encountered."
    assert error(engine, "SELECT ti / CAST(0 AS TINYINT) FROM #tt") == \
        "Msg 8134, Level 16: Divide by zero error encountered."


# WR-704, part 2: a table made earlier in the same script is typed when the script is read, so SUM and AVG of its
# int column are an int (they were a decimal and a float).

@pytest.mark.parametrize("script, types, rows", [
    (["CREATE TABLE dbo.u (i INT)", "INSERT INTO dbo.u VALUES (1), (2)", "SELECT SUM(i), AVG(i) FROM dbo.u"],
     ["int32", "int32"], [[3, 1]]),
    (["CREATE TABLE u (i INT)", "INSERT INTO u VALUES (-1), (-2)", "SELECT SUM(i), AVG(i) FROM u"],
     ["int32", "int32"], [[-3, -1]]),
    (["CREATE TABLE #u (ti TINYINT, si SMALLINT)", "INSERT INTO #u VALUES (255, 32767)",
      "SELECT ti * 100, si + 1, SUM(si) FROM #u GROUP BY ti, si"], ["int32", "int32", "int32"], [[25500, 32768, 32767]]),
    # CTAS and SELECT INTO, from the database's table and from one the script made
    (["CREATE TABLE dbo.c AS SELECT i, COUNT(*) AS n, CAST(32767 AS SMALLINT) AS s FROM t GROUP BY i",
      "SELECT SUM(n), AVG(i), s + 1 FROM dbo.c GROUP BY s"], ["int32", "int32", "int32"], [[1, 2147483646, 32768]]),
    (["CREATE TABLE dbo.v (i INT)", "INSERT INTO dbo.v VALUES (7)", "SELECT i, 1.5 AS d, i * 1.5 AS m INTO #w FROM dbo.v",
      "SELECT i / 2, d * 2, AVG(m) FROM #w GROUP BY i, d"], ["int32", "decimal128(4, 1)", "decimal128(38, 6)"],
     [[3, "3.0", "10.500000"]]),
    # ALTER TABLE ... ADD, on the database's table and on one the script made
    (["ALTER TABLE t ADD k INT", "UPDATE t SET k = 5", "SELECT SUM(k), AVG(k) FROM t"], ["int32", "int32"], [[5, 5]]),
    (["CREATE TABLE #x (a INT)", "ALTER TABLE #x ADD k SMALLINT", "INSERT INTO #x VALUES (1, 32767)",
      "SELECT k + 1, AVG(a) FROM #x GROUP BY k"], ["int32", "int32"], [[32768, 1]]),
    # a view, and a UNION whose branches have one type
    (["CREATE TABLE dbo.vt (i INT)", "INSERT INTO dbo.vt VALUES (1), (2)",
      "CREATE VIEW dbo.vv AS SELECT i, i * 2 AS j FROM dbo.vt", "SELECT SUM(j), AVG(i) FROM dbo.vv"],
     ["int32", "int32"], [[6, 1]]),
    (["SELECT si INTO #un FROM t UNION ALL SELECT CAST(4 AS SMALLINT)", "SELECT AVG(si) / 2, MAX(si) / 2 FROM #un"],
     ["int32", "int32"], [[8192, 16383]]),
    # dropped and made again with another type: the new type
    (["CREATE TABLE dbo.y (c INT)", "DROP TABLE dbo.y", "CREATE TABLE dbo.y (c DECIMAL(5, 2))",
      "INSERT INTO dbo.y VALUES (1.25)", "SELECT SUM(c), AVG(c) FROM dbo.y"], ["decimal128(38, 2)", "decimal128(38, 6)"],
     [["1.25", "1.250000"]]),
])
def test_table_made_earlier_in_the_script_is_typed(engine, script, types, rows):
    assert result(engine, "; ".join(script)) == (types, rows)


def test_overflow_in_a_table_made_earlier(engine):
    assert error(engine, ("CREATE TABLE dbo.z (i INT); INSERT INTO dbo.z VALUES (2147483647), (1); "
                          "SELECT SUM(i) FROM dbo.z")) == \
        "Statement 3 of 3 (SELECT) failed: " + overflow("int") + " Statements 1 to 2 ran, and what they changed is kept."
    assert error(engine, "SELECT 2147483647 AS big INTO #b; SELECT big + 1 FROM #b").startswith(
        "Statement 2 of 2 (SELECT) failed: " + overflow("int"))


def test_union_of_two_types_says_it_divides_as_duckdb(engine):
    out = engine.execute(("SELECT i AS x INTO #mixed FROM t UNION ALL SELECT CAST(1.5 AS DECIMAL(5, 1)); "
                          "SELECT x / 2 FROM #mixed"), dialect="tsql")
    assert any("Couldn't tell the types in x / 2" in note for note in out["translation"]["notes"])
