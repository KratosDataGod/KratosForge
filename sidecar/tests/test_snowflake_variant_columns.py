"""Snowflake ARRAY, VARIANT and OBJECT results show as JSON even when two columns' names differ only in case:
each column is written from its own values, by position, never from a column whose name matches but for case."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture
def duck() -> Iterator[local_sql.DuckDBSession]:
    session = local_sql.DuckDBSession(SessionConfig("nb"))
    yield session
    session.close()


def rows(duck: local_sql.DuckDBSession, sql: str) -> list[dict[str, Any]]:
    result = duck.execute(CellRequest(sql, "sql", dialect="snowflake", result_rows=10))
    assert result.status == "ok", result
    assert result.table is not None
    return result.table.to_pylist()


TWO_ARRAYS = "SELECT [1, 2]::ARRAY AS a, [3]::ARRAY AS A"


def test_two_array_columns_named_alike_but_for_case_show_as_json(duck: local_sql.DuckDBSession) -> None:
    result = duck.execute(CellRequest(TWO_ARRAYS, "sql", dialect="snowflake", result_rows=10))
    assert result.status == "ok" and result.table is not None and result.table.to_pylist() == [{"a": "[1,2]", "A": "[3]"}]


def test_a_variant_and_an_object_named_alike_but_for_case_show_as_json(duck: local_sql.DuckDBSession) -> None:
    # both are VARIANT columns in DuckDB; a plain OBJECT (JSON in DuckDB) beside them is left as it was
    sql = ("SELECT PARSE_JSON('[1, \"x\"]')::VARIANT AS v, OBJECT_CONSTRUCT('k', 1)::VARIANT AS V, "
           "OBJECT_CONSTRUCT('k', 2) AS \"vV\"")
    assert rows(duck, sql) == [{"v": '[1,"x"]', "V": '{"k":1}', "vV": '{"k":2}'}]


def test_three_columns_named_alike_but_for_case_keep_their_own_values(duck: local_sql.DuckDBSession) -> None:
    sql = "SELECT [1]::ARRAY AS x, 7 AS X, ['b']::ARRAY AS \"x\"\"\""
    assert rows(duck, sql) == [{"x": "[1]", "X": 7, 'x"': '["b"]'}]


def test_quoted_identifiers_in_mixed_case_keep_their_names_and_values(duck: local_sql.DuckDBSession) -> None:
    sql = "SELECT [1]::ARRAY AS \"Col\", [2]::ARRAY AS \"COL\", 'plain' AS \"col\""
    assert rows(duck, sql) == [{"Col": "[1]", "COL": "[2]", "col": "plain"}]


def test_a_single_array_column_still_shows_as_json(duck: local_sql.DuckDBSession) -> None:
    assert rows(duck, "SELECT [1, 2, 3]::ARRAY AS a") == [{"a": "[1,2,3]"}]
