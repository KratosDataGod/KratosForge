"""Procedures, functions and sp_rename in local T-SQL (localdata/tsql_routines.py, WR-224): what mutation
testing (batch 89, docs/quality/mutation-testing.md) showed no test pinned. The token scan that refuses a
routine before its body is read (a name cut at its parenthesis, EXEC or DROP as a batch's last word, a name
after a dot), a call written without EXEC, statement()'s own refusals behind that scan (ALTER as a command, DROP
with its name, EXEC of a string) and a rename that statement() has already made."""

from __future__ import annotations

import pytest
from sqlglot import exp

from fabric_desktop.localdata import LocalData, tsql_routines
from fabric_desktop.localdata.tsql import KfTSQL

_DIALECT = KfTSQL()


def tree(sql: str) -> exp.Expression:
    return _DIALECT.parse(sql)[0]


def scan(sql: str) -> None:
    tsql_routines.refuse_routines(sql, _DIALECT.tokenize(sql))


# ---- the token scan

@pytest.mark.parametrize("sql, named", [
    ("CREATE FUNCTION dbo.f(@x INT) RETURNS INT AS BEGIN RETURN 1 END", "CREATE FUNCTION dbo.f isn't mapped yet"),
    ("DROP PROCEDURE dbo.p;", "DROP PROCEDURE dbo.p isn't mapped yet"),
    ("DROP PROCEDURE IF", "DROP PROCEDURE IF isn't mapped yet"),  # IF with no EXISTS after it is the name
])
def test_the_scan_names_a_routine_as_written_up_to_its_parenthesis(sql, named):
    with pytest.raises(ValueError, match="can't be run on the local engine") as caught:
        scan(sql)
    assert named in str(caught.value)


@pytest.mark.parametrize("sql", ["EXEC ('SELECT 1')", "SELECT 1; EXEC ('SELECT 1')"])
def test_the_scan_refuses_exec_of_a_string_first_in_a_batch_or_later(sql):
    with pytest.raises(ValueError, match="EXEC of a string runs text as SQL"):
        scan(sql)


@pytest.mark.parametrize("sql", [
    "SELECT dbo.exec(1) AS a",  # exec after a dot is a function's name, not EXEC
    "SELECT 1; EXEC",           # EXEC as the last word: nothing after it to read
    "SELECT 1; DROP",           # DROP as the last word
])
def test_the_scan_leaves_what_isnt_a_routine_for_the_parser(sql):
    assert scan(sql) is None


@pytest.mark.parametrize("sql, read", [
    ("refresh_sales", "refresh_sales"),                       # a name alone isn't taken for a call
    ("sp_help", "EXEC sp_help"),                              # a system procedure alone is
    ("sp_rename 'a', 'b'", "EXEC sp_rename 'a', 'b'"),        # and so is a name with a string after it
])
def test_a_call_written_without_exec_gets_it(sql, read):
    assert tsql_routines.bare_call(sql, _DIALECT.tokenize(sql)) == read


# ---- statement(), behind the scan

@pytest.mark.parametrize("sql, named", [
    ("ALTER PROCEDURE dbo.load AS SELECT 2 AS a", "ALTER PROCEDURE dbo.load isn't mapped yet"),
    ("ALTER PROCEDURE", "ALTER PROCEDURE isn't mapped yet"),  # no name after the kind
    ("DROP PROCEDURE dbo.load", "DROP PROCEDURE dbo.load isn't mapped yet"),
    ("DROP FUNCTION IF EXISTS dbo.f", "DROP FUNCTION dbo.f isn't mapped yet"),
])
def test_statement_refuses_alter_and_drop_of_a_routine_by_its_name(sql, named):
    with pytest.raises(ValueError, match="can't be run on the local engine") as caught:
        tsql_routines.statement(tree(sql))
    assert named in str(caught.value)


def test_statement_leaves_other_commands_and_drops_alone():
    create = tree("CREATE TRIGGER dbo.tr ON t AFTER INSERT AS SELECT 1")  # a CREATE command: the scan names it
    assert isinstance(create, exp.Command) and tsql_routines.statement(create) is create
    drop = tree("DROP TABLE t")
    assert tsql_routines.statement(drop) is drop


def test_statement_refuses_exec_of_a_string():
    with pytest.raises(ValueError, match="EXEC of a string runs text as SQL"):
        tsql_routines.statement(tree("EXEC ('SELECT 1')"))


def test_a_rename_statement_made_runs_through_statement_again():
    renamed = tsql_routines.statement(tree("EXEC sp_rename 'dbo.t', 'u'"))
    assert isinstance(renamed, exp.Alter)
    assert tsql_routines.statement(renamed) is renamed  # marked as made here, so not taken for ALTER ... RENAME


# ---- sp_rename through the engine

@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    local = LocalData(tmp_path / "local-data")
    try:
        yield local.engine(local.create("Rename")["id"])
    finally:
        local.close_all()


def test_a_new_table_name_with_its_schema_is_refused_with_the_name_alone(engine):
    engine.execute("CREATE TABLE t AS SELECT 1 AS a")
    with pytest.raises(ValueError) as caught:
        engine.execute("EXEC sp_rename 'dbo.t', 'dbo.u'", dialect="tsql")
    assert "Write 'u'." in str(caught.value)
