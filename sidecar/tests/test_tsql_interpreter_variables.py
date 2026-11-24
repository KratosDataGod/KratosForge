"""WR-705: local T-SQL variables, as a Fabric Warehouse keeps them.

These tests cover:
- DECLARE, with and without a value, several to a statement, and the NULL a variable starts as;
- SET, with the compound operators (+=, -=, *=, /=, %=, &=, |=, ^=);
- SELECT @x = ..., with and without FROM, and the last row's value it keeps;
- variables read in WHERE, TOP, INSERT VALUES and expressions;
- T-SQL's conversion on assignment: an INT keeps the whole part, a VARCHAR(n) is cut to n, a VARCHAR with no
  length holds 1 character, a DECIMAL is rounded to its scale, a BIT is 1 for any non-zero number;
- batch scope: Msg 137 for a variable not declared in the batch, Msg 134 for one declared twice, names
  compared without case, and GO starting a new batch.
"""

from __future__ import annotations

import re

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Variables")["id"])
    e.execute("CREATE TABLE dbo.t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, name)")
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def refused(engine, sql, words):
    with pytest.raises(ValueError, match=re.escape(words)):
        engine.execute(sql, dialect="tsql")


def test_declare_with_a_value_and_without_one(engine):
    assert rows(engine, "DECLARE @x INT = 5, @y INT; SELECT @x AS x, @y AS y") == [[5, None]]


def test_declare_on_its_own_returns_no_rows(engine):
    result = engine.execute("DECLARE @x INT = 1", dialect="tsql")
    assert result["rows"] == [] and result["columns"] == []


def test_set_replaces_the_value(engine):
    assert rows(engine, "DECLARE @x INT = 1; SET @x = @x + 41; SELECT @x AS x") == [[42]]


@pytest.mark.parametrize("op, want", [("+=", 13), ("-=", 7), ("*=", 30), ("/=", 3), ("%=", 1),
                                      ("&=", 2), ("|=", 11), ("^=", 9)])
def test_compound_set(engine, op, want):
    assert rows(engine, f"DECLARE @x INT = 10; SET @x {op} 3; SELECT @x AS x") == [[want]]


def test_compound_set_on_text_appends(engine):
    assert rows(engine, "DECLARE @s VARCHAR(10) = 'ab'; SET @s += 'cd'; SELECT @s AS s") == [["abcd"]]


def test_select_assigns_each_variable_in_turn(engine):
    assert rows(engine, "DECLARE @a INT, @b INT; SELECT @a = 1, @b = @a + 1; SELECT @a AS a, @b AS b") == [[1, 2]]


def test_select_from_rows_keeps_the_last_rows_value(engine):
    assert rows(engine, "DECLARE @x INT; SELECT @x = id FROM dbo.t ORDER BY id; SELECT @x AS x") == [[3]]


def test_select_from_no_rows_keeps_the_old_value(engine):
    assert rows(engine, "DECLARE @x INT = 7; SELECT @x = id FROM dbo.t WHERE id > 100; SELECT @x AS x") == [[7]]


def test_select_of_an_aggregate(engine):
    assert rows(engine, "DECLARE @n INT; SELECT @n = COUNT(*) FROM dbo.t; SELECT @n AS n") == [[3]]


def test_a_select_over_rows_that_reads_what_it_assigns_is_refused_by_name(engine):
    refused(engine, "DECLARE @s VARCHAR(100) = ''; SELECT @s = @s + name FROM dbo.t",
            "doesn't run T-SQL row-by-row variable assignment")


def test_declare_from_a_subquery_of_more_than_one_row_is_msg_512(engine):
    refused(engine, "DECLARE @x INT = (SELECT id FROM dbo.t)", "Msg 512, Level 16: Subquery returned more than 1 value")


def test_declare_from_a_subquery_of_one_row(engine):
    assert rows(engine, "DECLARE @x VARCHAR(5) = (SELECT name FROM dbo.t WHERE id = 2); SELECT @x AS x") == [["b"]]


def test_variables_in_where_top_and_insert(engine):
    assert rows(engine, "DECLARE @x VARCHAR(10) = 'b'; SELECT id FROM dbo.t WHERE name = @x") == [[2]]
    assert rows(engine, "DECLARE @n INT = 2; SELECT TOP (@n) id FROM dbo.t ORDER BY id") == [[1], [2]]
    assert rows(engine, "DECLARE @k INT = 9; INSERT INTO dbo.t VALUES (@k, 'z'); "
                        "SELECT name FROM dbo.t WHERE id = @k") == [["z"]]


def test_a_value_with_a_quote_is_kept_whole(engine):
    assert rows(engine, "DECLARE @s NVARCHAR(20) = N'it''s ☃'; SELECT @s AS s") == [["it's ☃"]]


def test_a_variable_never_becomes_sql_text(engine):
    """A value goes into the next statement as a literal, so a value that looks like SQL stays a value."""
    assert rows(engine, "DECLARE @s VARCHAR(50) = 'x'' OR 1 = 1 --'; "
                        "SELECT COUNT(*) AS n FROM dbo.t WHERE name = @s") == [[0]]


# (declared type, value, what the variable holds)
CONVERSIONS = [
    ("INT", "2.7", 2),
    ("INT", "-2.7", -2),
    ("BIGINT", "9000000000", 9000000000),
    ("VARCHAR(3)", "'abcdef'", "abc"),
    ("VARCHAR", "'xyz'", "x"),
    ("NVARCHAR(4)", "N'héllo'", "héll"),
    ("CHAR(3)", "'a'", "a  "),
    ("DECIMAL(10, 2)", "1.235", "1.24"),
    ("DECIMAL(10, 4)", "1.23456", "1.2346"),
    ("BIT", "5", True),
    ("BIT", "0", False),
    ("FLOAT", "0.1E0 + 0.2E0", 0.30000000000000004),
    ("DATE", "'2024-02-29'", "2024-02-29"),
    ("UNIQUEIDENTIFIER", "'6F9619FF-8B86-D011-B42D-00C04FC964FF'", "6f9619ff-8b86-d011-b42d-00c04fc964ff"),
]


@pytest.mark.parametrize("to, value, want", CONVERSIONS)
def test_assignment_converts_to_the_declared_type(engine, to, value, want):
    got = rows(engine, f"DECLARE @v {to} = {value}; SELECT @v AS v")[0][0]
    assert str(got) == str(want)


def test_set_converts_as_declare_does(engine):
    assert rows(engine, "DECLARE @x INT; SET @x = 9.99; SELECT @x AS x") == [[9]]
    assert rows(engine, "DECLARE @s VARCHAR(2); SET @s = 'abc'; SELECT @s AS s") == [["ab"]]


def test_a_text_that_isnt_a_number_is_msg_245(engine):
    refused(engine, "DECLARE @x INT = 'x'",
            "Msg 245, Level 16: Conversion failed when converting the varchar value 'x' to data type int.")


def test_int_overflow_in_set_is_msg_8115(engine):
    refused(engine, "DECLARE @x INT = 2147483647; SET @x = @x + 1",
            "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type int.")


def test_an_undeclared_variable_is_msg_137(engine):
    refused(engine, "SET @x = 1", 'Msg 137, Level 15: Must declare the scalar variable "@x".')
    refused(engine, "SELECT @x", 'Msg 137, Level 15: Must declare the scalar variable "@x".')


def test_a_variable_declared_twice_is_msg_134_whatever_its_case(engine):
    refused(engine, "DECLARE @x INT; DECLARE @X INT", "Msg 134, Level 15: The variable name '@X' has already been "
                                                       "declared.")


def test_variable_names_are_compared_without_case(engine):
    assert rows(engine, "DECLARE @Total INT = 4; SELECT @TOTAL + @total AS n") == [[8]]


def test_a_variable_must_be_declared_before_the_statement_that_reads_it(engine):
    refused(engine, "SET @x = 1; DECLARE @x INT", 'Must declare the scalar variable "@x"')


def test_go_starts_a_new_batch_and_its_variables(engine):
    refused(engine, "DECLARE @x INT = 1\nGO\nSELECT @x", 'Msg 137, Level 15: Must declare the scalar variable "@x".')
    assert rows(engine, "DECLARE @x INT = 1\nGO\nDECLARE @x INT = 2\nSELECT @x AS x") == [[2]]


def test_nothing_runs_when_a_later_statement_wont_compile(engine):
    with pytest.raises(ValueError, match="None of the script ran"):
        engine.execute("DECLARE @x INT = 1; INSERT INTO dbo.t VALUES (8, 'q'); SELECT @nope", dialect="tsql")
    assert rows(engine, "SELECT COUNT(*) AS n FROM dbo.t") == [[3]]


def test_text_types_are_msg_2739(engine):
    refused(engine, "DECLARE @x TEXT", "Msg 2739, Level 16: The text, ntext, and image data types are invalid for "
                                       "local variables.")


def test_binary_variables_are_refused_by_name(engine):
    refused(engine, "DECLARE @v VARBINARY(4) = 0x01", "doesn't run T-SQL binary variables (@v VARBINARY(4)) yet")


def test_table_variables_are_refused_by_name(engine):
    refused(engine, "DECLARE @t TABLE (a INT)", "doesn't run T-SQL table variables (DECLARE @t TABLE) yet")


def test_order_by_a_variable_is_msg_1008(engine):
    refused(engine, "DECLARE @x INT = 1; SELECT id FROM dbo.t ORDER BY @x", "Msg 1008, Level 15:")


def test_a_table_made_in_the_script_is_read_by_later_statements(engine):
    assert rows(engine, "DROP TABLE IF EXISTS dbo.made; CREATE TABLE dbo.made (a INT); DECLARE @k INT = 3; "
                        "INSERT INTO dbo.made VALUES (@k); SELECT a FROM dbo.made") == [[3]]


def test_text_added_inside_a_cast_to_a_sized_text_is_joined(engine):
    """``CAST(a + b AS VARCHAR(n))`` with text on both sides joins them, as SET @s = @s + 'x' needs: the cast's
    rewrite used to copy what it held, so + inside it stayed a DuckDB addition (a binder error)."""
    assert rows(engine, "SELECT CAST(name + 'x' AS VARCHAR(10)) AS v FROM dbo.t WHERE id = 1") == [["ax"]]
    assert rows(engine, "SELECT CAST(CAST('ab' AS VARCHAR(10)) + 'c' AS CHAR(4)) AS v") == [["abc "]]
    assert rows(engine, "DECLARE @s VARCHAR(10) = 'ab'; SET @s = @s + 'cd'; SELECT @s AS s") == [["abcd"]]
