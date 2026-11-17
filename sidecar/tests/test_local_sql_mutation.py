"""Tests for the SQL engines on this PC (engines/local_sql.py) that mutation testing showed were missing.

Each test pins one behaviour a changed line in local_sql.py got past: the whole-result limits in
_drain and _keep, the one-cell-at-a-time lock, the DuckDB connection's closed settings, which parser
each dialect goes to, the Snowflake plan's label, the catalog's view and table types, and Polars'
results."""

from __future__ import annotations

import threading

import pyarrow as pa
import pytest

from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture
def duck():
    session = local_sql.DuckDBSession(SessionConfig("nb"))
    yield session
    session.close()


def _reader(*sizes: int) -> pa.RecordBatchReader:
    """An Arrow reader with one batch of ``n`` rows for each size, the values counting up from 0."""
    schema = pa.schema([("a", pa.int64())])
    start, batches = 0, []
    for n in sizes:
        batches.append(pa.record_batch([pa.array(range(start, start + n), pa.int64())], schema=schema))
        start += n
    return pa.RecordBatchReader.from_batches(schema, batches)


def test_the_result_limit_reads_256_mb_in_the_descriptor_and_the_error():
    assert "256 MB" in local_sql.DUCKDB.capabilities["result"].note
    assert "larger than 256 MB" in str(local_sql._too_big())


def test_a_statement_with_no_rows_says_it_is_done(duck):
    result = duck.execute(CellRequest("SET threads = 1", "sql"))
    assert result.status == "ok"
    # a DuckDB SET answers with a Success row, so make one that has none: Polars' DROP TABLE
    polars = local_sql.PolarsSession(SessionConfig("nb"))
    polars.execute(CellRequest("CREATE TABLE p AS SELECT 1 AS a", "sql"))
    done = polars.execute(CellRequest("DROP TABLE p", "sql"))
    assert done.status == "ok" and done.rows is None
    assert done.outputs[-1]["type"] == "text" and done.outputs[-1]["text"].startswith("Done in ")


def test_a_second_cell_while_one_runs_is_refused_at_once(duck):
    duck._lock.acquire()
    outcome: list[BaseException] = []

    def second() -> None:
        try:
            duck.execute(CellRequest("SELECT 1", "sql"))
        except BaseException as exc:  # noqa: BLE001 - the test reads what was raised
            outcome.append(exc)

    worker = threading.Thread(target=second, daemon=True)
    try:
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive(), "the second cell waited for the first instead of being refused"
    finally:
        duck._lock.release()
        worker.join(timeout=5)
    assert len(outcome) == 1 and isinstance(outcome[0], RuntimeError)
    assert "already running" in str(outcome[0])


def test_an_error_cell_reports_the_seconds_it_took(duck):
    result = duck.execute(CellRequest("SELECT * FROM no_such_table", "sql"))
    assert result.status == "error"
    assert 0 <= result.seconds < 60


def test_keep_refuses_more_rows_than_asked_for(duck):
    table = pa.table({"a": [1, 2, 3]})
    with pytest.raises(local_sql.ResultTooLarge, match="more than the 2 rows"):
        duck._keep(table, 3, CellRequest("", "sql", result_rows=2))


def test_keep_refuses_a_table_over_the_byte_limit(duck, monkeypatch):
    monkeypatch.setattr(local_sql, "RESULT_BYTES", 8)
    table = pa.table({"a": [1, 2, 3]})
    with pytest.raises(local_sql.ResultTooLarge, match="larger than"):
        duck._keep(table, 3, CellRequest("", "sql", result_rows=5))


def test_drain_without_a_whole_result_keeps_only_the_preview():
    table, total = local_sql._drain(_reader(3), 0)
    assert (table.num_rows, total) == (0, 3)


def test_drain_keeps_a_whole_result_of_one_row_with_no_preview():
    table, total = local_sql._drain(_reader(1), 0, 1)
    assert (table.num_rows, total) == (1, 1)


def test_drain_stops_at_one_row_more_than_asked_for():
    with pytest.raises(local_sql.ResultTooLarge, match="more than the 1 rows"):
        local_sql._drain(_reader(2), 5, 1)


def test_drain_keeps_exactly_the_preview_across_batches():
    table, total = local_sql._drain(_reader(3, 3, 3), 4)
    assert total == 9
    assert table.column("a").to_pylist() == [0, 1, 2, 3]


def test_drain_stops_when_a_whole_result_of_one_row_is_too_big(monkeypatch):
    monkeypatch.setattr(local_sql, "RESULT_BYTES", 1)
    with pytest.raises(local_sql.ResultTooLarge, match="larger than"):
        local_sql._drain(_reader(1), 0, 1)


def test_drain_lets_a_whole_result_within_the_byte_limit_through():
    table, total = local_sql._drain(_reader(2, 2), 0, 10)
    assert (table.num_rows, total) == (4, 4)


@pytest.mark.parametrize("setting", ["enable_external_access", "autoinstall_known_extensions",
                                     "autoload_known_extensions"])
def test_a_duckdb_session_has_files_and_extensions_turned_off(duck, setting):
    value = duck._con.execute(f"SELECT current_setting('{setting}')").fetchone()[0]
    assert value is False or str(value).lower() == "false"


def _spy(monkeypatch, owner, name):
    calls: list[str] = []
    real = getattr(owner, name)

    def spy(code, *args, **kwargs):
        calls.append(code)
        return real(code, *args, **kwargs)

    monkeypatch.setattr(owner, name, spy)
    return calls


def test_refuse_outside_reads_snowflake_with_the_snowflake_parser_only(monkeypatch):
    from fabric_desktop import snowflake_sql
    calls = _spy(monkeypatch, snowflake_sql, "parse")
    local_sql._refuse_outside("SELECT 1", "duckdb")
    local_sql._refuse_outside("SELECT 1", "spark")
    assert calls == []
    local_sql._refuse_outside("SELECT 2", "snowflake")
    assert calls == ["SELECT 2"]


def test_refuse_outside_reads_databricks_with_the_databricks_reader_only(monkeypatch):
    calls = _spy(monkeypatch, local_sql, "_databricks")
    local_sql._refuse_outside("SELECT 1", "spark")
    local_sql._refuse_outside("SELECT 1", None)
    assert calls == []
    local_sql._refuse_outside("SELECT 2", "databricks")
    assert calls == ["SELECT 2"]


def test_sql_translates_databricks_with_the_databricks_reader_only(duck, monkeypatch):
    calls = _spy(monkeypatch, local_sql, "_databricks")
    duck._sql("SELECT 1", "spark")
    assert calls == []
    duck._sql("SELECT 2", "databricks")
    assert calls == ["SELECT 2"]


def test_databricks_timediff_counts_whole_units(duck):
    result = duck.execute(CellRequest("SELECT timediff(HOUR, TIMESTAMP'2024-01-01 00:00:00', "
                                      "TIMESTAMP'2024-01-01 05:30:00') AS h", "sql", "databricks"))
    assert result.status == "ok", result.outputs
    assert result.outputs[-1]["rows"] == [[5]]


def test_a_runnable_snowflake_cell_runs_its_translation(duck):
    result = duck.execute(CellRequest("SELECT 41 + 1 AS answer", "sql", "snowflake"))
    assert result.status == "ok", result.outputs
    assert result.rows == 1
    assert result.outputs[-1]["rows"] == [[42]]


def test_only_a_snowflake_plan_says_it_is_the_translation_plan(duck):
    assert duck.plan("SELECT 1", "snowflake").startswith("Plan of the DuckDB translation on this PC")
    assert "translation" not in duck.plan("SELECT 1")
    assert "translation" not in duck.plan("SELECT 1", "spark")


def test_an_insert_on_duckdb_answers_with_its_count(duck):
    duck.execute(CellRequest("CREATE TABLE t (a INTEGER)", "sql"))
    result = duck.execute(CellRequest("INSERT INTO t VALUES (1), (2)", "sql"))
    assert result.status == "ok", result.outputs
    assert result.rows == 1
    assert result.outputs[-1]["rows"] == [[2]]


def test_the_relation_path_runs_a_query_and_a_change(duck, monkeypatch):
    """DuckDB 2.0's path (con.sql and a relation's reader), forced on whatever DuckDB is installed."""
    monkeypatch.setattr(local_sql, "duckdb_major", lambda: 2)
    assert duck._run(CellRequest("SELECT 7 AS a", "sql"))[1] == 1
    assert duck._run(CellRequest("-- only a comment", "sql")) == (None, 0)


def test_the_catalog_tells_views_from_tables(duck):
    duck.execute(CellRequest("CREATE TABLE t (a INTEGER)", "sql"))
    duck.execute(CellRequest("CREATE VIEW v AS SELECT * FROM t", "sql"))
    types = {row["name"]: row["type"] for row in duck.catalog()}
    assert types["t"] == "table"
    assert types["v"] == "view"


def test_a_sqlite_statement_with_no_rows_counts_none():
    session = local_sql.SQLiteSession(SessionConfig("nb"))
    try:
        assert session._run(CellRequest("CREATE TABLE t (a INTEGER)", "sql")) == (None, 0)
    finally:
        session.close()


def test_a_polars_result_of_one_column_is_a_table():
    session = local_sql.PolarsSession(SessionConfig("nb"))
    try:
        result = session.execute(CellRequest("SELECT 1 AS a", "sql"))
        assert result.status == "ok" and result.rows == 1
        assert result.outputs[-1]["rows"] == [[1]]
        session.execute(CellRequest("CREATE TABLE p AS SELECT 1 AS a", "sql"))
        assert session._run(CellRequest("DROP TABLE p", "sql")) == (None, 0)
    finally:
        session.close()


def test_the_polars_adapter_describes_polars():
    assert local_sql.PolarsAdapter().describe() is local_sql.POLARS
