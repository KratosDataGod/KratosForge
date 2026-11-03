"""The local SQL engines' whole results and refusals (KUDP hardening): a whole result over its row or
byte limit is an error, never a cut table; a Snowflake cell that only runs in Snowflake shows its
translation and runs nothing; errors reach the output sink; close stops a running cell. Nothing here
leaves this PC."""

from __future__ import annotations

import pytest

from fabric_desktop.engines import local_sql, registry
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture
def opened():
    sessions = []

    def open_(engine):
        s = registry.adapter(engine).open_session(SessionConfig("nb"))
        sessions.append(s)
        return s
    yield open_
    for s in sessions:
        s.close()


@pytest.mark.parametrize("engine, code", [("local-duckdb", "SELECT * FROM range(100) t(i)"),
                                          ("local-sqlite", "WITH RECURSIVE r(i) AS (SELECT 1 UNION ALL "
                                                           "SELECT i + 1 FROM r WHERE i < 100) SELECT i FROM r")])
def test_a_whole_result_with_more_rows_than_asked_is_an_error_never_a_cut_table(opened, engine, code):
    r = opened(engine).execute(CellRequest(code, "sql", result_rows=10))
    assert r.status == "error" and r.table is None
    assert "more than the 10 rows asked for, so none of it was kept" in r.outputs[-1]["evalue"]


@pytest.mark.parametrize("engine", ["local-duckdb", "local-sqlite"])
def test_a_whole_result_over_the_byte_limit_is_an_error_never_a_cut_table(opened, monkeypatch, engine):
    monkeypatch.setattr(local_sql, "RESULT_BYTES", 64)
    r = opened(engine).execute(CellRequest("SELECT 'x' || 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' AS a, "
                                           "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' AS b",
                                           "sql", result_rows=10))
    assert r.status == "error" and r.table is None and "so none of it was kept" in r.outputs[-1]["evalue"]


def test_a_whole_result_within_its_limits_is_kept_whole_and_previewed(opened):
    r = opened("local-duckdb").execute(CellRequest("SELECT * FROM range(50) t(i)", "sql", result_rows=50, preview_rows=5))
    assert r.status == "ok" and r.table.num_rows == 50 and len(r.outputs[-1]["rows"]) == 5


@pytest.mark.parametrize("code, area", [("PUT file:///tmp/x @st", "Stages"), ("COPY INTO t FROM @st", "Stages"),
                                        ("SELECT * FROM t AT(OFFSET => -60)", "Time travel")])
def test_snowflake_only_sql_shows_its_translation_and_runs_nothing(opened, code, area):
    seen = []
    r = opened("local-duckdb").execute(CellRequest(code, "sql", "snowflake"), on_output=seen.append)
    assert r.status == "error" and [o["type"] for o in r.outputs] == ["translation", "error"]
    assert f"runs only in Snowflake: {area}" in r.outputs[-1]["evalue"]
    assert r.outputs[-1]["evalue"].endswith("Nothing in the cell ran.")
    assert seen == r.outputs   # the sink gets the label and the error, in order


def test_an_error_reaches_the_output_sink(opened):
    seen = []
    r = opened("local-sqlite").execute(CellRequest("SELECT * FROM nope", "sql"), on_output=seen.append)
    assert r.status == "error" and seen == r.outputs and seen[-1]["type"] == "error"


def test_spark_sql_duckdb_cant_run_is_unsupported_with_the_reason(opened, monkeypatch):
    from fabric_desktop import sql

    def refuse(_statement):
        raise ValueError("TRANSFORM has no DuckDB form")
    monkeypatch.setattr(sql, "duckdb_sql", refuse)
    r = opened("local-duckdb").execute(CellRequest("SELECT 1", "sql", "spark"))
    assert r.status == "error" and r.outputs[-1]["ename"] == "Unsupported"
    assert r.outputs[-1]["evalue"] == "TRANSFORM has no DuckDB form"


def test_duckdb_refuses_a_dialect_it_doesnt_run(opened):
    r = opened("local-duckdb").execute(CellRequest("SELECT 1", "sql", "tsql"))
    assert r.status == "error" and "doesn't run the tsql dialect" in r.outputs[-1]["evalue"]


def test_a_statement_with_no_result_has_no_row_count(opened):
    s = opened("local-sqlite")
    r = s.execute(CellRequest("CREATE TABLE t AS SELECT 1 AS i", "sql"))
    assert r.status == "ok" and r.rows is None and r.table is None
    assert s.execute(CellRequest("SELECT count(*) AS n FROM t", "sql")).outputs[-1]["rows"] == [[1]]


@pytest.mark.parametrize("engine", ["local-duckdb", "local-polars"])
def test_closing_a_session_with_a_cell_running_stops_it_or_lets_it_finish(opened, engine):
    s = opened(engine)
    stopped = []
    if engine == "local-duckdb":
        s._interrupt = lambda: stopped.append(True)
    s._running = True
    try:
        s.close()
    finally:
        s._running = False
    assert s.closed and stopped == ([True] if engine == "local-duckdb" else [])
