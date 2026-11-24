"""WR-706 in a script: a variable declared DECIMAL or NUMERIC with no precision is decimal(18, 0), as a CAST to one
is, and takes a value rounded half away from zero, from a decimal, a float or text."""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("ScriptDecimals")["id"])
    yield e
    local.close_all()


def value(engine, sql):
    result = engine.execute(sql, dialect="tsql")
    return str(result["rows"][0][0]), result["columns"][0]["type"]


@pytest.mark.parametrize("declared, given, want, kind", [
    ("DECIMAL", "1.5", "2", "decimal128(18, 0)"),
    ("DECIMAL", "-2.5", "-3", "decimal128(18, 0)"),
    ("NUMERIC", "'2.5'", "3", "decimal128(18, 0)"),
    ("DEC", "2.5E0", "3", "decimal128(18, 0)"),
    ("NUMERIC(5)", "-0.5", "-1", "decimal128(5, 0)"),
    ("DECIMAL(5, 1)", "1.45", "1.5", "decimal128(5, 1)"),
])
def test_declare_with_a_value(engine, declared, given, want, kind):
    assert value(engine, f"DECLARE @d {declared} = {given}; SELECT @d AS d") == (want, kind)


def test_set_and_select_assign_as_declare_does(engine):
    assert value(engine, "DECLARE @d DECIMAL; SET @d = 3.5; SELECT @d AS d")[0] == "4"
    assert value(engine, "DECLARE @d DECIMAL; SELECT @d = 3.5; SELECT @d AS d")[0] == "4"


def test_the_same_as_a_cast(engine):
    assert value(engine, "DECLARE @d DECIMAL = 1.5; SELECT CASE WHEN @d = CAST(1.5 AS DECIMAL) THEN 1 ELSE 0 END AS same") \
        == ("1", "int32")
