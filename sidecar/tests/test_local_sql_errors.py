"""The local SQL engines' error paths (KUDP hardening): what DuckDB, SQLite and Polars say when a cell
can't run, and that a refused or failing cell leaves the session usable. Nothing here leaves this PC."""

from __future__ import annotations

import pytest

from fabric_desktop.engines import registry
from fabric_desktop.engines.contract import CellRequest, SessionClosed, SessionConfig, Unsupported


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


def error_of(r):
    assert r.status == "error" and r.outputs and r.outputs[-1]["type"] == "error", r.outputs
    return r.outputs[-1]["evalue"]


@pytest.mark.parametrize("engine", ["local-duckdb", "local-sqlite", "local-polars"])
def test_a_cell_in_another_language_is_refused_and_the_session_still_runs(opened, engine):
    s = opened(engine)
    with pytest.raises(Unsupported, match="SQL cells only"):
        s.execute(CellRequest("print(1)", "python"))
    assert s.execute(CellRequest("SELECT 1 AS id", "sql")).status == "ok"


@pytest.mark.parametrize("engine", ["local-duckdb", "local-sqlite", "local-polars"])
def test_a_second_cell_while_one_runs_is_refused(opened, engine):
    s = opened(engine)
    assert s._lock.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            s.execute(CellRequest("SELECT 1", "sql"))
    finally:
        s._lock.release()
    assert s.execute(CellRequest("SELECT 1 AS id", "sql")).status == "ok"


@pytest.mark.parametrize("engine", ["local-duckdb", "local-sqlite", "local-polars"])
def test_a_failing_cell_leaves_the_session_usable(opened, engine):
    s = opened(engine)
    assert error_of(s.execute(CellRequest("SELECT * FROM no_such_table", "sql")))
    assert s.execute(CellRequest("SELECT 1 AS id", "sql")).status == "ok"


def test_sqlite_says_an_empty_cell_is_empty(opened):
    assert error_of(opened("local-sqlite").execute(CellRequest("  ;  ", "sql"))) == "The cell is empty."


@pytest.mark.parametrize("code", ["SELECT 1; SELECT 2", ""])
def test_sqlite_plans_one_statement_at_a_time(opened, code):
    with pytest.raises(ValueError, match="one statement at a time"):
        opened("local-sqlite").plan(code)


@pytest.mark.parametrize("engine, dialect", [("local-sqlite", "spark"), ("local-polars", "spark"),
                                             ("local-polars", "duckdb")])
def test_a_dialect_the_engine_doesnt_run_is_an_error_that_names_it(opened, engine, dialect):
    assert dialect in error_of(opened(engine).execute(CellRequest("SELECT 1", "sql", dialect)))


def test_duckdb_says_when_spark_sql_cant_be_read(opened):
    message = error_of(opened("local-duckdb").execute(CellRequest("SELEC FROM WHERE ((", "sql", "databricks")))
    assert message.startswith("Could not read the query")


@pytest.mark.parametrize("dialect", ["spark", "databricks", None])
@pytest.mark.parametrize("path", ["/etc/passwd", "C:/Windows/win.ini"])
def test_duckdb_cells_cant_read_files_on_this_pc_in_any_dialect(opened, dialect, path):
    s = opened("local-duckdb")
    assert error_of(s.execute(CellRequest(f"SELECT * FROM read_csv('{path}')", "sql", dialect)))
    assert s.execute(CellRequest("SELECT 1 AS id", "sql", dialect)).status == "ok"


def test_duckdb_plans_refuse_files_too(opened):
    with pytest.raises(Exception):   # DuckDB's own wording; what matters is that no plan comes back
        opened("local-duckdb").plan("SELECT * FROM read_parquet('/etc/x.parquet')", "spark")


def test_a_duckdb_plan_of_snowflake_sql_says_it_is_the_translation(opened):
    assert opened("local-duckdb").plan("SELECT 1", "snowflake").startswith("Plan of the DuckDB translation")


@pytest.mark.parametrize("engine", ["local-duckdb", "local-sqlite", "local-polars"])
def test_close_is_final_and_safe_to_repeat(opened, engine):
    s = opened(engine)
    s.close()
    s.close()
    assert s.closed and s.cancel() is False
    with pytest.raises(SessionClosed):
        s.execute(CellRequest("SELECT 1", "sql"))


def test_duckdb_cells_raise_no_warnings_on_either_duckdb(opened):
    # DuckDB 2.0 warns that Arrow from an executed (DB-API) result copies every row once more; the
    # engine reads a relation there instead (#1341). Warnings as errors: any warning fails the cell.
    import warnings
    s = opened("local-duckdb")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        r = s.execute(CellRequest("CREATE TABLE t AS SELECT * FROM range(3) r(id)", "sql"))
        assert r.status == "ok", r.outputs
        assert s.execute(CellRequest("SELECT id FROM t ORDER BY id", "sql")).outputs[0]["rows"] == [[0], [1], [2]]
        # Several statements give the last one's rows; the ones before it still ran.
        r = s.execute(CellRequest("INSERT INTO t VALUES (3); SELECT count(*) AS n FROM t", "sql"))
        assert r.status == "ok" and r.outputs[0]["rows"] == [[4]], r.outputs
        # Spark SQL, which runs with the zone set to UTC and back.
        r = s.execute(CellRequest("SELECT max(id) AS m FROM t", "sql", dialect="spark"))
        assert r.status == "ok" and r.outputs[0]["rows"] == [[3]], r.outputs
        # A value that fails while the rows are read is an error, and the session still runs.
        assert error_of(s.execute(CellRequest("SELECT 'x'::INT AS bad", "sql")))
        assert s.execute(CellRequest("SELECT 1 AS id", "sql")).status == "ok"
