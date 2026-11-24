"""WR-706: DECIMAL and NUMERIC with no precision are decimal(18, 0) in local T-SQL, as in a Fabric Warehouse.

DuckDB's DECIMAL with no precision is DECIMAL(18, 3), so CAST(1.5 AS DECIMAL) was 1.500 where T-SQL gives 2.
These tests cover every place a type is written (CAST, CONVERT, TRY_CAST, TRY_CONVERT, PARSE, TRY_PARSE, a column
of CREATE TABLE and ALTER TABLE ADD, SELECT INTO and a Delta write), the scale 0 that a precision alone gives,
T-SQL's rounding to the scale (half away from zero, from a decimal, a float or text), and the value and the type a
table keeps and gives back.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from fabric_desktop.localdata import LocalData, tsql


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Decimals")["id"])
    yield e
    local.close_all()


def one(engine, expression):
    result = engine.execute(f"SELECT {expression} AS v", dialect="tsql")
    return Decimal(str(result["rows"][0][0])), result["columns"][0]["type"]


# (T-SQL expression, its value, its type)
VALUES = [
    ("CAST(1.5 AS DECIMAL)", "2", "decimal128(18, 0)"),
    ("CAST(1.4 AS DECIMAL)", "1", "decimal128(18, 0)"),
    ("CAST(2.5 AS NUMERIC)", "3", "decimal128(18, 0)"),
    ("CAST(-2.5 AS DECIMAL)", "-3", "decimal128(18, 0)"),
    ("CAST(-0.5 AS DECIMAL)", "-1", "decimal128(18, 0)"),
    ("CAST(0.49 AS DEC)", "0", "decimal128(18, 0)"),
    ("CAST(2.5 AS NUMERIC(5))", "3", "decimal128(5, 0)"),
    ("CAST(-2.5 AS DECIMAL(5))", "-3", "decimal128(5, 0)"),
    ("CONVERT(DECIMAL, 1.5)", "2", "decimal128(18, 0)"),
    ("CONVERT(NUMERIC, '3.5')", "4", "decimal128(18, 0)"),
    ("TRY_CAST(1.5 AS DECIMAL)", "2", "decimal128(18, 0)"),
    ("TRY_CONVERT(NUMERIC, '-2.5')", "-3", "decimal128(18, 0)"),
    ("CAST('2.5' AS DECIMAL)", "3", "decimal128(18, 0)"),
    ("CAST(2.5E0 AS DECIMAL)", "3", "decimal128(18, 0)"),
    ("CAST(-2.5E0 AS DECIMAL)", "-3", "decimal128(18, 0)"),
    ("CAST(CAST(2.5 AS REAL) AS DECIMAL)", "3", "decimal128(18, 0)"),
    ("PARSE('2.5' AS DECIMAL)", "3", "decimal128(18, 0)"),
    ("TRY_PARSE('-2.5' AS NUMERIC)", "-3", "decimal128(18, 0)"),
    ("CAST(999999999999999999 AS DECIMAL)", "999999999999999999", "decimal128(18, 0)"),
    # with a precision and a scale nothing changes
    ("CAST(1.45 AS DECIMAL(5, 1))", "1.5", "decimal128(5, 1)"),
    ("CAST(-1.45 AS DECIMAL(5, 1))", "-1.5", "decimal128(5, 1)"),
    ("CAST(1.5 AS DECIMAL(10, 3))", "1.500", "decimal128(10, 3)"),
]


@pytest.mark.parametrize("expression, value, kind", VALUES)
def test_value_and_type(engine, expression, value, kind):
    got, got_kind = one(engine, expression)
    assert str(got) == value and got_kind == kind


def test_arithmetic_on_it_keeps_t_sqls_scale(engine):
    """decimal(18, 0) + decimal(3, 2) is decimal(21, 2) in T-SQL."""
    assert one(engine, "CAST(1.5 AS DECIMAL) + 0.25") == (Decimal("2.25"), "decimal128(21, 2)")


@pytest.mark.parametrize("sql", ["SELECT CAST(1.5 AS DECIMAL)", "SELECT CONVERT(NUMERIC, 1.5)",
                                 "SELECT TRY_CAST(1.5 AS DEC)", "CREATE TABLE dbo.x (a DECIMAL)"])
def test_the_translation_writes_the_precision_out(sql):
    texts, _notes = tsql.translate(sql)
    assert "DECIMAL(18, 0)" in texts[0], texts


def test_create_table_columns(engine):
    engine.execute("CREATE TABLE dbo.x (a DECIMAL, b NUMERIC(5), c DECIMAL(5, 2))", dialect="tsql")
    engine.execute("INSERT INTO dbo.x VALUES (1.5, -2.5, 1.005)", dialect="tsql")
    result = engine.execute("SELECT a, b, c FROM dbo.x", dialect="tsql")
    assert [str(v) for v in result["rows"][0]] == ["2", "-3", "1.01"]
    assert [c["type"] for c in result["columns"]] == ["decimal128(18, 0)", "decimal128(5, 0)", "decimal128(5, 2)"]


def test_alter_table_add_column(engine):
    engine.execute("CREATE TABLE dbo.x (id INT)", dialect="tsql")
    engine.execute("ALTER TABLE dbo.x ADD d NUMERIC", dialect="tsql")
    engine.execute("INSERT INTO dbo.x VALUES (1, 7.5)", dialect="tsql")
    result = engine.execute("SELECT d FROM dbo.x", dialect="tsql")
    assert str(result["rows"][0][0]) == "8" and result["columns"][0]["type"] == "decimal128(18, 0)"


def test_select_into_keeps_the_type(engine):
    engine.execute("SELECT CAST(2.5 AS DECIMAL) AS a INTO dbo.y", dialect="tsql")
    result = engine.execute("SELECT a FROM dbo.y", dialect="tsql")
    assert str(result["rows"][0][0]) == "3" and result["columns"][0]["type"] == "decimal128(18, 0)"


def test_a_delta_table_keeps_the_value_and_the_type(engine):
    written = engine.write_delta("dbo.d", "SELECT CAST(2.5 AS DECIMAL) AS a, CAST(-1.5 AS NUMERIC(4)) AS b",
                                 dialect="tsql")
    assert [c["type"] for c in written["columns"]] == ["decimal128(18, 0)", "decimal128(4, 0)"]
    result = engine.execute("SELECT a, b FROM dbo.d", dialect="tsql")
    assert [str(v) for v in result["rows"][0]] == ["3", "-2"]


def test_a_value_read_back_is_the_same_value(engine):
    engine.execute("CREATE TABLE dbo.r (a DECIMAL)", dialect="tsql")
    engine.execute("INSERT INTO dbo.r VALUES (123456789012345678)", dialect="tsql")
    result = engine.execute("SELECT a, CAST(a AS VARCHAR(30)) AS t FROM dbo.r", dialect="tsql")
    assert [str(v) for v in result["rows"][0]] == ["123456789012345678", "123456789012345678"]


def test_text_that_isnt_a_number_is_still_msg_8114(engine):
    with pytest.raises(Exception, match="Msg 8114, Level 16: Error converting data type varchar to numeric."):
        engine.execute("SELECT CAST('abc' AS DECIMAL) AS v", dialect="tsql")


def test_a_number_too_big_for_18_digits_is_an_error_not_a_wrong_value(engine):
    with pytest.raises(Exception, match="out of range"):
        engine.execute("SELECT CAST(9999999999999999999 AS DECIMAL) AS v", dialect="tsql")
    assert engine.execute("SELECT TRY_CAST(1E30 AS DECIMAL) AS v", dialect="tsql")["rows"] == [[None]]
