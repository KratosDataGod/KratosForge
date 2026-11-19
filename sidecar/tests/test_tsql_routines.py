"""WR-224: procedures, functions and system procedures in local T-SQL. Each runs as a Fabric Warehouse documents it
or is refused by its own name, and none runs as a no-op. Procedures and user-defined functions take parameters,
which are variables, so they wait on the local engine's support for variables and control flow; sp_rename renames
a table or a column; dynamic SQL never runs; INFORMATION_SCHEMA lists the project's own objects.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate

WAITS = "take parameters and use control flow, which the local engine doesn't run yet"


@pytest.mark.parametrize("sql, words", [
    ("CREATE PROCEDURE dbo.load AS SELECT 1 AS a", "CREATE PROCEDURE dbo.load isn't mapped yet. Procedures"),
    ("CREATE PROC dbo.load @day DATE AS BEGIN SET NOCOUNT ON; SELECT @day END", "CREATE PROCEDURE dbo.load"),
    ("CREATE OR ALTER PROCEDURE dbo.load AS SELECT 1 AS a", "CREATE OR ALTER PROCEDURE dbo.load"),
    ("ALTER PROCEDURE dbo.load AS SELECT 2 AS a", "ALTER PROCEDURE dbo.load"),
    ("DROP PROCEDURE dbo.load", "DROP PROCEDURE dbo.load"),
    ("DROP PROC IF EXISTS dbo.load", "DROP PROCEDURE dbo.load"),
    (("CREATE PROCEDURE [dbo].[load] @n INT AS\nBEGIN\n  DECLARE @i INT = 0;\n  WHILE @i < @n SET @i += 1;\n"
      "  IF @i > 2 THROW 50000, 'x', 1;\nEND"), "CREATE PROCEDURE [dbo].[load]"),
    ("CREATE FUNCTION dbo.plus_one (@x INT) RETURNS INT AS BEGIN RETURN @x + 1 END",
     "CREATE FUNCTION dbo.plus_one isn't mapped yet. Procedures"),
    ("CREATE FUNCTION dbo.orders_on (@d DATE) RETURNS TABLE AS RETURN (SELECT 1 AS id)", "CREATE FUNCTION dbo.orders_on"),
    ("CREATE OR ALTER FUNCTION dbo.f () RETURNS INT AS BEGIN RETURN 1 END", "CREATE OR ALTER FUNCTION dbo.f"),
    ("ALTER FUNCTION dbo.f () RETURNS INT AS BEGIN RETURN 2 END", "ALTER FUNCTION dbo.f"),
    ("DROP FUNCTION dbo.f", "DROP FUNCTION dbo.f"),
    ("DROP FUNCTION IF EXISTS dbo.f", "DROP FUNCTION dbo.f"),
    ("SELECT 1 AS a;\nCREATE PROCEDURE dbo.p AS SELECT 1 AS a", "CREATE PROCEDURE dbo.p"),
    ("EXEC dbo.load", "EXEC dbo.load isn't mapped yet. Procedures"),
    ("EXECUTE dbo.load @day = '2026-11-01'", "EXEC dbo.load"),
    ("EXEC load 1, 'a'", "EXEC load"),
    ("SELECT 1 AS a; EXEC dbo.load", "EXEC dbo.load"),
    ("dbo.load '2026-11-01'", "EXEC dbo.load"),  # a batch's first statement needs no EXEC in T-SQL
    ("SELECT dbo.plus_one(1) AS a", "dbo.plus_one is a user-defined function, which isn't mapped yet"),
    ("SELECT id FROM dbo.orders_on('2026-11-01') AS o", "dbo.orders_on is a user-defined function"),
    ("SELECT o.id FROM dbo.t AS o CROSS APPLY dbo.lines(o.id) AS l", "dbo.lines is a user-defined function"),
])
def test_procedures_and_functions_are_refused_by_name_and_wait_on_variables(sql, words):
    with pytest.raises(ValueError) as err:
        translate(sql)
    message = str(err.value)
    assert words in message and WAITS in message and "Fabric Warehouse" in message
    assert "Could not read" not in message


@pytest.mark.parametrize("sql, words", [
    ("EXEC sp_executesql N'SELECT 1'", "sp_executesql isn't mapped yet. It runs text as SQL"),
    ("EXEC sp_executesql N'SELECT * FROM dbo.t WHERE id = @id', N'@id INT', @id = 1", "sp_executesql"),
    ("EXECUTE sys.sp_executesql N'SELECT 1'", "sp_executesql"),
    ("sp_executesql N'SELECT 1'", "sp_executesql"),
    ("EXEC ('SELECT 1')", "EXEC of a string runs text as SQL"),
    ("EXEC (N'DROP TABLE dbo.t')", "EXEC of a string"),
    ("EXEC ('SELECT ' + '1')", "EXEC of a string"),
])
def test_dynamic_sql_never_runs(sql, words):
    with pytest.raises(ValueError) as err:
        translate(sql)
    assert words in str(err.value) and "dynamic SQL" in str(err.value)


@pytest.mark.parametrize("procedure", ["sp_help 'dbo.t'", "sp_helptext 'dbo.v'", "sp_columns 't'", "sp_tables",
                                       "sp_spaceused 'dbo.t'", "sp_who", "sys.sp_help 'dbo.t'", "xp_cmdshell 'dir'"])
@pytest.mark.parametrize("call", ["EXEC {p}", "EXECUTE {p}", "{p}"])
def test_other_system_procedures_are_refused_by_name(procedure, call):
    with pytest.raises(ValueError) as err:
        translate(call.format(p=procedure))
    name = procedure.split()[0].split(".")[-1]
    assert f"the system procedure {name} isn't mapped yet" in str(err.value)
    assert "INFORMATION_SCHEMA.COLUMNS" in str(err.value)


def test_a_trigger_is_refused_as_a_warehouse_has_none():
    with pytest.raises(ValueError, match="a Fabric Warehouse has no triggers either"):
        translate("CREATE TRIGGER dbo.tr ON dbo.t AFTER INSERT AS SELECT 1")


@pytest.mark.parametrize("sql", ["SELECT 'EXEC dbo.p' AS a, 'CREATE PROCEDURE x' AS b",
                                 "-- EXEC sp_executesql N'x'\nSELECT 1 AS a", "SELECT [exec], [procedure] FROM dbo.t",
                                 "SELECT DATEADD(day, 1, '2026-01-01') AS a", "SELECT o.id FROM dbo.t AS o"])
def test_the_words_as_text_comments_names_or_built_ins_are_left_alone(sql):
    translate(sql)


@pytest.mark.parametrize("sql", ["SELECT x.value('a', 'int') AS v FROM dbo.d x",
                                 "SELECT d.value('a', 'int') AS v FROM dbo.d"])
def test_a_method_on_a_tables_column_isnt_called_a_user_defined_function(sql):
    with pytest.raises(ValueError, match="VALUE isn't mapped yet") as err:
        translate(sql, known=set())  # as the engine runs it: a function DuckDB doesn't have is refused by name
    assert "user-defined function" not in str(err.value)


@pytest.mark.parametrize("sql", ["ALTER TABLE dbo.t RENAME TO t2", "ALTER TABLE dbo.t RENAME COLUMN a TO b"])
def test_alter_table_rename_isnt_tsql(sql):
    with pytest.raises(ValueError, match="ALTER TABLE ... RENAME isn't T-SQL.*sp_rename"):
        translate(sql)


@pytest.mark.parametrize("sql, expected", [
    ("EXEC sp_rename 'dbo.t', 't2'", 'ALTER TABLE "dbo"."t" RENAME TO "t2"'),
    ("EXEC sp_rename 't', 't2'", 'ALTER TABLE "dbo"."t" RENAME TO "t2"'),
    ("EXEC sp_rename '[sales].[t]', 't2', 'OBJECT'", 'ALTER TABLE "sales"."t" RENAME TO "t2"'),
    ("EXEC sp_rename @objname = N'dbo.t', @newname = N't2'", 'ALTER TABLE "dbo"."t" RENAME TO "t2"'),
    ("EXECUTE sys.sp_rename 'dbo.t', 't2'", 'ALTER TABLE "dbo"."t" RENAME TO "t2"'),
    ("sp_rename 'dbo.t', 't2'", 'ALTER TABLE "dbo"."t" RENAME TO "t2"'),
    ("EXEC sp_rename 'dbo.t.a', 'b', 'COLUMN'", 'ALTER TABLE "dbo"."t" RENAME COLUMN "a" TO "b"'),
    ("EXEC sp_rename 't.a', 'b', 'column'", 'ALTER TABLE "dbo"."t" RENAME COLUMN "a" TO "b"'),
    ("EXEC sp_rename @objtype = 'COLUMN', @newname = 'b', @objname = 'dbo.t.a'",
     'ALTER TABLE "dbo"."t" RENAME COLUMN "a" TO "b"'),
])
def test_sp_rename_renames_a_table_or_a_column(sql, expected):
    texts, _ = translate(sql)
    assert texts == [expected]


@pytest.mark.parametrize("sql, words", [
    ("EXEC sp_rename 'dbo.t.ix', 'ix2', 'INDEX'", "sp_rename with @objtype 'INDEX' isn't mapped yet"),
    ("EXEC sp_rename 'db', 'db2', 'DATABASE'", "sp_rename with @objtype 'DATABASE'"),
    ("EXEC sp_rename 'dbo.t', 'dbo.t2'", "would name the table [dbo.t2]"),
    ("EXEC sp_rename 'dbo.t', 'a/b'", "contain / or \\"),
    ("EXEC sp_rename 'dbo.t', 'ab.'", "end with a period"),
    ("EXEC sp_rename 'dbo.t', ''", "can't be empty"),
    ("EXEC sp_rename 'dbo.t'", "expects parameters '@objname' and '@newname'"),
    ("EXEC sp_rename @old, 't2'", "because variables aren't mapped yet"),
    ("EXEC sp_rename 'dbo.t', 't2', 'OBJECT', 'x'", "this sp_rename isn't mapped yet"),
    ("EXEC sp_rename 'a.b.c', 't2'", "can't read 'a.b.c'"),
    ("EXEC sp_rename 't', 'b', 'COLUMN'", "can't read 't'"),
    ("EXEC sp_rename '#t', 't2'", "renaming a #temp table"),
])
def test_an_sp_rename_that_isnt_mapped_is_refused_by_name(sql, words):
    with pytest.raises(ValueError) as err:
        translate(sql)
    assert words in str(err.value)


@pytest.mark.parametrize("view", ["ROUTINES", "PARAMETERS", "ROUTINE_COLUMNS", "VIEW_TABLE_USAGE", "DOMAINS"])
def test_an_information_schema_view_the_engine_lacks_is_refused_by_name(view):
    with pytest.raises(ValueError, match=f"INFORMATION_SCHEMA.{view} isn't mapped yet"):
        translate(f"SELECT * FROM INFORMATION_SCHEMA.{view}")


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "local-data")
    e = local.engine(local.create("Routines")["id"])
    e.execute("CREATE TABLE dbo.t (id INT, amount DECIMAL(9, 2))", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, 9.50)", dialect="tsql")
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def test_sp_rename_renames_a_project_table_and_then_a_column(engine):
    engine.execute("EXEC sp_rename 'dbo.t', 'orders'", dialect="tsql")
    engine.execute("EXEC sp_rename 'dbo.orders.amount', 'total', 'COLUMN'", dialect="tsql")
    assert [[r[0], str(r[1])] for r in rows(engine, "SELECT id, total FROM dbo.orders")] == [[1, "9.50"]]
    with pytest.raises(Exception, match=r"Invalid object name 'dbo\.t'\."):
        engine.execute("SELECT id FROM dbo.t", dialect="tsql")


def test_a_renamed_table_is_listed_by_its_new_name(engine):
    engine.execute("sp_rename 'dbo.t', 'orders'", dialect="tsql")
    assert rows(engine, "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES ORDER BY TABLE_NAME") == \
        [["dbo", "orders"]]


def test_a_rename_in_a_script_that_fails_renames_nothing(engine):
    with pytest.raises(ValueError, match="sp_executesql"):
        engine.execute("EXEC sp_rename 'dbo.t', 'orders';\nEXEC sp_executesql N'SELECT 1'", dialect="tsql")
    assert rows(engine, "SELECT id FROM dbo.t") == [[1]]


def test_renaming_a_delta_table_or_a_view_is_refused_by_name(engine):
    engine.execute("CREATE TABLE dbo.d USING DELTA AS SELECT 1 AS id", dialect="spark")
    engine.execute("CREATE VIEW dbo.v AS SELECT id FROM dbo.t", dialect="tsql")
    with pytest.raises(ValueError, match="Renaming dbo.d isn't mapped yet: it's one of the project's Delta tables"):
        engine.execute("EXEC sp_rename 'dbo.d', 'd2'", dialect="tsql")
    with pytest.raises(ValueError, match="Renaming the view dbo.v isn't mapped yet"):
        engine.execute("EXEC sp_rename 'dbo.v', 'v2'", dialect="tsql")
    assert rows(engine, "SELECT id FROM dbo.d") == [[1]] and rows(engine, "SELECT id FROM dbo.v") == [[1]]


def test_a_rename_that_would_leave_the_old_name_in_a_length_message_is_refused(engine):
    engine.execute("CREATE TABLE dbo.s (id INT, code VARCHAR(3))", dialect="tsql")
    with pytest.raises(ValueError, match="Renaming the column dbo.s.code isn't mapped yet"):
        engine.execute("EXEC sp_rename 'dbo.s.code', 'label', 'COLUMN'", dialect="tsql")
    with pytest.raises(ValueError, match="Renaming dbo.s isn't mapped yet: the checks"):
        engine.execute("EXEC sp_rename 'dbo.s', 's2'", dialect="tsql")
    engine.execute("EXEC sp_rename 'dbo.s.id', 'key', 'COLUMN'", dialect="tsql")  # a column with no length check
    assert rows(engine, "SELECT [key] FROM dbo.s") == []


def test_information_schema_lists_the_projects_own_objects(engine):
    engine.execute("CREATE TABLE #scratch (id INT)", dialect="tsql")
    engine.execute("CREATE VIEW dbo.v AS SELECT id FROM dbo.t", dialect="tsql")
    assert rows(engine, "SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES ORDER BY TABLE_NAME") == \
        [["t", "BASE TABLE"], ["v", "VIEW"]]
    assert rows(engine, "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS") == [["dbo", "v"]]
    assert rows(engine, "SELECT c.COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS AS c WHERE c.TABLE_NAME = 't' "
                        "ORDER BY c.ORDINAL_POSITION") == [["id"], ["amount"]]
    schemas = [r[0] for r in rows(engine, "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA")]
    assert "dbo" in schemas and "pg_catalog" not in schemas and "information_schema" not in schemas


def test_a_refused_drop_leaves_nothing_changed(engine):
    with pytest.raises(ValueError, match="DROP PROCEDURE dbo.t isn't mapped yet"):
        engine.execute("DROP PROCEDURE dbo.t", dialect="tsql")
    assert rows(engine, "SELECT id FROM dbo.t") == [[1]]
