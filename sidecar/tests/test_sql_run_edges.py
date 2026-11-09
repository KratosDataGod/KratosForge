"""SQL over the lakehouse on this PC (sql.py), the paths the main tests don't reach: the sandboxed run
external AI tools use (one statement, read only, no files), version suffixes it can't read, from_json
schemas it can't use, and statements that return nothing. No lakehouse is attached; nothing reaches Fabric."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql


# ---- the sandboxed run


def test_a_sandboxed_query_that_only_reads_runs():
    assert sql.run("SELECT 1 AS n", sandbox=True).to_pylist() == [{"n": 1}]


@pytest.mark.parametrize("query, error, says", [
    ("SELECT 1; SELECT 2", ValueError, "Send one statement at a time"),
    ("INSERT INTO t VALUES (1)", PermissionError, "Only queries that read"),
    ("CREATE TABLE t AS SELECT 1", PermissionError, "Only queries that read"),
    ("SET enable_external_access = true", PermissionError, "Only queries that read"),
])
def test_a_sandboxed_query_that_would_change_something_is_refused(query, error, says):
    with pytest.raises(error, match=says):
        sql.run(query, sandbox=True)


def test_a_sandboxed_query_cant_read_files_on_this_pc(tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("token\nabc\n")
    with pytest.raises(duckdb.PermissionException, match="file system operations are disabled"):
        sql.run(f"SELECT * FROM read_csv('{secret.as_posix()}')", dialect="duckdb", sandbox=True)
    assert sql.run(f"SELECT * FROM read_csv('{secret.as_posix()}')", dialect="duckdb").num_rows == 1  # unsandboxed it can


def test_a_sandboxed_connection_has_its_limits_set_and_locked():
    con, statement = sql.prepare_sandboxed("SELECT 2 AS n", memory_mb=256, threads=1)
    try:
        assert sql.execute_sandboxed(con, statement).to_pylist() == [{"n": 2}]
        assert con.execute("SELECT current_setting('threads')").fetchone()[0] == 1
        with pytest.raises(duckdb.Error, match="locked"):
            con.execute("SET enable_external_access = true")
    finally:
        con.close()


# ---- what this PC can't read


@pytest.mark.parametrize("query, says", [
    ("SELECT * FROM t@abc", r"t@abc isn't a version \(@v3\) or a time"),
    ("SELECT from_json('{}', 'struct<>')", "from_json's schema has no fields"),
    ("SELECT from_json('{}', '!!!')", "from_json's schema"),
    ("SELECT 'unclosed", "Could not read the query"),
])
def test_queries_this_pc_cant_read_say_why(query, says):
    with pytest.raises(ValueError, match=says):
        sql.run(query)


def test_spark_answers_this_pc_gives_as_spark_does():
    assert sql.run("SELECT format_number(1.5, -1) AS x").to_pylist() == [{"x": None}]  # a negative count is NULL
    assert sql.run("SELECT schema_of_json('{\"a\":1}') AS s").to_pylist() == [{"s": "STRUCT<a: BIGINT>"}]


def test_a_statement_with_no_rows_gives_an_empty_result():
    assert sql.head("SET threads = 2", dialect="duckdb")[1] == 0
    assert sql._arrow(None).num_rows == 0
