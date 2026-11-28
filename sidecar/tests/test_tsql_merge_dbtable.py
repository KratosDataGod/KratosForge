"""WR-259: every local T-SQL MERGE gets a Fabric Warehouse's column checks and SQL Server's error 8672 on a database
table (CREATE TABLE, changed by DuckDB) as on one of the project's Delta tables (USING DELTA, changed by delta-rs),
or is refused by name before anything runs.

The shapes that got past those checks before, each on a database table and a Delta table:
- a MERGE after WITH (a CTE as its source) ran on a database table with no 8672 check, so the last source row won,
  and stopped on a Delta table with DuckDB's binder words;
- a source with a column list (``USING (VALUES ...) AS s(id, v)``) stopped with DuckDB's binder words, or with a
  wrong "Invalid column name";
- a MERGE that delta_dml can't read (WHEN NOT MATCHED BY SOURCE THEN INSERT, a source without an alias, INSERT with
  fewer values than columns) ran on a database table as DuckDB took it, inserting rows T-SQL refuses.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.engine import TSQL_SAME_ROW_TWICE

TWO = "(SELECT 2 AS id, 'X' AS v UNION ALL SELECT 2, 'Y')"
START = [[1, "a"], [2, "b"], [3, "c"]]
TABLES = ["dbo.tgt", "dbo.dt"]  # a database table, and one of the project's Delta tables


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Merge on a database table")["id"])
    rows = "SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, v)"
    e.execute(f"CREATE TABLE dbo.tgt AS {rows}")
    e.execute(f"CREATE TABLE dbo.dt USING DELTA AS {rows}")
    e.execute("CREATE TABLE dbo.src AS SELECT * FROM (VALUES (2, 'B'), (4, 'D')) v(id, v)")
    e.execute("CREATE TABLE dbo.other AS SELECT * FROM (VALUES (2, 'B', 7)) v(id, v, w)")
    yield e
    local.close_all()


def table(engine, name):
    return [list(r) for r in engine.execute(f"SELECT id, v FROM {name} ORDER BY id, v")["rows"]]


def refused(engine, name, sql):
    with pytest.raises(ValueError) as caught:
        engine.execute(sql.format(t=name), dialect="tsql")
    assert table(engine, name) == START, "a refused MERGE changes nothing"
    return str(caught.value)


def ran(engine, name, sql):
    engine.execute(sql.format(t=name), dialect="tsql")
    return table(engine, name)


# A MERGE after WITH


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("clauses", ["WHEN MATCHED THEN UPDATE SET v = s.v;", "WHEN MATCHED THEN DELETE;"])
def test_a_cte_source_with_two_rows_for_one_table_row_is_refused_with_8672(engine, name, clauses):
    message = refused(engine, name, f"WITH d AS {TWO} MERGE {{t}} AS t USING d AS s ON t.id = s.id {clauses}")
    assert message == f"{TSQL_SAME_ROW_TWICE} Nothing in {name} was changed."


@pytest.mark.parametrize("name", TABLES)
def test_two_ctes_reading_each_other_are_checked_too(engine, name):
    message = refused(engine, name, f"WITH a AS {TWO}, b AS (SELECT * FROM a) "
                                    "MERGE {t} AS t USING b AS s ON t.id = s.id WHEN MATCHED THEN DELETE;")
    assert message.startswith(TSQL_SAME_ROW_TWICE)


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("using", ["d AS s", "(SELECT * FROM d WHERE id > 0) AS s", "d AS s(id, v)"])
def test_a_cte_source_runs_as_a_warehouse_runs_it(engine, name, using):
    sql = (f"WITH d AS (SELECT 2 AS id, 'Q' AS v UNION ALL SELECT 5, 'E') MERGE {{t}} AS t USING {using} "
           "ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);")
    assert ran(engine, name, sql) == [[1, "a"], [2, "Q"], [3, "c"], [5, "E"]]


@pytest.mark.parametrize("name", TABLES)
def test_a_cte_without_a_source_alias_runs(engine, name):
    sql = "WITH d AS (SELECT 2 AS id, 'Q' AS v) MERGE {t} AS t USING d ON t.id = d.id WHEN MATCHED THEN UPDATE SET v = d.v;"
    assert ran(engine, name, sql) == [[1, "a"], [2, "Q"], [3, "c"]]


@pytest.mark.parametrize("name", TABLES)
def test_a_merge_into_a_cte_is_refused_by_name(engine, name):
    message = refused(engine, name, "WITH c AS (SELECT * FROM {t}) MERGE c AS t USING dbo.src AS s ON t.id = s.id "
                                    "WHEN MATCHED THEN DELETE;")
    assert "MERGE into the CTE 'c'" in message


@pytest.mark.parametrize("name", TABLES)
def test_a_cte_read_outside_using_is_refused_by_name(engine, name):
    message = refused(engine, name, "WITH d AS (SELECT 2 AS id) MERGE {t} AS t USING dbo.src AS s ON t.id = s.id "
                                    "AND s.id IN (SELECT id FROM d) WHEN MATCHED THEN DELETE;")
    assert "reads the CTE 'd' outside USING" in message


# A source with a column list


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("using", ["(VALUES (2, 'Q'), (5, 'E')) AS s(id, v)",
                                   "(SELECT 2, 'Q' UNION ALL SELECT 5, 'E') AS s(id, v)",
                                   "dbo.src AS s(id, v)"])
def test_a_source_with_a_column_list_runs(engine, name, using):
    sql = f"MERGE {{t}} AS t USING {using} ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v;"
    assert ran(engine, name, sql) == [[1, "a"], [2, "B" if "src" in using else "Q"], [3, "c"]]


@pytest.mark.parametrize("name", TABLES)
def test_a_source_with_a_column_list_and_two_rows_for_one_table_row_is_refused_with_8672(engine, name):
    message = refused(engine, name, "MERGE {t} AS t USING (VALUES (2, 'X'), (2, 'Y')) AS s(id, v) ON t.id = s.id "
                                    "WHEN MATCHED THEN UPDATE SET v = s.v;")
    assert message == f"{TSQL_SAME_ROW_TWICE} Nothing in {name} was changed."


# What delta_dml can't read is refused on a database table too, not run as DuckDB takes it


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize(("sql", "words"), [
    ("MERGE {t} AS t USING dbo.src AS s ON t.id = s.id WHEN NOT MATCHED BY SOURCE THEN INSERT (id, v) VALUES (9, 'z');",
     "INSERT goes with WHEN NOT MATCHED"),
    ("MERGE {t} AS t USING (SELECT 2 AS id, 'Q' AS v) ON t.id = id WHEN MATCHED THEN UPDATE SET v = 'z';",
     "Give the MERGE source an alias"),
    ("MERGE {t} AS t USING dbo.src AS s ON t.id = s.id WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id);",
     "needs as many values as columns"),
])
def test_a_merge_delta_dml_cant_read_is_refused_by_name(engine, name, sql, words):
    assert words in refused(engine, name, sql)
