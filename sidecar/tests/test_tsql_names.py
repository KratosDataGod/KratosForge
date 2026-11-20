"""WR-216: names, schemas and views in local T-SQL, as a Fabric Warehouse resolves them.

- One- and two-part names, [brackets] and "quotes"; a name without a schema means dbo.
- A name that would resolve to another schema's table, a three-part name (another warehouse or a SQL analytics
  endpoint), a four-part name and sys catalog views are refused by name, never answered from the wrong table.
- CREATE, ALTER (as CREATE OR REPLACE) and DROP VIEW; a view's names are bound to dbo when it's made; a view whose
  table was dropped stops with an error.
- CREATE and DROP SCHEMA; AUTHORIZATION and SCHEMABINDING are refused by name."""
from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture()
def local(tmp_path):
    store = LocalData(tmp_path / "app" / "local-data")
    yield store
    store.close_all()


@pytest.fixture()
def engine(local):
    e = local.engine(local.create("Names")["id"])
    e.execute("CREATE TABLE dbo.t (id INT, [my col] VARCHAR(10))", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, 'a')", dialect="tsql")
    return e


def run(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


@pytest.mark.parametrize("sql", ["SELECT id FROM t", "SELECT id FROM dbo.t", "SELECT id FROM [dbo].[t]",
                                 'SELECT "id" FROM "dbo"."t"', "SELECT [id] FROM DBO.T"])
def test_one_and_two_part_names(engine, sql):
    assert run(engine, sql) == [[1]]


def test_bracketed_and_quoted_column_names(engine):
    assert run(engine, 'SELECT [my col], "my col" FROM dbo.t') == [["a", "a"]]


def test_a_schema_of_your_own(engine):
    run(engine, "CREATE SCHEMA sales")
    run(engine, "CREATE TABLE sales.t (x INT)")
    run(engine, "INSERT INTO sales.t VALUES (9)")
    assert run(engine, "SELECT x FROM sales.t") == [[9]]
    assert run(engine, "SELECT id FROM t") == [[1]]  # still dbo.t


def test_a_name_without_a_schema_never_reads_another_schemas_table(engine):
    run(engine, "CREATE SCHEMA sales")
    run(engine, "CREATE TABLE sales.only_here (x INT)")
    for sql in ("SELECT * FROM only_here", "INSERT INTO only_here VALUES (1)", "DELETE FROM only_here",
                "DROP TABLE only_here"):
        with pytest.raises(ValueError, match=r"Invalid object name 'only_here': a name without a schema is looked "
                                             r"for in dbo, as in T-SQL, and there's no dbo\.only_here\. Write "
                                             r"sales\.only_here"):
            engine.execute(sql, dialect="tsql")
    assert run(engine, "SELECT COUNT(*) FROM sales.only_here") == [[0]]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM only_here AS only_here",
    "SELECT * FROM only_here only_here",
    "DELETE FROM only_here WHERE x IN (SELECT id FROM dbo.t AS only_here)",
    "INSERT INTO only_here SELECT id FROM dbo.t AS only_here",
    "UPDATE only_here SET x = 1 FROM dbo.t AS c",
])
def test_an_alias_never_lets_a_name_reach_another_schema(engine, sql):
    run(engine, "CREATE SCHEMA sales")
    run(engine, "CREATE TABLE sales.only_here (x INT)")
    run(engine, "INSERT INTO sales.only_here VALUES (1)")
    with pytest.raises(ValueError, match=r"Invalid object name 'only_here'"):
        engine.execute(sql, dialect="tsql")
    assert run(engine, "SELECT x FROM sales.only_here") == [[1]]


@pytest.mark.parametrize("schema", ["main", "sales"])
def test_update_through_an_alias_changes_the_aliased_table_only(engine, schema):
    if schema != "main":
        run(engine, f"CREATE SCHEMA {schema}")
    run(engine, f"CREATE TABLE {schema}.orders (id INT, [my col] VARCHAR(10))")
    run(engine, f"INSERT INTO {schema}.orders VALUES (1, 'keep')")
    run(engine, "UPDATE orders SET [my col] = 'changed' FROM dbo.t AS orders WHERE orders.id = 1")
    assert run(engine, "SELECT id, [my col] FROM dbo.t") == [[1, "changed"]]
    assert run(engine, f"SELECT id, [my col] FROM {schema}.orders") == [[1, "keep"]]


def test_a_string_that_holds_create_schema_authorization_is_text(engine):
    assert run(engine, "SELECT 'CREATE SCHEMA x AUTHORIZATION y'") == [["CREATE SCHEMA x AUTHORIZATION y"]]


def test_a_cte_a_temp_table_and_an_alias_are_not_schema_names(engine):
    run(engine, "CREATE SCHEMA sales")
    run(engine, "CREATE TABLE sales.c (x INT)")
    assert run(engine, "WITH c AS (SELECT 5 AS x) SELECT x FROM c") == [[5]]
    run(engine, "CREATE TABLE #c (x INT)")
    assert run(engine, "SELECT COUNT(*) FROM #c") == [[0]]
    assert run(engine, "SELECT c.id FROM dbo.t AS c") == [[1]]
    from fabric_desktop.localdata.tsql import translate  # the alias c names dbo.t, never sales.c
    texts, _ = translate("UPDATE c SET id = 2 FROM dbo.t AS c", {"dbo": {"t": {"id": "INTEGER"}},
                                                                   "sales": {"c": {"x": "INTEGER"}}})
    assert texts == ["UPDATE dbo.t AS c SET id = 2"]


@pytest.mark.parametrize(("sql", "words"), [
    ("SELECT * FROM otherwh.dbo.t", (r"three-part names \(otherwh\.dbo\.t\) aren't mapped yet: on this PC a project "
                                    r"is one database, so another warehouse or a SQL analytics endpoint can't be "
                                    r"named\. Write dbo\.t for this project's table")),
    ("SELECT * FROM Names.dbo.t", r"three-part names \(Names\.dbo\.t\)"),
    ("SELECT * FROM mylakehouse..t", r"three-part names \(mylakehouse\.\.t\).* Write dbo\.t"),
    ("INSERT INTO otherwh.dbo.t VALUES (1, 'a')", "three-part names"),
    ("SELECT * FROM srv.db.dbo.t", (r"four-part names \(srv\.db\.dbo\.t\) aren't supported in a Fabric Warehouse "
                                   r"either, because it has no linked servers\. Write dbo\.t")),
    ("SELECT * FROM sys.objects", r"catalog views \(sys\.objects\) aren't mapped yet\. INFORMATION_SCHEMA\.TABLES"),
    ("SELECT * FROM sys.tables", r"catalog views \(sys\.tables\)"),
    ("CREATE SCHEMA s AUTHORIZATION dbo", r"CREATE SCHEMA \.\.\. AUTHORIZATION isn't mapped yet"),
    ("CREATE VIEW dbo.b WITH SCHEMABINDING AS SELECT id FROM dbo.t",
     r"CREATE VIEW \.\.\. WITH SCHEMABINDING isn't mapped yet"),
])
def test_what_cant_resolve_here_is_refused_by_name(engine, sql, words):
    with pytest.raises(ValueError, match=words):
        engine.execute(sql, dialect="tsql")


def test_information_schema_runs_with_a_note(engine):
    out = engine.execute("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 't'",
                         dialect="tsql")
    assert [list(r) for r in out["rows"]] == [["dbo", "t"]]
    assert any("INFORMATION_SCHEMA lists this project's local database" in n
               for n in out["translation"]["notes"])


def test_create_alter_and_drop_view(engine):
    run(engine, "CREATE VIEW dbo.v AS SELECT id FROM t")
    assert run(engine, "SELECT id FROM v") == [[1]]
    run(engine, "ALTER VIEW dbo.v AS SELECT id + 1 AS id FROM t")
    assert run(engine, "SELECT id FROM dbo.v") == [[2]]
    run(engine, "CREATE OR ALTER VIEW dbo.v AS SELECT 5 AS id")
    assert run(engine, "SELECT id FROM dbo.v") == [[5]]
    run(engine, "DROP VIEW dbo.v")
    run(engine, "DROP VIEW IF EXISTS dbo.v")
    with pytest.raises(ValueError, match=r"Invalid object name 'dbo\.v': ALTER VIEW changes a view that's there"):
        engine.execute("ALTER VIEW dbo.v AS SELECT 1 AS id", dialect="tsql")


def test_a_view_keeps_reading_dbo_and_stops_when_its_table_is_dropped(engine):
    run(engine, "CREATE VIEW dbo.w AS SELECT id FROM t")
    run(engine, "CREATE SCHEMA sales")
    run(engine, "CREATE TABLE sales.t (id INT)")
    run(engine, "INSERT INTO sales.t VALUES (99)")
    run(engine, "DROP TABLE dbo.t")
    with pytest.raises(Exception, match=r"Invalid object name 't'\."):
        engine.execute("SELECT id FROM dbo.w", dialect="tsql")  # not sales.t's 99


def test_create_and_drop_schema(engine):
    run(engine, "CREATE SCHEMA staging")
    run(engine, "CREATE TABLE staging.x (a INT)")
    with pytest.raises(Exception, match="depend"):
        engine.execute("DROP SCHEMA staging", dialect="tsql")  # T-SQL refuses too: the schema isn't empty
    run(engine, "DROP TABLE staging.x")
    run(engine, "DROP SCHEMA staging")
    run(engine, "DROP SCHEMA IF EXISTS staging")
