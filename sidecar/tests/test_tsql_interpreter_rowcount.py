"""WR-705: @@ROWCOUNT, PRINT's messages, and the policy check of a local T-SQL script.

These tests cover:
- @@ROWCOUNT after a SELECT, an INSERT, UPDATE and DELETE, an assignment, SET, and the statements that set it
  to 0 (IF, WHILE, PRINT, DECLARE without a value);
- PRINT's text, in order, in the result's ``messages``: from execute, from execute_reader's stream, and in a
  managed local run's status (sql_start with target local), with no new screen;
- the policy check (sql_exec.check_local), which classes each statement of a script and numbers its verdicts by
  the user's statements, so a DROP or a DELETE with no WHERE inside IF or WHILE still needs confirm.
"""

from __future__ import annotations

import time

import pytest

from fabric_desktop import sql_exec
from fabric_desktop.localdata import LocalData


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Rowcount")["id"])
    e.execute("CREATE TABLE dbo.t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, name)")
    e.execute("CREATE TABLE dbo.dt USING DELTA AS SELECT * FROM (VALUES (1, 10), (2, 20), (3, 30)) v(id, v)",
              dialect="duckdb")
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


ROWCOUNTS = [
    ("SELECT id FROM dbo.t", 3),
    ("SELECT id FROM dbo.t WHERE id > 9", 0),
    ("INSERT INTO dbo.t VALUES (5, 'e'), (6, 'f')", 2),
    ("UPDATE dbo.t SET name = 'z' WHERE id >= 2", 2),
    ("DELETE FROM dbo.t WHERE id = 1", 1),
    ("UPDATE dbo.dt SET v = v + 1 WHERE id < 3", 2),
    ("INSERT INTO dbo.dt VALUES (7, 70)", 1),
    ("DELETE FROM dbo.dt WHERE id = 3", 1),
    ("DECLARE @x INT; SELECT @x = id FROM dbo.t", 3),
    ("DECLARE @x INT; SET @x = 1", 1),
    ("DECLARE @x INT", 0),
    ("IF 1 = 1 SELECT id FROM dbo.t", 3),
    ("PRINT 'x'", 0),
]


@pytest.mark.parametrize("sql, want", ROWCOUNTS)
def test_rowcount_after_a_statement(engine, sql, want):
    assert rows(engine, f"{sql}; SELECT @@ROWCOUNT AS rc") == [[want]]


def test_rowcount_read_into_a_variable(engine):
    assert rows(engine, "DECLARE @n INT; UPDATE dbo.t SET name = 'z' WHERE id < 3; SET @n = @@ROWCOUNT; "
                        "SELECT @n AS n") == [[2]]


def test_rowcount_in_a_select_of_one_row_after_another(engine):
    assert rows(engine, "SELECT 1 AS a; SELECT @@ROWCOUNT AS rc") == [[1]]


def test_print_text_reaches_the_result_in_order(engine):
    result = engine.execute("PRINT 'a'\nDECLARE @n INT = 3\nPRINT 'n is ' + CAST(@n AS VARCHAR(10))\n"
                            "SELECT id FROM dbo.t ORDER BY id\nPRINT N'é'", dialect="tsql")
    assert result["messages"] == ["a", "n is 3", "é"]
    assert [list(r) for r in result["rows"]] == [[1], [2], [3]]


def test_print_converts_and_shows_null_as_empty(engine):
    assert engine.execute("PRINT 1 + 1; PRINT NULL", dialect="tsql")["messages"] == ["2", ""]


def test_print_in_a_loop(engine):
    result = engine.execute("DECLARE @i INT = 0; WHILE @i < 3 BEGIN SET @i += 1; "
                            "PRINT CAST(@i AS VARCHAR(2)); END", dialect="tsql")
    assert result["messages"] == ["1", "2", "3"]


def test_a_query_with_no_script_has_no_messages(engine):
    assert engine.execute("SELECT 1 AS one", dialect="tsql").get("messages", []) == []


def test_execute_reader_carries_the_messages(engine):
    with engine.execute_reader("PRINT 'a'; SELECT id FROM dbo.t ORDER BY id; PRINT 'b'", dialect="tsql") as stream:
        assert stream.read_all().to_pylist() == [{"id": 1}, {"id": 2}, {"id": 3}]
        assert stream.messages == ["a", "b"]


def test_a_managed_local_run_shows_the_messages_in_its_status(tmp_path):
    local = LocalData(tmp_path / "ld")
    pid = local.create("Managed")["id"]
    limits = {"sql_page_rows": 1000, "sql_time_limit_minutes": 10, "sql_memory_limit_percent": 30,
              "sql_max_concurrent": 2, "sql_result_retention_hours": 24, "sql_temp_dir": "",
              "sql_time_limit_by_policy": False}
    m = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", limits=lambda: limits, local_engine=local.engine)
    try:
        run_id = m.start("DECLARE @x INT = 2; PRINT 'x is ' + CAST(@x AS VARCHAR(5)); SELECT @x AS x",
                         target="local", project=pid, dialect="tsql")["id"]
        end = time.monotonic() + 60
        status = m.status(run_id)
        while status["state"] not in sql_exec.FINISHED and time.monotonic() < end:
            time.sleep(0.05)
            status = m.status(run_id)
        assert status["state"] == "completed", status
        assert status["messages"] == ["x is 2"]
    finally:
        local.close_all()


@pytest.mark.parametrize("sql, statement", [
    ("IF 1 = 1 DROP TABLE dbo.t", 2),
    ("DECLARE @x INT = 1; DELETE FROM dbo.t", 2),
    ("DECLARE @i INT = 0\nWHILE @i < 2\nBEGIN\n  SET @i += 1\n  DELETE FROM dbo.t\nEND", 4),
])
def test_the_policy_check_numbers_verdicts_by_the_users_statements(sql, statement):
    verdicts, stop = sql_exec.check_local(sql, "tsql")
    assert stop is not None and stop.confirm and stop.statement == statement, (stop, verdicts)
    _verdicts, after = sql_exec.check_local(sql, "tsql", confirm=True)
    assert after is None


def test_a_script_that_only_reads_needs_no_confirm():
    _verdicts, stop = sql_exec.check_local("DECLARE @x INT = 1; IF @x = 1 SELECT @x AS x", "tsql")
    assert stop is None
