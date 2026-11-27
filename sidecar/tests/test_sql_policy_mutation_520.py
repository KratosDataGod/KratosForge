"""sql_policy.py (mutation batch 520): a statement on the local target that removes a table or every row is
refused unless the run says it was confirmed; leaving ``confirmed`` out means it wasn't."""

from __future__ import annotations

import pytest

from fabric_desktop import sql, sql_policy

ALLOW = ("read_only", "local_mutation", "local_project_write")


@pytest.mark.parametrize("query", ["DROP TABLE sales", "TRUNCATE sales", "DELETE FROM sales"])
def test_a_statement_that_needs_confirming_is_refused_by_default(query):
    verdicts = sql_policy.classify(sql.parse(query, "duckdb"), "local")
    assert [(v.kind, v.confirm) for v in verdicts] == [("local_project_write", True)]
    assert sql_policy.refused(verdicts, ALLOW) is verdicts[0]
    assert sql_policy.refused(verdicts, ALLOW, confirmed=False) is verdicts[0]
    assert sql_policy.refused(verdicts, ALLOW, confirmed=True) is None


def test_a_change_that_needs_no_confirming_runs_without_it():
    verdicts = sql_policy.classify(sql.parse("INSERT INTO sales VALUES (1)", "duckdb"), "local")
    assert [(v.kind, v.confirm) for v in verdicts] == [("local_project_write", False)]
    assert sql_policy.refused(verdicts, ALLOW) is None


@pytest.mark.parametrize("query, reason", [
    ("DROP TABLE sales", "DROP TABLE sales removes it from the project"),
    ("TRUNCATE sales", "TRUNCATE sales deletes every row"),
    ("DELETE FROM sales", "DELETE on sales has no WHERE, so it changes every row"),
    ("UPDATE sales SET n = 1 WHERE n = 0", "UPDATE on sales changes the project's own table"),
])
def test_a_local_change_names_its_table(query, reason):
    [verdict] = sql_policy.classify(sql.parse(query, "duckdb"), "local")
    assert verdict.reason == reason


@pytest.mark.parametrize("dialect", ["duckdb", "spark"])
def test_use_changes_only_the_runs_session(dialect):
    [verdict] = sql_policy.classify(sql.parse("USE sales_db", dialect))
    assert (verdict.kind, verdict.reason) == ("local_mutation", "USE changes this run's session")


def test_a_verdict_cant_be_changed_once_made():
    [verdict] = sql_policy.classify(sql.parse("DROP TABLE sales", "duckdb"), "local")
    with pytest.raises(AttributeError):
        verdict.confirm = False  # type: ignore[misc]
    assert verdict.confirm is True


@pytest.mark.parametrize("query, reason", [
    ("VACUUM", "VACUUM isn't run here"),
    ("SET threads = 2", "changes the project database's settings, which a query can't do"),
    ("LOAD httpfs", "changes the project database's settings, which a query can't do"),
    ("PRAGMA version", "changes the project database's settings, which a query can't do"),
])
def test_on_the_local_target_only_settings_commands_are_called_settings(query, reason):
    [verdict] = sql_policy.classify(sql.parse(query, "duckdb"), "local")
    assert (verdict.kind, verdict.reason) == ("fabric_mutation", reason)


@pytest.mark.parametrize("query, dialect", [("RESET threads", "duckdb"), ("RESET spark.sql.shuffle.partitions", "spark")])
def test_reset_changes_only_the_runs_session(query, dialect):
    [verdict] = sql_policy.classify(sql.parse(query, dialect))
    assert (verdict.kind, verdict.reason) == ("local_mutation", "RESET changes this run's session")


@pytest.mark.parametrize("dialect", ["duckdb", "spark"])
def test_a_table_in_a_schema_is_the_projects_own_on_the_local_target(dialect):
    [verdict] = sql_policy.classify(sql.parse("SELECT * FROM main.sales", dialect), "local")
    assert (verdict.kind, verdict.reason) == ("read_only", "reads tables")


@pytest.mark.parametrize("fmt", ["parquet", "json", "delta"])
def test_sparks_format_dot_path_reads_a_file_on_the_local_target(fmt):
    [verdict] = sql_policy.classify(sql.parse(f"SELECT * FROM {fmt}.`data`", "spark"), "local")
    assert verdict.kind == "fabric_mutation"
    assert verdict.reason.startswith(f"{fmt}.`...` reads a file on this PC outside the project")


@pytest.mark.parametrize("query, reason", [
    ("DROP VIEW totals", "DROP VIEW totals removes it from the project"),
    ("DROP TABLE sales", "DROP TABLE sales removes it from the project"),
])
def test_a_local_drop_names_what_it_removes(query, reason):
    [verdict] = sql_policy.classify(sql.parse(query, "duckdb"), "local")
    assert verdict.reason == reason


def test_a_create_of_another_kind_is_named_by_its_kind():
    [verdict] = sql_policy.classify(sql.parse("CREATE INDEX i ON sales (a)", "duckdb"))
    assert (verdict.kind, verdict.reason) == ("fabric_mutation", "CREATE INDEX isn't run here")


def test_a_quoted_table_name_is_a_table_and_a_quoted_path_is_a_file():
    [table] = sql_policy.classify(sql.parse('SELECT * FROM "sales"', "duckdb"), "local")
    assert (table.kind, table.reason) == ("read_only", "reads tables")
    [file] = sql_policy.classify(sql.parse("SELECT * FROM 'data/x.csv'", "duckdb"), "local")
    assert file.kind == "fabric_mutation" and file.reason.startswith("'data/x.csv' reads a file on this PC")


@pytest.mark.parametrize("query, verdict", [
    ("INSERT INTO sales VALUES (1)", ("onelake_write", "INSERT on sales would change the lakehouse")),
    ("CREATE TYPE mood AS ENUM ('a')", ("fabric_mutation", "CREATE isn't run here")),
])
def test_on_the_lakehouse_a_refusal_names_the_table_or_the_command(query, verdict):
    [v] = sql_policy.classify(sql.parse(query, "duckdb"))
    assert (v.kind, v.reason) == verdict
