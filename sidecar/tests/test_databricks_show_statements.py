"""Databricks SQL's metadata statements in a DuckDB cell on this PC: SHOW CATALOGS, SCHEMAS, TABLES, VIEWS and
COLUMNS answer from the session's own catalog with the columns Databricks' reference gives each one, in the Unity
Catalog names of WR-316 (the catalog main, its schema default); what only a Delta table or Unity Catalog has is
refused by name, so no other answer gets through.

https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-names: "Unity Catalog stores all object names as
lowercase", and it keeps a column name's case."""

from __future__ import annotations

import pytest
import sqlglot

from fabric_desktop.databricks_builtins_sql import unity_names
from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture()
def cell():
    session = local_sql.DuckDBSession(SessionConfig("nb"))

    def run(code, columns=False):
        result = session.execute(CellRequest(code, "sql", "databricks"))
        output = result.outputs[-1] if result.outputs else {}
        if result.status != "ok":
            return "error: " + str(output.get("evalue") or output.get("text"))
        return (output.get("columns"), output.get("rows")) if columns else output.get("rows")
    run("CREATE TABLE Sam (id INT, Name STRING); CREATE TABLE sam1 (a INT); CREATE VIEW suj AS SELECT 1 AS x; "
        "CREATE SCHEMA usersc; CREATE TABLE usersc.user1 (u INT); CREATE VIEW usersc.user2 AS SELECT 2 AS u; "
        "CREATE TEMPORARY VIEW temp2 AS SELECT 3 AS t")
    yield run
    session.close()


def _refused(answer, what):
    assert answer.startswith(f"error: {what} can't run on this PC the way it runs in Databricks, because "), answer
    assert ", so nothing ran. Run the cell on a Databricks SQL warehouse, or " in answer, answer


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-catalogs: one column, catalog;
# the session answers for main and the catalogs that name it (spark_catalog is a name for one, not a catalog)
def test_show_catalogs(cell):
    assert cell("SHOW CATALOGS", columns=True) == (["catalog"], [["hive_metastore"], ["main"], ["workspace"]])
    assert cell("SHOW CATALOGS LIKE 'ma*'") == [["main"]]
    assert cell("SHOW CATALOGS 'HIVE*|work*'") == [["hive_metastore"], ["workspace"]]


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-schemas: one column, databaseName;
# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-information-schema: "each catalog created in Unity
# Catalog also automatically includes an information_schema"; DuckDB's main schema is default, and its own
# pg_catalog isn't listed
def test_show_schemas(cell):
    expected = (["databaseName"], [["default"], ["information_schema"], ["usersc"]])
    assert cell("SHOW SCHEMAS", columns=True) == expected
    assert cell("SHOW DATABASES IN main", columns=True) == expected
    assert cell("SHOW SCHEMAS FROM workspace") == expected[1]
    assert cell("SHOW SCHEMAS IN hive_metastore") == [["default"], ["usersc"]]  # not a Unity Catalog catalog
    assert cell("SHOW SCHEMAS LIKE 'user*|def*'") == [["default"], ["usersc"]]
    assert cell("SHOW SCHEMAS IN main LIKE 'nothing'") == []


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-tables: database, tableName,
# isTemporary; the current schema unless one is named, a temporary one with a blank database
def test_show_tables(cell):
    assert cell("SHOW TABLES", columns=True) == (["database", "tableName", "isTemporary"], [
        ["default", "sam", False], ["default", "sam1", False], ["default", "suj", False], ["", "temp2", True]])
    assert cell("SHOW TABLES FROM usersc") == [["usersc", "user1", False], ["usersc", "user2", False],
                                               ["", "temp2", True]]
    # the doc's own example: SHOW TABLES LIKE 'sam*|suj'
    assert cell("SHOW TABLES LIKE 'sam*|suj'") == [["default", "sam", False], ["default", "sam1", False],
                                                   ["default", "suj", False]]
    assert cell("SHOW TABLES IN main.default ' SAM '") == [["default", "sam", False]]  # blanks trimmed, any case
    assert cell("SHOW TABLES IN hive_metastore.usersc LIKE 'user_'") == []  # _ is itself, not any one character
    assert cell("USE SCHEMA usersc; SHOW TABLES LIKE 'user1'") == [["usersc", "user1", False]]


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-views: namespace, viewName,
# isTemporary; local temporary views "regardless of a given schema"
def test_show_views(cell):
    assert cell("SHOW VIEWS", columns=True) == (["namespace", "viewName", "isTemporary"], [
        ["default", "suj", False], ["", "temp2", True]])
    assert cell("SHOW VIEWS FROM usersc") == [["usersc", "user2", False], ["", "temp2", True]]
    assert cell("SHOW VIEWS LIKE 'sam|suj|temp*'") == [["default", "suj", False], ["", "temp2", True]]


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-tables (and -show-schemas): a
# schema that isn't there stops with SCHEMA_NOT_FOUND
def test_show_tables_in_a_missing_schema(cell):
    answer = cell("SHOW TABLES IN nope")
    assert answer.startswith("error: [SCHEMA_NOT_FOUND] The schema `main`.`nope` cannot be found."), answer
    assert cell("SHOW VIEWS IN nope").startswith("error: [SCHEMA_NOT_FOUND]")


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-columns: one column, col_name,
# in the table's order; the schema after the table, or in its name; "If the table does not exist, an exception
# is thrown"
def test_show_columns(cell):
    assert cell("SHOW COLUMNS IN sam", columns=True) == (["col_name"], [["id"], ["Name"]])
    assert cell("SHOW COLUMNS FROM user1 IN usersc") == [["u"]]
    assert cell("SHOW COLUMNS IN usersc.user1 FROM usersc") == [["u"]]
    assert cell("SHOW COLUMNS IN main.default.suj") == [["x"]]
    assert cell("SHOW COLUMNS IN temp2") == [["t"]]
    answer = cell("SHOW COLUMNS IN nowhere")
    assert answer.startswith("error: [TABLE_OR_VIEW_NOT_FOUND] The table or view nowhere cannot be found"), answer


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-columns: "table name should not
# be qualified with a different schema name"
def test_show_columns_with_two_schemas_stops_as_databricks_does(cell):
    answer = cell("SHOW COLUMNS IN usersc.user1 IN default")
    assert answer.startswith("error: SHOW COLUMNS names two schemas, so nothing ran. Databricks stops it too"), answer


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-schemas (and -show-tables): a
# catalog outside the session is refused by name, as WR-316 refuses any name in it
_OWN_CATALOG = "is a catalog of this PC's own engine, not one of the session's, and this session holds only its own tables"


@pytest.mark.parametrize("code, what, kind", [
    ("SHOW SCHEMAS IN prod", "SHOW SCHEMAS IN prod", "is a Unity Catalog catalog, and this session holds only its own tables"),
    ("SHOW TABLES IN prod.sales", "SHOW TABLES IN prod.sales", "is a Unity Catalog catalog, and"),
    ("SHOW COLUMNS IN prod.sales.orders", "SHOW COLUMNS IN prod.sales", "is a Unity Catalog catalog, and"),
    # DuckDB's own catalogs are named as this PC's, as a table name in them is (WR-316)
    ("SHOW VIEWS FROM system.information_schema", "SHOW VIEWS IN system.information_schema", _OWN_CATALOG),
    ("SHOW TABLES IN memory.default", "SHOW TABLES IN memory.default", _OWN_CATALOG),
    ("SHOW SCHEMAS IN temp", "SHOW SCHEMAS IN temp", _OWN_CATALOG),
])
def test_another_catalog_is_refused_by_name(cell, code, what, kind):
    answer = cell(code)
    _refused(answer, what)
    assert kind in answer, answer
    assert "name the table as main.schema.table, schema.table or table" in answer, answer


# WR-316: DuckDB's main schema is default here, so a schema named main is refused as a table's schema and USE main
# are, and SHOW SCHEMAS never lists main
@pytest.mark.parametrize("code, what", [
    ("SHOW TABLES IN main", "SHOW TABLES IN main"),
    ("SHOW VIEWS FROM workspace.main", "SHOW VIEWS IN workspace.main"),
    ("SHOW COLUMNS IN main.sam", "SHOW COLUMNS IN main"),
    ("SHOW COLUMNS IN sam IN main", "SHOW COLUMNS IN main"),
])
def test_a_schema_named_main_is_refused_since_it_is_default_here(cell, code, what):
    answer = cell(code)
    _refused(answer, what)
    assert "this PC's engine already uses the name main for the schema shown as default" in answer, answer
    assert "main" not in [row[0] for row in cell("SHOW SCHEMAS")]


# and system, temp and memory, which DuckDB keeps for its own catalogs, are refused as USE refuses them
@pytest.mark.parametrize("name", ["system", "temp", "memory"])
def test_a_schema_with_a_duckdb_catalog_name_is_refused(cell, name):
    answer = cell(f"SHOW TABLES IN {name}")
    _refused(answer, f"SHOW TABLES IN {name}")
    assert f"DuckDB keeps the name {name} for one of its own catalogs" in answer, answer


@pytest.mark.parametrize("schema", ["main.default", "workspace.default", "hive_metastore.default", "default"])
def test_show_tables_in_the_sessions_default_schema(cell, schema):
    assert cell(f"SHOW TABLES IN {schema} LIKE 'sam'") == [["default", "sam", False]]


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-tables: "Except for * and |
# character, the pattern works like a regular expression", which this PC reads the same way only for names
def test_a_pattern_this_pc_would_read_another_way_is_refused(cell):
    _refused(cell("SHOW TABLES LIKE 'sam.'"), "The SHOW pattern 'sam.'")
    _refused(cell("SHOW SCHEMAS LIKE '[a-z]*'"), "The SHOW pattern '[a-z]*'")


# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-aux-show-tblproperties (a table's
# properties), -show-create-table (its DDL, USING delta), sql-ref-syntax-aux-describe-schema (Database Name,
# Description, Location), sql-ref-syntax-aux-describe-table (EXTENDED's Type, Provider, Location; DETAIL's files and
# versions) and delta-history: what only a Delta table or Unity Catalog has can't be answered here
@pytest.mark.parametrize("code, what", [
    ("SHOW TBLPROPERTIES sam", "SHOW TBLPROPERTIES"),
    ("SHOW TBLPROPERTIES sam ('delta.appendOnly')", "SHOW TBLPROPERTIES"),
    ("SHOW CREATE TABLE sam", "SHOW CREATE TABLE"),
    ("DESCRIBE SCHEMA usersc", "DESCRIBE SCHEMA"),
    ("DESC SCHEMA EXTENDED usersc", "DESCRIBE SCHEMA"),
    ("DESCRIBE DATABASE default", "DESCRIBE DATABASE"),
    ("DESCRIBE DETAIL sam", "DESCRIBE DETAIL"),
    ("DESC DETAIL main.default.sam", "DESCRIBE DETAIL"),
    ("DESCRIBE TABLE EXTENDED sam", "DESCRIBE TABLE EXTENDED"),
    ("DESCRIBE EXTENDED sam", "DESCRIBE TABLE EXTENDED"),
    ("DESCRIBE TABLE FORMATTED sam", "DESCRIBE TABLE FORMATTED"),
    ("DESCRIBE HISTORY sam", "DESCRIBE HISTORY"),
    ("SHOW FUNCTIONS", "SHOW FUNCTIONS"),
    ("SHOW TABLE EXTENDED LIKE 'sam'", "SHOW TABLE EXTENDED"),
    ("SHOW PARTITIONS sam", "SHOW PARTITIONS"),
])
def test_delta_and_unity_catalog_metadata_is_refused_by_name(cell, code, what):
    _refused(cell(code), what)


def test_a_show_this_pc_cant_read_is_refused_with_its_form(cell):
    answer = cell("SHOW TABLES IN usersc sam")
    _refused(answer, "This SHOW TABLES")
    assert "SHOW TABLES [IN schema] [LIKE 'pattern']" in answer


# a SHOW still can't reach outside the session: a statement after it that would is refused before anything runs
def test_the_policy_still_refuses_what_reaches_outside(cell):
    answer = cell("SHOW TABLES; COPY (SELECT 1) TO 'x.csv'")
    assert answer.startswith("error: COPY isn't allowed in a DuckDB engine session (statement 2)"), answer


def test_spark_sql_show_is_unchanged():
    session = local_sql.DuckDBSession(SessionConfig("nb"))
    try:
        result = session.execute(CellRequest("SHOW TABLES", "sql", "spark"))
        assert result.status != "ok"
        assert "SHOW isn't allowed in a DuckDB engine session" in str(result.outputs[-1].get("evalue"))
    finally:
        session.close()


@pytest.mark.parametrize("sql", ["SELECT * FROM memory.t", "SELECT * FROM system.t", "SELECT * FROM temp.t",
                                 "CREATE SCHEMA memory", "DROP SCHEMA temp", "SELECT * FROM main.memory.t"])
def test_a_schema_with_a_duckdb_catalog_name_is_refused_in_a_table_name(sql):
    with pytest.raises(ValueError, match="DuckDB keeps the name"):
        unity_names(sqlglot.parse_one(sql, read="databricks"))


def test_the_sessions_information_schema_still_reads():
    for sql in ("SELECT * FROM system.information_schema.tables", "SELECT * FROM information_schema.tables"):
        assert unity_names(sqlglot.parse_one(sql, read="databricks"))
