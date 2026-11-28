"""tsql_errors: DuckDB's "Could not convert string ... to INT32" for a value that isn't a whole number is
SQL Server's 245; a whole number (too big, which SQL Server words otherwise) or '' (0 in SQL Server) keeps
DuckDB's words. The messages are DuckDB's own, read from a real cast."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop.localdata import tsql_errors


def _duckdb_error(sql: str) -> str:
    with duckdb.connect() as con:
        with pytest.raises(duckdb.Error) as raised:
            con.execute(sql)
    return str(raised.value)


@pytest.mark.parametrize(("value", "kind", "name"), [
    ("abc", "INTEGER", "int"),
    ("x", "SMALLINT", "smallint"),
    (" 12x", "BIGINT", "bigint"),
    ("x", "UTINYINT", "tinyint"),
])
def test_text_that_isnt_a_whole_number_is_245(value, kind, name):
    sql = f"SELECT CAST('{value}' AS {kind})"
    got = tsql_errors.warehouse(_duckdb_error(sql), sql, "select")
    assert got == f"Conversion failed when converting the varchar value '{value}' to data type {name}."
    assert tsql_errors.numbered(got) == f"Msg 245, Level 16: {got}"


@pytest.mark.parametrize("value", ["99999999999", "", "  ", "-99999999999"])
def test_a_whole_number_too_big_or_blank_text_keeps_duckdbs_words(value):
    sql = f"SELECT CAST('{value}' AS INTEGER)"
    message = _duckdb_error(sql)
    assert "Could not convert string" in message
    assert tsql_errors.warehouse(message, sql, "select") is None


def test_the_rule_reads_duckdbs_first_line_only():
    message = "Conversion Error: Could not convert string 'q' to INT64\n\nLINE 1: SELECT ..."
    assert tsql_errors.warehouse(message, "SELECT 1", "select") == \
        "Conversion failed when converting the varchar value 'q' to data type bigint."
