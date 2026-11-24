"""WR-705: local T-SQL errors in a script: TRY...CATCH, the ERROR_* functions, THROW and RAISERROR, and what is
still refused by name.

These tests cover:
- TRY...CATCH, nested, with an empty CATCH, and ERROR_NUMBER, ERROR_MESSAGE, ERROR_SEVERITY, ERROR_STATE,
  ERROR_LINE and ERROR_PROCEDURE in it (NULL outside a CATCH, as in T-SQL);
- THROW with its number, message and state, THROW with a number below 50000 (Msg 35100), and THROW; to rethrow
  (Msg 10704 outside a CATCH);
- RAISERROR with printf-style arguments, severity 10 or less as a message, severity over 18 (Msg 2754), and an
  argument of the wrong type (Msg 2786);
- errors a warehouse doesn't let a CATCH catch in the same batch: a table or column that isn't there;
- cursors, transactions, EXEC, procedures, GOTO, labels, WAITFOR, table variables and sp_executesql, still refused
  by name;
- translate(), which reads each statement on its own and says so.
"""

from __future__ import annotations

import re

import pytest

from fabric_desktop.localdata import LocalData, tsql


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Errors")["id"])
    e.execute("CREATE TABLE dbo.t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, name)")
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def messages(engine, sql):
    return engine.execute(sql, dialect="tsql")["messages"]


def refused(engine, sql, words):
    with pytest.raises(ValueError, match=re.escape(words)):
        engine.execute(sql, dialect="tsql")


def test_catch_reads_the_error(engine):
    sql = ("BEGIN TRY\n  SELECT 1 / 0\nEND TRY\nBEGIN CATCH\n  SELECT ERROR_NUMBER() AS n, ERROR_MESSAGE() AS m, "
           "ERROR_SEVERITY() AS s, ERROR_STATE() AS st, ERROR_LINE() AS l, ERROR_PROCEDURE() AS p\nEND CATCH")
    assert rows(engine, sql) == [[8134, "Divide by zero error encountered.", 16, 1, 2, None]]


def test_error_functions_outside_a_catch_are_null(engine):
    assert rows(engine, "SELECT ERROR_NUMBER() AS n, ERROR_MESSAGE() AS m") == [[None, None]]


def test_an_empty_catch_swallows_the_error(engine):
    assert rows(engine, "BEGIN TRY SELECT 1 / 0 END TRY BEGIN CATCH END CATCH; SELECT 2 AS two") == [[2]]


def test_try_without_an_error_skips_the_catch(engine):
    assert messages(engine, "BEGIN TRY PRINT 'ok' END TRY BEGIN CATCH PRINT 'caught' END CATCH") == ["ok"]


def test_the_statements_after_the_error_in_try_dont_run(engine):
    assert messages(engine, "BEGIN TRY\n  PRINT 'a'\n  SELECT 1 / 0\n  PRINT 'b'\nEND TRY\n"
                            "BEGIN CATCH\n  PRINT 'caught'\nEND CATCH") == ["a", "caught"]


def test_a_conversion_error_is_caught_with_its_number(engine):
    assert rows(engine, "BEGIN TRY DECLARE @x INT = 'abc' END TRY BEGIN CATCH SELECT ERROR_NUMBER() AS n END CATCH") \
        == [[245]]


def test_throw_is_caught_with_its_number_message_and_state(engine):
    assert rows(engine, "BEGIN TRY THROW 50001, 'Stop here', 3; END TRY BEGIN CATCH "
                        "SELECT ERROR_NUMBER() AS n, ERROR_MESSAGE() AS m, ERROR_STATE() AS s, "
                        "ERROR_SEVERITY() AS v END CATCH") == [[50001, "Stop here", 3, 16]]


def test_throw_uncaught_stops_the_script(engine):
    refused(engine, "THROW 50001, 'Stop', 1", "Msg 50001, Level 16: Stop")


def test_throw_with_a_variable_message(engine):
    refused(engine, "DECLARE @m NVARCHAR(100) = N'Row ' + CAST(7 AS NVARCHAR(10)) + N' is bad'; THROW 50002, @m, 1",
            "Msg 50002, Level 16: Row 7 is bad")


def test_throw_below_50000_is_msg_35100(engine):
    refused(engine, "THROW 100, 'x', 1", "Msg 35100, Level 16:")


def test_rethrow_outside_a_catch_is_msg_10704(engine):
    refused(engine, "THROW", "Msg 10704, Level 15: To rethrow an error, a THROW statement must be used inside a "
                             "CATCH block.")


def test_rethrow_reaches_the_outer_catch_with_the_first_error(engine):
    sql = ("BEGIN TRY\n  BEGIN TRY\n    SELECT 1 / 0\n  END TRY\n  BEGIN CATCH\n    THROW;\n  END CATCH\nEND TRY\n"
           "BEGIN CATCH\n  SELECT ERROR_NUMBER() AS n, ERROR_LINE() AS l\nEND CATCH")
    assert rows(engine, sql) == [[8134, 3]]


def test_rethrow_uncaught_stops_with_the_first_error(engine):
    refused(engine, "BEGIN TRY SELECT 1 / 0 END TRY BEGIN CATCH THROW; END CATCH",
            "Msg 8134, Level 16: Divide by zero error encountered.")


def test_raiserror_formats_its_arguments(engine):
    assert messages(engine, "RAISERROR('%s has %d rows', 10, 1, 'dbo.t', 3)") == ["dbo.t has 3 rows"]


def test_raiserror_at_severity_10_or_less_is_a_message_and_the_script_goes_on(engine):
    result = engine.execute("RAISERROR('note', 0, 1); SELECT 1 AS one", dialect="tsql")
    assert result["messages"] == ["note"] and result["rows"] == [[1]]


def test_raiserror_at_severity_16_is_an_error_with_number_50000(engine):
    # outside TRY the script goes on, as a warehouse's batch does (tests/test_tsql_interpreter_raiserror.py)
    assert messages(engine, "RAISERROR('bad %s', 16, 2, 'thing')") == ["Msg 50000, Level 16, State 2, Line 1\nbad thing"]


def test_raiserror_is_caught(engine):
    assert rows(engine, "BEGIN TRY RAISERROR('bad', 16, 4) END TRY BEGIN CATCH "
                        "SELECT ERROR_NUMBER() AS n, ERROR_MESSAGE() AS m, ERROR_SEVERITY() AS s, "
                        "ERROR_STATE() AS st END CATCH") == [[50000, "bad", 16, 4]]


def test_raiserror_over_severity_18_is_msg_2754(engine):
    refused(engine, "RAISERROR('x', 20, 1)", "Msg 2754, Level 16: Error severity levels greater than 18 can only be "
                                             "specified by members of the sysadmin role, using the WITH LOG option.")


def test_raiserror_with_an_argument_of_the_wrong_type_is_msg_2786(engine):
    refused(engine, "RAISERROR('%d', 10, 1, 'x')", "Msg 2786, Level 16: The data type of substitution parameter 1 "
                                                   "does not match the expected type of the format specification.")


def test_raiserror_with_a_message_number_is_refused_by_name(engine):
    refused(engine, "RAISERROR(50001, 16, 1)", "doesn't run T-SQL RAISERROR with a message number")


def test_a_missing_table_isnt_caught_in_the_same_batch(engine):
    """A warehouse resolves names when it compiles: Msg 208 in a TRY ends the batch, its CATCH doesn't run."""
    with pytest.raises(ValueError, match=re.escape("Msg 208, Level 16: Invalid object name 'dbo.nope'.")):
        engine.execute("BEGIN TRY SELECT * FROM dbo.nope END TRY BEGIN CATCH PRINT 'caught' END CATCH",
                       dialect="tsql")


# (script, the words it is refused with)
STILL_REFUSED = [
    ("DECLARE c CURSOR FOR SELECT 1", "doesn't run T-SQL cursors (DECLARE ... CURSOR) yet"),
    ("FETCH NEXT FROM c", "doesn't run T-SQL FETCH yet"),
    ("BEGIN TRAN; SELECT 1", "doesn't run T-SQL transactions (BEGIN TRAN) yet"),
    ("COMMIT", "doesn't run T-SQL transactions (COMMIT) yet"),
    ("SAVE TRAN s", "doesn't run T-SQL transactions (SAVE TRANSACTION) yet"),
    ("SET TRANSACTION ISOLATION LEVEL SNAPSHOT", "doesn't run T-SQL SET TRANSACTION yet"),
    ("EXEC dbo.p", "EXEC dbo.p isn't mapped yet"),
    ("DECLARE @x INT = 1; EXEC dbo.p @x", "EXEC dbo.p isn't mapped yet"),
    ("CREATE PROCEDURE dbo.p AS BEGIN SELECT 1 END", "CREATE PROCEDURE dbo.p isn't mapped yet"),
    ("GOTO x", "doesn't run T-SQL GOTO yet"),
    ("x: PRINT 1", "doesn't run T-SQL labels (x:) yet"),
    ("WAITFOR DELAY '00:00:01'", "doesn't run T-SQL WAITFOR yet"),
    ("DECLARE @t TABLE (a INT)", "doesn't run T-SQL table variables (DECLARE @t TABLE) yet"),
    ("EXEC sp_executesql N'SELECT 1'", "sp_executesql isn't mapped yet"),
    ("SELECT @@ERROR", "@@ERROR isn't mapped yet"),
]


@pytest.mark.parametrize("sql, words", STILL_REFUSED)
def test_still_refused_by_name(engine, sql, words):
    refused(engine, sql, words)


def test_a_refusal_in_a_script_runs_nothing_before_it(engine):
    with pytest.raises(ValueError, match="None of the script ran"):
        engine.execute("DECLARE @i INT = 1; UPDATE dbo.t SET name = 'z' WHERE id = @i; BEGIN TRAN", dialect="tsql")
    assert rows(engine, "SELECT name FROM dbo.t WHERE id = 1") == [["a"]]


@pytest.mark.parametrize("sql, name", [
    ("DECLARE @x INT", "variables (DECLARE)"),
    ("SELECT @x", "variables (@x)"),
    ("PRINT 1", "PRINT"),
    ("RETURN", "RETURN"),
    ("THROW 50001, 'a', 1", "THROW"),
    ("IF 1 = 1 SELECT 1", "IF blocks"),
    ("WHILE 1 = 1 SELECT 1", "WHILE loops"),
    ("BEGIN TRY SELECT 1 END TRY BEGIN CATCH SELECT 2 END CATCH", "BEGIN ... END blocks"),
])
def test_translate_reads_each_statement_on_its_own_and_says_so(sql, name):
    with pytest.raises(ValueError) as caught:
        tsql.translate(sql)
    said = str(caught.value)
    assert f"doesn't run T-SQL {name} in a statement read on its own." in said, said
    assert "local_query, or sql_start with target local" in said, said
