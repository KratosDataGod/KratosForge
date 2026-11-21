"""tsql_merge.py: a MERGE INSERT with no VALUES is refused by name on a database table and a Delta table
(it inserted the source row on a database table, and DuckDB stopped on its parser's words for a column list),
and check()'s other edges: a target that isn't a table, a SET of a variable, and the table's columns found
by schema and name."""

from __future__ import annotations

import pytest
import sqlglot

from fabric_desktop.localdata import LocalData, tsql_merge

NO_VALUES = "A MERGE's INSERT needs VALUES (...) in T-SQL, so write INSERT (columns) VALUES (...)."


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Merge cov4")["id"])
    e.execute("CREATE TABLE dbo.tgt AS SELECT * FROM (VALUES (1, 'a')) v(id, v)")
    e.execute("CREATE TABLE dbo.dt USING DELTA AS SELECT * FROM (VALUES (1, 'a')) v(id, v)")
    e.execute("CREATE TABLE dbo.src AS SELECT * FROM (VALUES (2, 'B')) v(id, v)")
    yield e
    local.close_all()


@pytest.mark.parametrize("table", ["dbo.tgt", "dbo.dt"])
@pytest.mark.parametrize("insert", ["INSERT;", "INSERT (id, v);"])
def test_an_insert_without_values_is_refused_and_nothing_changes(engine, table, insert):
    with pytest.raises(ValueError) as caught:
        engine.execute(f"MERGE {table} AS t USING dbo.src AS s ON t.id = s.id WHEN NOT MATCHED THEN {insert}",
                       dialect="tsql")
    assert str(caught.value) == NO_VALUES
    assert [list(r) for r in engine.execute(f"SELECT id, v FROM {table} ORDER BY id")["rows"]] == [[1, "a"]]


@pytest.mark.parametrize("table", ["dbo.tgt", "dbo.dt"])
def test_an_insert_with_values_still_runs(engine, table):
    engine.execute(f"MERGE {table} AS t USING dbo.src AS s ON t.id = s.id "
                   "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);", dialect="tsql")
    assert [list(r) for r in engine.execute(f"SELECT id, v FROM {table} ORDER BY id")["rows"]] == [[1, "a"], [2, "B"]]


def parse(sql):
    return sqlglot.parse_one(sql, read="tsql")


def test_a_target_that_isnt_a_table_and_other_statements_are_left_alone():
    tsql_merge.check(parse("MERGE INTO (SELECT 1 AS a) AS t USING s ON t.a = s.a WHEN MATCHED THEN DELETE "
                           "WHEN MATCHED THEN DELETE WHEN MATCHED THEN DELETE;"))  # three clauses: not checked
    tsql_merge.check(parse("SELECT 1"))


def test_a_set_of_a_variable_isnt_a_column_and_is_left_as_it_is():
    tree = parse("MERGE t USING s ON t.a = s.a WHEN MATCHED THEN UPDATE SET @x = 1, t.a = s.a;")
    tsql_merge.check(tree)
    assert "SET @x = 1, a = s.a" in tree.sql(dialect="tsql")


def test_the_tables_columns_are_found_by_schema_and_name_case_blind():
    columns = {"Sales": {"T": {"x": "int"}}, "DBO": {"other": {"x": "int"}, "T": {"a": "int", "b": "int"}}}
    assert tsql_merge._columns_of(parse("MERGE t USING s ON 1 = 1 WHEN MATCHED THEN DELETE;").this, columns) == ["a", "b"]
    assert tsql_merge._columns_of(parse("MERGE sales.t USING s ON 1 = 1 WHEN MATCHED THEN DELETE;").this, columns) == ["x"]
    assert tsql_merge._columns_of(parse("MERGE dbo.none USING s ON 1 = 1 WHEN MATCHED THEN DELETE;").this, columns) is None
    assert tsql_merge._columns_of(parse("MERGE hr.t USING s ON 1 = 1 WHEN MATCHED THEN DELETE;").this, columns) is None
    with pytest.raises(ValueError, match=r"it has 2 \(a, b\), but VALUES gives 1"):
        tsql_merge.check(parse("MERGE t USING s ON t.a = s.a WHEN NOT MATCHED THEN INSERT VALUES (s.a);"), columns)
