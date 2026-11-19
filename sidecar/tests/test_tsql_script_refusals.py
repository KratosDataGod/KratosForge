"""WR-238: a statement of a T-SQL script that the local engine refuses is named by its place in the script.

A script is translated whole before any of it runs, so a refusal of statement 2 of 3 runs nothing. That matches
a Fabric Warehouse for an error it finds when it compiles the batch, such as Msg 239 (two CTEs of one name), which
stops the batch before its first statement. Before WR-238 the message didn't say which statement it was, so in a
long script the user had to find it. A script of one statement keeps the refusal as it was.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql

ENDING = " None of the script ran, because every statement is checked before the first one runs."


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    yield data.engine(data.create("Scripts")["id"])
    data.close_all()


def error(engine, sql):
    with pytest.raises(ValueError) as caught:
        engine.execute(sql, dialect="tsql")
    return str(caught.value)


@pytest.mark.parametrize("sql, framing, inner", [
    ("SELECT 1;\nWITH a AS (SELECT 1 AS x), a AS (SELECT 2 AS x) SELECT x FROM a;\nSELECT 3",
     "Statement 2 of 3 (WITH) failed: ", "Msg 239, Level 16: Duplicate common table expression name 'a' was specified."),
    ("SELECT 1;\nSELECT 2;\nWITH b AS (SELECT 1 AS x), B AS (SELECT 2 AS x) SELECT x FROM b",
     "Statement 3 of 3 (WITH) failed: ", "Msg 239, Level 16: Duplicate common table expression name 'B'"),
    ("WITH a AS (SELECT 1 AS x), a AS (SELECT 2 AS x) SELECT x FROM a;\nSELECT 2",
     "Statement 1 of 2 (WITH) failed: ", "Msg 239"),
    ("SELECT 1;\nSELECT ERROR_NUMBER()", "Statement 2 of 2 (SELECT) failed: ", "ERROR_NUMBER() isn't mapped yet"),
    ("SELECT 1\nGO\nSELECT 2 OPTION (MAXDOP 1)", "Statement 2 of 2 (SELECT) failed: ", "OPTION (MAXDOP)"),
])
def test_a_refused_statement_is_named_by_its_place_and_nothing_ran(engine, sql, framing, inner):
    text = error(engine, sql)
    assert text.startswith(framing) and inner in text and text.endswith(ENDING), text


def test_nothing_before_the_refused_statement_ran(engine):
    text = error(engine, "CREATE TABLE made AS SELECT 1 AS x;\nWITH a AS (SELECT 1 AS x), a AS (SELECT 2 AS x) "
                         "SELECT x FROM a")
    assert text.startswith("Statement 2 of 2 (WITH) failed: Msg 239"), text
    assert engine.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'made'")["rows"] == [[0]]


@pytest.mark.parametrize("sql", ["WITH a AS (SELECT 1 AS x), a AS (SELECT 2 AS x) SELECT x FROM a",
                                 "SET NOCOUNT ON;\nWITH a AS (SELECT 1 AS x), a AS (SELECT 2 AS x) SELECT x FROM a"])
def test_a_script_of_one_statement_keeps_its_refusal_as_it_was(engine, sql):
    """SET NOCOUNT and the other session options are skipped, so they aren't counted."""
    assert error(engine, sql) == "Msg 239, Level 16: Duplicate common table expression name 'a' was specified."


def test_a_statement_that_fails_as_it_runs_keeps_the_running_framing(engine):
    with pytest.raises(Exception) as caught:  # noqa: B017 - DuckDB's own error type, kept as it was
        engine.execute("CREATE TABLE ran AS SELECT 1 AS x;\nSELECT 1/0;\nSELECT 3", dialect="tsql")
    text = str(caught.value)
    assert text.startswith("Statement 2 of 3 (SELECT) failed: Msg 8134") and "Statement 1 ran" in text, text
    assert ENDING not in text


def test_the_translator_says_where_for_other_callers():
    with pytest.raises(tsql.InScript) as caught:
        tsql.translate("SELECT 1;\nSELECT 2;\nSELECT 3 OPTION (MAXDOP 1)")
    assert (caught.value.index, caught.value.count, caught.value.kind) == (2, 3, "SELECT")
    assert str(caught.value).startswith("This T-SQL can't be run on the local engine: OPTION (MAXDOP)")


def test_a_refusal_is_still_a_value_error_for_every_caller():
    assert issubclass(tsql.InScript, ValueError)
