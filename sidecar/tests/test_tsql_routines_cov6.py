"""Procedures and functions in local T-SQL (localdata/tsql_routines.py), the paths the other tests leave: a parsed
CREATE of a procedure or function refused by name, an ALTER of something that isn't a routine, CREATE PROCEDURE
with no name at the end of a batch, and an INFORMATION_SCHEMA view that lists no catalog."""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from fabric_desktop.localdata import tsql_routines as R


def _tokens(batch):
    return sqlglot.Dialect.get_or_raise("tsql").tokenize(batch)


@pytest.mark.parametrize(("sql", "said"), [
    ("CREATE PROCEDURE p AS SELECT 1", "CREATE PROCEDURE p isn't mapped yet"),
    ("CREATE OR ALTER PROCEDURE dbo.p AS SELECT 1", "CREATE OR ALTER PROCEDURE dbo.p isn't mapped yet"),
    ("CREATE FUNCTION f() RETURNS INT AS BEGIN RETURN 1 END", "CREATE FUNCTION f"),
])
def test_a_parsed_routine_is_refused_by_name(sql, said):
    with pytest.raises(ValueError, match=f"^This T-SQL can't be run on the local engine: {said}"):
        R.statement(sqlglot.parse_one(sql, read="tsql"))


def test_an_alter_of_something_else_runs_as_it_was():
    tree = sqlglot.parse_one("ALTER SOMETHING x", read="tsql")
    assert R.statement(tree) is tree


def test_a_routine_with_no_name_at_the_end_of_a_batch_is_refused():
    with pytest.raises(ValueError, match="^This T-SQL can't be run on the local engine: CREATE PROCEDURE isn't mapped"):
        R.refuse_routines("CREATE PROCEDURE", _tokens("CREATE PROCEDURE"))


def test_an_information_schema_view_without_a_catalog_column_is_left_as_it_is():
    table = exp.to_table("INFORMATION_SCHEMA.KEY_COLUMN_USAGE")
    before = table.sql()
    R.project_rows(table)
    assert table.sql() == before
