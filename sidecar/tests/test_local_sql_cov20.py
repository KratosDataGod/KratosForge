"""The local SQL engines: the paths the other tests leave out (coverage pass 20). A DuckDB cell DuckDB
can't split still reports DuckDB's own error, a Databricks cell with no statement is refused as empty, and
a SQLite result past its first batch counts every row while keeping only the preview."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop.engines import local_sql as ls
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture
def duck():
    s = ls.DuckDBAdapter().open_session(SessionConfig("nb"))
    yield s
    s.close()


@pytest.mark.skipif(ls.duckdb_major() >= 2, reason="DuckDB 2 runs a cell through con.sql, not statement by statement")
def test_a_cell_duckdb_cant_split_reports_duckdbs_own_parser_error(duck):
    result = duck.execute(CellRequest("SELEC 1", "sql"))
    assert result.status == "error"
    assert result.outputs[-1]["ename"] == "ParserException"
    assert 'syntax error at or near "SELEC"' in result.outputs[-1]["evalue"]
    assert duck.execute(CellRequest("SELECT 2 AS n", "sql")).rows == 1   # the session still runs


def test_last_query_leaves_code_duckdb_cant_split_for_execute(duck):
    class Con:
        def extract_statements(self, code):
            raise duckdb.ParserException("cannot split")
    real, duck._con = duck._con, Con()
    try:
        assert duck._last_query("SELEC 1; SELECT 2") == (None, "SELEC 1; SELECT 2")
    finally:
        duck._con = real   # the fixture closes the real connection


@pytest.mark.parametrize("code", ["", "   ", "-- only a comment"])
def test_a_databricks_cell_with_no_statement_is_refused_as_empty(duck, code):
    result = duck.execute(CellRequest(code, "sql", dialect="databricks"))
    assert result.status == "error"
    assert result.outputs[-1] == {"type": "error", "ename": "ValueError", "evalue": "The query is empty."}


def test_a_sqlite_result_past_its_first_batch_counts_every_row_and_keeps_the_preview():
    s = ls.SQLiteAdapter().open_session(SessionConfig("nb"))
    try:
        result = s.execute(CellRequest("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 25000) "
                                       "SELECT i FROM n", "sql", preview_rows=3))
    finally:
        s.close()
    assert result.status == "ok" and result.rows == 25000
    assert result.outputs[0]["rows"] == [[1], [2], [3]] and result.outputs[0]["total_rows"] == 25000
