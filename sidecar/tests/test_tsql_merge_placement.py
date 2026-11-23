"""WR-253: a column named without ``t.`` or ``s.`` in a local T-SQL MERGE's WHEN clause is placed as a Fabric
Warehouse places it, on a database table (CREATE TABLE, changed by DuckDB) and on one of the project's Delta tables
(USING DELTA, changed by delta-rs).

These tests cover:
- a column only one side has, which runs where that side has a row, and is refused by name in WHEN NOT MATCHED
  [BY TARGET] (only the table has it) and WHEN NOT MATCHED BY SOURCE (only the source has it);
- a column both sides have, SQL Server's error 209 in WHEN MATCHED (WR-264: not in WHEN NOT MATCHED);
- the source's columns read from a table, a subquery, a CTE, ``SELECT *`` and ``x.*``, and the alias's column list;
- ``USING (VALUES ...) AS s(k, w)``, which stopped with DuckDB's binder words.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData

START = [[1, "a"], [2, "b"], [3, "c"]]
TABLES = ["dbo.tgt", "dbo.dt"]  # a database table, and one of the project's Delta tables


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Merge placement")["id"])
    rows = "SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(k, v)"
    e.execute(f"CREATE TABLE dbo.tgt AS {rows}")
    e.execute(f"CREATE TABLE dbo.dt USING DELTA AS {rows}")
    e.execute("CREATE TABLE dbo.src AS SELECT * FROM (VALUES (2, 'B'), (4, 'D')) v(k, w)")
    e.execute("CREATE TABLE dbo.extra AS SELECT * FROM (VALUES (2, 'x'), (4, 'y')) v(k, note)")
    yield e
    local.close_all()


def table(engine, name):
    return [list(r) for r in engine.execute(f"SELECT k, v FROM {name} ORDER BY k")["rows"]]


def run(engine, name, sql):
    engine.execute(sql.format(t=name), dialect="tsql")
    return table(engine, name)


def refused(engine, name, sql, words):
    with pytest.raises(ValueError) as caught:
        engine.execute(sql.format(t=name), dialect="tsql")
    assert words in str(caught.value)
    assert table(engine, name) == START
    return str(caught.value)


# Sources whose columns are k and w, written each way the source's columns can be known before the MERGE runs.
SOURCES = [
    "dbo.src",
    "(SELECT k, w FROM dbo.src)",
    "(SELECT * FROM dbo.src)",
    "(SELECT x.* FROM dbo.src AS x)",
    "(SELECT 2 AS k, 'B' AS w UNION ALL SELECT 4, 'D')",
    "(VALUES (2, 'B'), (4, 'D'))",  # with the alias's column list, below
]


def source(sql):
    return f"{sql} AS s(k, w)" if sql.startswith("(VALUES") else f"{sql} AS s"


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("using", SOURCES)
def test_a_column_one_side_has_runs_where_that_side_has_a_row(engine, name, using):
    rows = run(engine, name, f"MERGE {{t}} AS t USING {source(using)} ON t.k = s.k "
                             "WHEN MATCHED AND v = 'b' THEN UPDATE SET v = w "
                             "WHEN NOT MATCHED THEN INSERT (k, v) VALUES (s.k, w) "
                             "WHEN NOT MATCHED BY SOURCE AND v = 'c' THEN DELETE;")
    assert rows == [[1, "a"], [2, "B"], [4, "D"]]


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("using", SOURCES)
@pytest.mark.parametrize("clause", [
    "WHEN NOT MATCHED THEN INSERT (k, v) VALUES (s.k, v);",
    "WHEN NOT MATCHED BY TARGET AND v IS NULL THEN INSERT (k, v) VALUES (s.k, s.w);",
])
def test_a_table_column_in_when_not_matched_is_refused_by_name(engine, name, using, clause):
    message = refused(engine, name, f"MERGE {{t}} AS t USING {source(using)} ON t.k = s.k {clause}",
                      "WHEN NOT MATCHED [BY TARGET] reads only the source's columns")
    assert "This one reads v, which only the table has" in message


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("using", SOURCES)
@pytest.mark.parametrize("clause", [
    "WHEN NOT MATCHED BY SOURCE AND w = 'B' THEN DELETE;",
    "WHEN NOT MATCHED BY SOURCE THEN UPDATE SET v = w;",
])
def test_a_source_column_in_when_not_matched_by_source_is_refused_by_name(engine, name, using, clause):
    message = refused(engine, name, f"MERGE {{t}} AS t USING {source(using)} ON t.k = s.k {clause}",
                      "WHEN NOT MATCHED BY SOURCE reads only the table's columns")
    assert "This one reads w, which only the source has" in message


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("using", SOURCES)
@pytest.mark.parametrize("clause", [
    "WHEN MATCHED AND k = 2 THEN DELETE;",
    "WHEN MATCHED THEN UPDATE SET v = CAST(k AS VARCHAR(5));",
])
def test_a_column_both_sides_have_is_error_209(engine, name, using, clause):
    message = refused(engine, name, f"MERGE {{t}} AS t USING {source(using)} ON t.k = s.k {clause}",
                      "Ambiguous column name 'k'.")
    assert message.endswith("Ambiguous column name 'k'.")


@pytest.mark.parametrize("name", TABLES)
def test_a_cte_source_is_placed_by_its_columns(engine, name):
    cte = "WITH c AS (SELECT k, w FROM dbo.src) MERGE {t} AS t USING c AS s ON t.k = s.k "
    refused(engine, name, cte + "WHEN MATCHED AND k = 2 THEN DELETE;", "Ambiguous column name 'k'.")
    refused(engine, name, cte + "WHEN NOT MATCHED THEN INSERT (k, v) VALUES (s.k, v);", "which only the table has")


def test_a_cte_source_runs_on_a_database_table(engine):
    """On a Delta table a MERGE after WITH isn't run yet (DuckDB says it merges only into base tables)."""
    sql = "WITH c AS (SELECT k, w FROM dbo.src) MERGE {t} AS t USING c AS s ON t.k = s.k "
    assert run(engine, "dbo.tgt", sql + "WHEN MATCHED THEN UPDATE SET v = w;") == [[1, "a"], [2, "B"], [3, "c"]]


@pytest.mark.parametrize("name", TABLES)
def test_a_cte_with_a_column_list_is_placed_by_the_list(engine, name):
    cte = "WITH c (k, w) AS (SELECT 2, 'Q') MERGE {t} AS t USING c AS s ON t.k = s.k "
    refused(engine, name, cte + "WHEN NOT MATCHED BY SOURCE AND w = 'Q' THEN DELETE;", "which only the source has")
    refused(engine, name, cte + "WHEN MATCHED AND k = 2 THEN DELETE;", "Ambiguous column name 'k'.")


@pytest.mark.parametrize("name", TABLES)
def test_select_star_over_a_join_reads_both_tables_columns(engine, name):
    using = "(SELECT * FROM dbo.src AS a JOIN (SELECT k AS ek, note FROM dbo.extra) AS b ON a.k = b.ek) AS s"
    refused(engine, name, f"MERGE {{t}} AS t USING {using} ON t.k = s.k WHEN NOT MATCHED BY SOURCE AND note = 'x' "
                          "THEN DELETE;", "This one reads note, which only the source has")
    refused(engine, name, f"MERGE {{t}} AS t USING {using} ON t.k = s.k WHEN MATCHED AND k = 2 THEN DELETE;",
            "Ambiguous column name 'k'.")
    using = "(SELECT a.*, b.note FROM dbo.src AS a JOIN dbo.extra AS b ON a.k = b.k) AS s"
    assert run(engine, name, f"MERGE {{t}} AS t USING {using} ON t.k = s.k "
                             "WHEN MATCHED THEN UPDATE SET v = note;") == [[1, "a"], [2, "x"], [3, "c"]]


@pytest.mark.parametrize("name", TABLES)
def test_the_alias_column_list_names_the_source_columns(engine, name):
    sql = "MERGE {t} AS t USING (SELECT 2, 'Q') AS s(k, w) ON t.k = s.k "
    refused(engine, name, sql + "WHEN MATCHED AND k = 2 THEN DELETE;", "Ambiguous column name 'k'.")
    assert run(engine, name, sql + "WHEN MATCHED THEN UPDATE SET v = w;") == [[1, "a"], [2, "Q"], [3, "c"]]


@pytest.mark.parametrize("name", TABLES)
def test_values_with_a_column_list_runs(engine, name):
    """It stopped with DuckDB's binder words: the source lost its alias's column list."""
    rows = run(engine, name, "MERGE {t} AS t USING (VALUES (2, 'Q'), (5, 'E')) AS s(k, w) ON t.k = s.k "
                             "WHEN MATCHED THEN UPDATE SET v = s.w "
                             "WHEN NOT MATCHED THEN INSERT (k, v) VALUES (s.k, s.w);")
    assert rows == [[1, "a"], [2, "Q"], [3, "c"], [5, "E"]]


def test_a_column_inside_a_subquery_is_placed_by_the_subquery(engine):
    """Only on a database table: delta-rs refuses a subquery in a WHEN clause by name."""
    rows = run(engine, "dbo.tgt", "MERGE {t} AS t USING dbo.src AS s ON t.k = s.k "
                             "WHEN NOT MATCHED AND s.k IN (SELECT k FROM dbo.extra) "
                             "THEN INSERT (k, v) VALUES (s.k, s.w);")
    assert rows == [[1, "a"], [2, "b"], [3, "c"], [4, "D"]]


@pytest.mark.parametrize("name", TABLES)
def test_the_column_set_and_the_columns_inserted_are_the_tables(engine, name):
    """The names left of SET's = and in INSERT's column list are the table's, so they are never ambiguous."""
    rows = run(engine, name, "MERGE {t} AS t USING (SELECT k, w AS v FROM dbo.src) AS s ON t.k = s.k "
                             "WHEN MATCHED THEN UPDATE SET v = s.v "
                             "WHEN NOT MATCHED THEN INSERT (k, v) VALUES (s.k, s.v);")
    assert rows == [[1, "a"], [2, "B"], [3, "c"], [4, "D"]]
