"""A Snowflake VARIANT or ARRAY column in a cell's result shows as JSON on DuckDB 1.x and 2.0 (WR-312): DuckDB
1.x can't give a VARIANT to Arrow, and DuckDB 2.0 gives its stored bytes."""

from __future__ import annotations

import pytest

from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture()
def cell():
    session = local_sql.DuckDBSession(SessionConfig("nb"))

    def run(code, dialect="snowflake"):
        result = session.execute(CellRequest(code, "sql", dialect))
        output = result.outputs[-1] if result.outputs else {}
        assert result.status == "ok", output
        return output.get("rows")
    yield run
    session.close()


def test_an_array_column_shows_as_json(cell):
    assert cell("CREATE TABLE ta (a ARRAY, n INT); INSERT INTO ta SELECT array_construct(1, 2), 7; "
                "SELECT a, n FROM ta") == [["[1,2]", 7]]


def test_a_variant_shows_as_json_with_each_value_its_own_type(cell):
    assert cell("SELECT [1::VARIANT, 'a'::VARIANT, 3.5::VARIANT, NULL] AS a, {'k': [1, 2]}::VARIANT AS \"v w\", "
                "2 AS n", "duckdb") == [['[1,"a",3.5,null]', '{"k":[1,2]}', 2]]


def test_a_query_without_a_variant_is_as_it_was(cell):
    assert cell("SELECT [1, 2] AS a, 'x' AS b", "duckdb") == [["[1, 2]", "x"]]


def test_a_variant_result_after_a_change_comes_from_the_last_statement_only(cell):
    cell("CREATE TABLE c (v VARIANT)", "duckdb")
    assert cell("INSERT INTO c VALUES (1::VARIANT); SELECT v FROM c", "duckdb") == [["1"]]
    assert cell("SELECT count(*) FROM c", "duckdb") == [[1]]
