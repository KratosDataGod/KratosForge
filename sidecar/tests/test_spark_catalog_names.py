"""Unity Catalog style names in a notebook's Spark on this PC: catalog.schema.table, USE CATALOG, USE SCHEMA and
backtick-quoted names each read or write the attached lakehouse's table they name, or stop and say why. None
resolves to another table without saying so."""

from __future__ import annotations

import pytest

from fabric_desktop import lakehouse
from tests.test_spark_writer import ORIGINAL, lake, rows, run  # noqa: F401 - lake is a fixture

CATALOG = "isn't the attached lakehouse, and a catalog"


@pytest.mark.parametrize("code", [
    "spark.sql('SELECT count(*) AS n FROM Sales.dbo.sales').first()['n']",
    "spark.sql('SELECT count(*) AS n FROM `Sales`.`dbo`.`sales`').first()['n']",
    "spark.sql('SELECT count(*) AS n FROM `dbo`.`sales`').first()['n']",
    "spark.table('`dbo`.`sales`').count()",
    "spark.read.table('Sales.dbo.sales').count()",
])
def test_the_lakehouses_own_names_read_its_table(lake, code):  # noqa: F811
    assert run(code) == ("ok", "3")


@pytest.mark.parametrize("code", [
    "spark.sql('SELECT * FROM main.dbo.sales').collect()",
    "spark.table('main.dbo.sales')",
    "spark.read.table('main.dbo.sales')",
    "spark.sql('SELECT * FROM `main`.`dbo`.`sales`').collect()",
])
def test_another_catalog_is_refused_on_read(lake, code):  # noqa: F811
    status, text = run(code)
    assert status == "error" and f"main {CATALOG}" in text


@pytest.mark.parametrize("code", [
    "spark.sql('CREATE TABLE main.dbo.made AS SELECT 1 AS id')",
    "spark.sql(\"INSERT INTO main.dbo.sales VALUES (9, 'EU')\")",
    "df.write.mode('append').saveAsTable('main.dbo.sales')",
    "df.write.mode('overwrite').saveAsTable('main.dbo.made')",
])
def test_another_catalog_is_refused_on_write_and_nothing_is_written(lake, code):  # noqa: F811
    """These used to write the attached lakehouse's dbo.made and dbo.sales, dropping the catalog."""
    status, text = run(code)
    assert status == "error" and f"main {CATALOG}" in text
    assert rows(lake) == ORIGINAL and not (lake / "dbo" / "made").exists()


@pytest.mark.parametrize("code", ["spark.sql('DELETE FROM main.dbo.sales WHERE id = 1')",
                                  "spark.sql('UPDATE main.dbo.sales SET region = 1')"])
def test_a_change_in_another_catalog_is_refused(lake, code):  # noqa: F811
    status, _ = run(code)
    assert status == "error" and rows(lake) == ORIGINAL


def test_a_backticked_name_with_a_dot_is_one_name_and_is_refused(lake):  # noqa: F811
    """In Spark `dbo.sales` is a single name; it used to read dbo.sales here."""
    status, text = run("spark.sql('SELECT * FROM `dbo.sales`').collect()")
    assert status == "error" and "one name with a dot in it" in text
    status, text = run("df.write.mode('append').saveAsTable('`dbo.sales`')")
    assert status == "error" and "one name with a dot in it" in text and rows(lake) == ORIGINAL


def test_backticked_parts_write_the_table_they_name(lake):  # noqa: F811
    assert run("df.write.mode('append').saveAsTable('`Sales`.`dbo`.`sales`')")[0] == "ok"
    assert len(rows(lake)) == 4


@pytest.mark.parametrize("statement", ["USE CATALOG Sales", "USE CATALOG spark_catalog", "USE SCHEMA dbo",
                                       "USE dbo", "USE DATABASE dbo", "USE Sales", "USE SCHEMA Sales.dbo"])
def test_use_runs_when_it_names_where_tables_already_resolve(lake, statement):  # noqa: F811
    status, _ = run(f"spark.sql({statement!r})\nspark.table('sales').count()")
    assert status == "ok"


@pytest.mark.parametrize("statement, says", [
    ("USE CATALOG main", "the catalog main"),
    ("USE main.crm", "the catalog main"),
    ("USE SCHEMA main.dbo", "the catalog main"),
    ("USE SCHEMA crm", "can't point it at crm"),
    ("USE crm", "can't point it at crm"),
    ("USE DATABASE crm", "can't point it at crm"),
])
def test_use_of_another_catalog_or_schema_is_refused(lake, statement, says):  # noqa: F811
    status, text = run(f"spark.sql({statement!r})")
    assert status == "error" and says in text and "nothing changed" in text


@pytest.mark.parametrize("name, parts", [
    ("sales", ["sales"]), ("dbo.sales", ["dbo", "sales"]), ("`dbo`.`sales`", ["dbo", "sales"]),
    ("`dbo.sales`", ["dbo.sales"]), ("`a``b`.c", ["a`b", "c"]),
])
def test_name_parts_reads_backticks_as_spark_does(name, parts):
    assert lakehouse.name_parts(name) == parts


def test_an_unclosed_backtick_is_refused():
    with pytest.raises(ValueError, match="backtick isn't closed"):
        lakehouse.name_parts("`dbo.sales")


@pytest.mark.parametrize("code, says", [
    ("spark.sql('DESCRIBE HISTORY `dbo.sales`').collect()", "one name with a dot in it"),
    ("spark.sql('DESCRIBE HISTORY main.dbo.sales').collect()", f"main {CATALOG}"),
    ("spark.sql('DROP TABLE IF EXISTS main.dbo.gone')", f"main {CATALOG}"),
])
def test_history_and_drop_name_the_same_table_or_refuse(lake, code, says):  # noqa: F811
    status, text = run(code)
    assert status == "error" and says in text


def test_history_of_the_lakehouses_own_table_still_reads(lake):  # noqa: F811
    assert run("spark.sql('DESCRIBE HISTORY `Sales`.`dbo`.`sales`').count()") == ("ok", "1")


# ---- spark.catalog: answers about the attached lakehouse and temp views

def test_catalog_list_columns_reads_a_lakehouse_table_and_a_temp_view(lake):  # noqa: F811
    status, text = run("[(c.name, c.dataType) for c in spark.catalog.listColumns('sales', 'dbo')]")
    assert status == "ok" and "('id', 'bigint'), ('region', 'string')" in text
    status, text = run("df.createOrReplaceTempView('v')\n[c.name for c in spark.catalog.listColumns('v')]")
    assert (status, text) == ("ok", "['id', 'region']")


def test_catalog_list_columns_of_a_missing_table_stops(lake):  # noqa: F811
    """DuckDB's listColumns gave [] for any name; Spark raises for a table that isn't there."""
    assert run("spark.catalog.listColumns('nope')")[0] == "error"


@pytest.fixture
def listed(lake, monkeypatch):  # noqa: F811
    """List the test lakehouse's folders from disk, as FileStore.list does from OneLake."""
    from fabric_desktop import spark_local

    def dirs(directory):
        here = lake.parent / directory
        return [{"name": p.name, "is_dir": True} for p in sorted(here.iterdir())
                if p.is_dir() and not p.name.startswith(("_", "."))] if here.is_dir() else []
    monkeypatch.setattr(spark_local, "_list_dirs", dirs)
    return lake


def test_catalog_list_tables_names_lakehouse_tables_and_temp_views(listed):
    status, text = run("df.createOrReplaceTempView('v')\n"
                       "[(t.name, t.database, t.isTemporary) for t in spark.catalog.listTables()]")
    assert status == "ok", text
    assert "('sales', 'dbo', False)" in text and "('v', None, True)" in text
    assert "duckdb" not in text.lower() and "__kf_" not in text


def test_catalog_list_tables_filters_by_schema_and_pattern(listed):
    assert run("[t.name for t in spark.catalog.listTables('dbo')]") == ("ok", "['sales']")
    assert run("[t.name for t in spark.catalog.listTables('crm')]") == ("ok", "[]")
    assert run("df.createOrReplaceTempView('v')\n[t.name for t in spark.catalog.listTables(pattern='sa*')]") \
        == ("ok", "['sales']")


def test_catalog_list_tables_lists_global_temp_views(listed):
    status, text = run("df.createOrReplaceGlobalTempView('g')\n"
                       "[(t.name, t.database) for t in spark.catalog.listTables('global_temp')]")
    assert (status, text) == ("ok", "[('g', 'global_temp')]")


def test_catalog_list_databases_and_catalogs(listed):
    assert run("[d.name for d in spark.catalog.listDatabases()]") == ("ok", "['dbo']")
    assert run("[c.name for c in spark.catalog.listCatalogs()]") == ("ok", "['spark_catalog']")


def test_catalog_list_functions_names_spark_builtins(listed):
    status, text = run("names = [f.name for f in spark.catalog.listFunctions()]\n"
                       "('upper' in names, 'date_add' in names, len(names) > 300)")
    assert (status, text) == ("ok", "(True, True, True)")
    assert run("[f.name for f in spark.catalog.listFunctions(pattern='upp*')]") == ("ok", "['upper']")


def test_catalog_listings_with_no_lakehouse_are_empty_not_duckdbs(listed):
    lakehouse.attach(None)
    status, text = run("df.createOrReplaceTempView('v')\n"
                       "([t.name for t in spark.catalog.listTables()], spark.catalog.listDatabases())")
    assert (status, text) == ("ok", "(['v'], [])")


def test_catalog_current_and_set_follow_use(lake):  # noqa: F811
    assert run("spark.catalog.currentCatalog()") == ("ok", "'spark_catalog'")
    assert run("spark.catalog.setCurrentCatalog('Sales')\nspark.catalog.setCurrentDatabase('dbo')")[0] == "ok"
    status, text = run("spark.catalog.setCurrentCatalog('main')")
    assert status == "error" and "setCurrentCatalog('main'): the catalog main" in text
    status, text = run("spark.catalog.setCurrentDatabase('crm')")
    assert status == "error" and "can't point it at crm" in text


def test_catalog_current_database_is_the_lakehouse_without_schemas(lake, monkeypatch):  # noqa: F811
    """In Fabric the current database is the default lakehouse. With schemas, which one this PC can't say: refused."""
    assert run("spark.catalog.currentDatabase()")[0] == "error"  # the fixture's lakehouse has schemas
    import dataclasses
    monkeypatch.setattr(lakehouse, "_attached", dataclasses.replace(lakehouse._attached, schemas_enabled=lambda: False))
    assert run("spark.catalog.currentDatabase()") == ("ok", "'Sales'")


def test_catalog_list_columns_marks_a_lakehouse_tables_partition_columns(lake):  # noqa: F811
    """A table Fabric wrote partitioned by region: listColumns says so, as Spark does."""
    import pyarrow as pa
    from deltalake import write_deltalake
    write_deltalake(str(lake / "dbo" / "parts"), pa.table({"id": [1], "region": ["EU"]}), partition_by=["region"])
    status, text = run("[(c.name, c.isPartition) for c in spark.catalog.listColumns('dbo.parts')]")
    assert status == "ok" and "('region', True)" in text and "('id', False)" in text
