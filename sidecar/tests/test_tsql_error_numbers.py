"""WR-234: local T-SQL names an error with SQL Server's number and severity where it's certain, alone or in the
middle of a script, and refuses what reads an error afterwards (@@ERROR, ERROR_NUMBER() ...) by name.

SQL Server prints "Msg 8134, Level 16, State 1, Line 2" and then "Divide by zero error encountered.". The local
engine gives the number and severity for 207, 208, 209, 239 and 8134, and no number where it isn't certain.
TRY...CATCH waits on the T-SQL interpreter, so what reads the error inside it is refused until then.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql_errors


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Numbers")["id"])
    e.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a')) v(id, s)")
    yield e
    data.close_all()


def error(engine, sql):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    return str(caught.value)


@pytest.mark.parametrize("sql, says", [
    ("SELECT 1 / 0", "Msg 8134, Level 16: Divide by zero error encountered."),
    ("SELECT id / 0 FROM t", "Msg 8134, Level 16: Divide by zero error encountered."),
    ("SELECT nope FROM t", "Msg 207, Level 16: Invalid column name 'nope'."),
    ("SELECT * FROM dbo.missing", "Msg 208, Level 16: Invalid object name 'dbo.missing'."),
    ("WITH a AS (SELECT 1 AS x), A AS (SELECT 2 AS x) SELECT x FROM a",
     "Msg 239, Level 16: Duplicate common table expression name 'A' was specified."),
])
def test_an_error_has_sql_servers_number_and_severity(engine, sql, says):
    assert error(engine, sql) == says


def test_in_the_middle_of_a_script_it_names_the_statement_the_number_and_what_ran(engine):
    text = error(engine, "INSERT INTO t VALUES (2, 'b'); SELECT 1 / 0; INSERT INTO t VALUES (3, 'c')")
    assert text == ("Statement 2 of 3 (SELECT) failed: Msg 8134, Level 16: Divide by zero error encountered. "
                    "Statement 1 ran, and what it changed is kept; the last statement didn't run.")
    assert engine.execute("SELECT id FROM t ORDER BY id", dialect="tsql")["rows"] == [[1], [2]]


def test_an_error_in_the_last_statement_as_its_rows_are_read_has_its_number_too(engine):
    # DuckDB 2.0 runs the last query as its result is read, so its error comes from there
    text = error(engine, "SELECT 1; SELECT id / 0 FROM t")
    assert text == ("Statement 2 of 2 (SELECT) failed: Msg 8134, Level 16: Divide by zero error encountered. "
                    "Statement 1 ran, and what it changed is kept."), text


def test_a_duplicate_cte_name_after_another_statement_has_its_number_too(engine):
    text = error(engine, "SELECT 1; WITH a AS (SELECT 1 AS x), A AS (SELECT 2 AS x) SELECT x FROM a")
    assert text.startswith("Msg 239, Level 16: Duplicate common table expression name 'A'"), text


@pytest.mark.parametrize("sql", [
    "SELECT CAST('x' AS INT)",  # 245 names the value and its type, which the local engine words differently
    "DROP TABLE missing",  # 3701, whose text the local engine doesn't give
])
def test_an_error_with_no_certain_number_shows_none(engine, sql):
    assert "Msg " not in error(engine, sql)


@pytest.mark.parametrize("sql, name", [
    ("SELECT @@ERROR", "@@ERROR"),
    ("SELECT @@error", "@@ERROR"),
    ("SELECT ERROR_NUMBER()", "ERROR_NUMBER()"),
    ("SELECT ERROR_MESSAGE()", "ERROR_MESSAGE()"),
    ("SELECT ERROR_SEVERITY()", "ERROR_SEVERITY()"),
    ("SELECT ERROR_STATE()", "ERROR_STATE()"),
    ("SELECT error_line()", "ERROR_LINE()"),
    ("SELECT ERROR_PROCEDURE()", "ERROR_PROCEDURE()"),
    ("SELECT id FROM t WHERE ERROR_NUMBER() = 0", "ERROR_NUMBER()"),
])
def test_what_reads_an_error_afterwards_is_refused_by_name(engine, sql, name):
    text = error(engine, sql)
    assert text.startswith(f"This T-SQL can't be run on the local engine: {name} isn't mapped yet.") and \
        "TRY...CATCH" in text and "Run it in a Fabric Warehouse" in text, text


def test_try_catch_is_still_refused_as_control_flow(engine):
    text = error(engine, "BEGIN TRY SELECT 1 / 0 END TRY BEGIN CATCH SELECT ERROR_NUMBER() END CATCH")
    assert "doesn't run T-SQL BEGIN yet" in text, text


@pytest.mark.parametrize("message, out", [
    ("Invalid column name 'x'.", "Msg 207, Level 16: Invalid column name 'x'."),
    ("Invalid object name 'dbo.x'.", "Msg 208, Level 16: Invalid object name 'dbo.x'."),
    ("Ambiguous column name 'x'.", "Msg 209, Level 16: Ambiguous column name 'x'."),
    ("Divide by zero error encountered.", "Msg 8134, Level 16: Divide by zero error encountered."),
    ("Divide by zero error encountered. And more", "Divide by zero error encountered. And more"),
    ("Binder Error: Referenced table \"x\" not found!", "Binder Error: Referenced table \"x\" not found!"),
    ("The column Invalid column name 'x'.", "The column Invalid column name 'x'."),  # only at the start
])
def test_numbered_adds_a_number_only_where_its_certain(message, out):
    assert tsql_errors.numbered(message) == out
