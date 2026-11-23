"""Mutation batch 267: localdata/tsql_errors.py, first score (docs/quality/mutation-testing.md)."""

from fabric_desktop.localdata import tsql_errors


def test_divide_by_zero_is_read_from_the_first_line_of_a_longer_message():
    message = "Invalid Input Error: Divide by zero error encountered.\nLINE 1: SELECT 1 / 0\n               ^"
    assert tsql_errors.warehouse(message, "SELECT 1 / 0", "select") == "Divide by zero error encountered."


def test_one_schema_written_two_ways_still_names_the_schema():
    message = "Catalog Error: Table with name t does not exist!"
    for sql in ("SELECT * FROM dbo.t JOIN [dbo].t AS x ON 1 = 1",
                "SELECT * FROM dbo.t JOIN dbo . t AS x ON 1 = 1"):
        assert tsql_errors.warehouse(message, sql, "select") == "Invalid object name 'dbo.t'."
