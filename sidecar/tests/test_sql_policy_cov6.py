"""What a SQL statement may do (sql_policy.py), the paths the other tests leave: a Spark file read by format and
name, a write whose target isn't a table name, and a session command DuckDB's parser keeps as a command."""

from __future__ import annotations

import sqlglot

from fabric_desktop import sql_policy as P


def _verdict(sql, dialect):
    [verdict] = P.classify(sqlglot.parse(sql, read=dialect))
    return verdict.kind, verdict.reason


def test_a_spark_file_read_by_format_names_the_format():
    statement = sqlglot.parse_one("SELECT * FROM parquet.`sales`", read="spark")
    assert P._reads_files(statement) == "parquet.`...`"


def test_a_write_without_a_table_name_says_a_table():
    assert _verdict("INSERT INTO (SELECT 1) VALUES (1)", "duckdb") == (
        "onelake_write", "INSERT on a table would change the lakehouse")


def test_a_session_command_kept_as_a_command_changes_only_this_run():
    assert _verdict("RESET x", "duckdb") == ("local_mutation", "RESET changes this run's session")


def test_use_changes_only_this_runs_session():
    assert _verdict("USE sales", "spark") == ("local_mutation", "USE changes this run's session")
