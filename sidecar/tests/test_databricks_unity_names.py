"""Unity Catalog names in a Databricks SQL cell on this PC (WR-316): the session is the catalog main, with the
schema default, as a workspace's default catalog is on Databricks; any other catalog is refused by name.

https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-names (catalog.schema.table),
https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-use-catalog,
https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-usedb,
https://docs.databricks.com/aws/en/sql/language-manual/functions/current_schema"""

from __future__ import annotations

import pytest

from fabric_desktop.engines import local_sql
from fabric_desktop.engines.contract import CellRequest, SessionConfig


@pytest.fixture()
def cell():
    session = local_sql.DuckDBSession(SessionConfig("nb"))

    def run(code):
        result = session.execute(CellRequest(code, "sql", "databricks"))
        output = result.outputs[-1] if result.outputs else {}
        if result.status != "ok":
            return "error: " + str(output.get("evalue") or output.get("text"))
        return output.get("rows")
    run("CREATE TABLE t (id INT); INSERT INTO t VALUES (1)")
    yield run
    session.close()


def test_current_catalog_and_schema_answer_as_databricks(cell):
    assert cell("SELECT current_catalog(), current_schema(), current_database()") == [["main", "default", "default"]]


@pytest.mark.parametrize("name", ["main.default.t", "`main`.`default`.`t`", "MAIN.Default.T", "default.t", "t",
                                  "workspace.default.t", "hive_metastore.default.t", "spark_catalog.default.t"])
def test_three_part_names_in_the_session_catalog_read_its_tables(cell, name):
    assert cell(f"SELECT id FROM {name}") == [[1]]


def test_three_part_names_write_and_create(cell):
    assert cell("CREATE SCHEMA main.sales") == []
    cell("CREATE TABLE main.sales.o (x INT); INSERT INTO main.sales.o VALUES (7)")
    assert cell("SELECT x FROM sales.o") == [[7]]
    cell("UPDATE main.sales.o SET x = 8")
    assert cell("SELECT x FROM main.sales.o") == [[8]]


@pytest.mark.parametrize("code, what", [
    ("SELECT * FROM prod.sales.orders", "A table in the catalog prod"),
    ("INSERT INTO prod.default.t VALUES (2)", "A table in the catalog prod"),
    ("SELECT * FROM memory.main.t", "A table in the catalog memory"),  # DuckDB's own name isn't Databricks'
    ("USE CATALOG prod", "USE CATALOG prod"),
    ("USE SCHEMA prod.sales", "USE SCHEMA prod.sales"),
])
def test_another_catalog_is_refused_by_name(cell, code, what):
    answer = cell(code)
    assert answer.startswith(f"error: {what} can't run on this PC the way it runs in Databricks, because "), answer
    assert "Run the cell on a Databricks SQL warehouse, or name the table as main.schema.table, schema.table or table" in answer
    assert cell("SELECT count(*) FROM t") == [[1]]  # nothing ran


def test_use_schema_makes_it_current_for_later_cells(cell):
    cell("CREATE SCHEMA sales; CREATE TABLE sales.o (x INT); INSERT INTO sales.o VALUES (5)")
    assert cell("USE SCHEMA sales") == []
    assert cell("SELECT current_schema(), x FROM o") == [["sales", 5]]
    for code in ("USE DATABASE default", "USE main.sales", "USE default", "USE CATALOG main"):
        cell(code)
    # USE CATALOG makes the catalog's default schema current, as on Databricks
    assert cell("SELECT current_schema(), id FROM t") == [["default", 1]]


def test_use_a_schema_the_cell_creates_first(cell):
    assert cell("CREATE SCHEMA s2; USE s2; CREATE TABLE x (a INT); SELECT current_schema()") == [["s2"]]
    assert cell("SELECT count(*) FROM main.s2.x") == [[0]]


def test_use_a_missing_schema_stops_with_schema_not_found(cell):
    answer = cell("USE SCHEMA nope")
    assert answer == ("error: [SCHEMA_NOT_FOUND] The schema `main`.`nope` cannot be found. Verify the spelling and "
                      "correctness of the schema and catalog."), answer
    assert cell("SELECT current_schema()") == [["default"]]


@pytest.mark.parametrize("name", ["system", "temp", "memory"])
def test_use_of_a_duckdb_catalog_name_is_refused(cell, name):
    answer = cell(f"USE {name}")
    assert answer.startswith(f"error: USE {name} can't run on this PC the way it runs in Databricks, because DuckDB "
                             f"keeps the name {name}"), answer
    assert cell("SELECT current_schema()") == [["default"]]


def test_system_information_schema_lists_the_sessions_tables(cell):
    assert cell("SELECT table_name FROM system.information_schema.tables WHERE table_name = 't'") == [["t"]]


def test_quotes_in_a_schema_name_stay_in_the_name(cell):
    answer = cell("USE SCHEMA `it's`")
    assert answer.startswith("error: [SCHEMA_NOT_FOUND] The schema `main`.`it's` cannot be found."), answer
