"""WR-233: local T-SQL says a name isn't there in SQL Server's own words, as a Fabric Warehouse does.

A warehouse says "Invalid column name 'x'." (207), "Invalid object name 'x'." (208) and "Ambiguous column name
'x'." (209). DuckDB's binder and catalog messages that map to one of those with certainty are rewritten; any
other keeps DuckDB's words, without a suggestion of DuckDB's own system tables. DuckDB's text is logged.
"""

from __future__ import annotations

import logging

import pytest

from fabric_desktop.localdata import LocalData, tsql_errors


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Errors")["id"])
    e.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a')) v(id, s)")
    e.execute("CREATE TABLE u AS SELECT * FROM (VALUES (1, 'b')) v(id, s)")
    yield e
    data.close_all()


def error(engine, sql, dialect="tsql"):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect=dialect)
    return str(caught.value)


@pytest.mark.parametrize("sql, says", [
    ("SELECT nope FROM dbo.t", "Invalid column name 'nope'."),
    ("SELECT [Nope] FROM t", "Invalid column name 'Nope'."),  # as written
    ("SELECT t.nope FROM dbo.t", "Invalid column name 'nope'."),
    ("SELECT id FROM t WHERE nope = 1", "Invalid column name 'nope'."),
    ("UPDATE t SET nope = 1", "Invalid column name 'nope'."),
    ("INSERT INTO t (nope) VALUES (1)", "Invalid column name 'nope'."),
    ("CREATE STATISTICS s ON dbo.t (nope)", "Invalid column name 'nope'."),
    ("SELECT * FROM dbo.missing", "Invalid object name 'dbo.missing'."),
    ("SELECT * FROM [dbo].[missing]", "Invalid object name 'dbo.missing'."),
    ("SELECT * FROM missing", "Invalid object name 'missing'."),
    ("SELECT * FROM nos.missing", "Invalid object name 'nos.missing'."),
    ("INSERT INTO dbo.missing VALUES (1)", "Invalid object name 'dbo.missing'."),
    ("UPDATE STATISTICS dbo.missing", "Invalid object name 'dbo.missing'."),
    ("SELECT id FROM t JOIN u ON t.id = u.id", "Ambiguous column name 'id'."),
])
def test_a_name_that_isnt_there_is_said_as_sql_server_says_it(engine, sql, says):
    assert error(engine, sql) == says


def test_in_a_script_it_names_the_statement_and_what_ran(engine):
    text = error(engine, "SELECT * FROM t; SELECT nope FROM t")
    assert text == "Statement 2 of 2 (SELECT) failed: Invalid column name 'nope'. Statement 1 ran, and what it " \
                   "changed is kept."


def test_duckdbs_own_text_goes_to_the_log(engine, caplog):
    with caplog.at_level(logging.INFO, logger="fabric_desktop.localdata.engine"):
        error(engine, "SELECT * FROM dbo.missing")
    assert any("Table with name missing does not exist" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("sql", ["SELECT nope FROM t WHERE s = '123-45-6789'",
                                 "INSERT INTO missing VALUES ('123-45-6789')"])
def test_a_value_in_the_statement_never_reaches_the_log(engine, caplog, sql):
    with caplog.at_level(logging.INFO, logger="fabric_desktop.localdata.engine"):
        error(engine, sql)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "DuckDB said" in logged and "123-45-6789" not in logged and "LINE" not in logged, logged


@pytest.mark.parametrize("sql", [
    "SELECT x.id FROM dbo.t",  # SQL Server's 4104 names the whole identifier, which DuckDB doesn't give
    "DROP TABLE missing",  # SQL Server's 3701, not 208
])
def test_an_error_with_no_certain_mapping_keeps_duckdbs_words(engine, sql):
    text = error(engine, sql)
    assert text.startswith(("Binder Error", "Catalog Error")) and "Invalid" not in text, text


def test_the_duckdb_dialect_keeps_duckdbs_words(engine):
    assert error(engine, "SELECT nope FROM t", dialect="duckdb").startswith("Binder Error")


@pytest.mark.parametrize("message", [
    'Catalog Error: Scalar Function with name nope does not exist!\nDid you mean "pg_catalog.pg_size_pretty"?',
    'Catalog Error: Table Function with name nope does not exist!\nDid you mean "duckdb_tables"?',
    'Catalog Error: Table with name x does not exist!\nDid you mean "information_schema.tables"?',
])
def test_a_suggestion_of_duckdbs_system_tables_is_dropped(message):
    assert "Did you mean" not in tsql_errors.without_system_hint(message)


def test_a_suggestion_of_the_projects_own_table_is_kept():
    message = 'Catalog Error: Table with name tt does not exist!\nDid you mean "t"?'
    assert tsql_errors.without_system_hint(message) == message


@pytest.mark.parametrize("name, sql, out", [
    ("missing", "SELECT * FROM missing", "missing"),
    ("missing", 'SELECT * FROM "dbo"."missing"', "dbo.missing"),
    ("missing", "SELECT * FROM dbo . missing", "dbo.missing"),
    ("missing", "SELECT * FROM a.missing JOIN b.missing ON 1 = 1", "missing"),  # two schemas: can't tell
    ("missing", "SELECT * FROM dbo.missing_two", "missing"),  # another name
    ("s.missing", "SELECT * FROM s.missing", "s.missing"),
])
def test_the_name_keeps_its_schema_when_the_statement_gives_one(name, sql, out):
    assert tsql_errors.warehouse(f"Catalog Error: Table with name {name} does not exist!", sql, "select") == \
        f"Invalid object name '{out}'."
