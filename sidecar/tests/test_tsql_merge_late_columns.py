"""WR-257: a local T-SQL MERGE whose source's columns can't be known before it runs (a temporary table, a table the
script makes, a table-valued function) places a column named without ``t.`` or ``s.`` by the columns the source has
when the MERGE runs, before anything is written, as WR-253 places one over a known source. On a database table
(CREATE TABLE, changed by DuckDB) and on one of the project's Delta tables (USING DELTA, changed by delta-rs).
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData

START = [[1, "a"], [2, "b"], [3, "c"]]
TABLES = ["dbo.tgt", "dbo.dt"]  # a database table, and one of the project's Delta tables
TEMP = "CREATE TABLE #src (k INT, w VARCHAR(5)); INSERT INTO #src VALUES (2, 'B'), (4, 'D'); "


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Merge late columns")["id"])
    rows = "SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(k, v)"
    e.execute(f"CREATE TABLE dbo.tgt AS {rows}")
    e.execute(f"CREATE TABLE dbo.dt USING DELTA AS {rows}")
    yield e
    local.close_all()


def table(engine, name, columns="k, v"):
    return [list(r) for r in engine.execute(f"SELECT {columns} FROM {name} ORDER BY 1")["rows"]]


def run(engine, name, sql):
    engine.execute(sql.format(t=name), dialect="tsql")
    return table(engine, name)


def refused(engine, name, sql, words):
    with pytest.raises(ValueError) as caught:
        engine.execute(sql.format(t=name), dialect="tsql")
    assert words in str(caught.value)
    assert table(engine, name) == START
    return str(caught.value)


def made(_name):
    return "CREATE TABLE dbo.made (k INT, w VARCHAR(5)); INSERT INTO dbo.made VALUES (2, 'B'), (4, 'D'); "


# (what runs before the MERGE, its USING), for a source with columns k and w known only when the MERGE runs
LATE = {
    "temp table": (TEMP, "#src AS s"),
    "temp table through SELECT *": (TEMP, "(SELECT * FROM #src) AS s"),
    "table the script makes": (made(""), "dbo.made AS s"),
}


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("late", LATE)
def test_a_column_one_side_has_runs_where_that_side_has_a_row(engine, name, late):
    before, using = LATE[late]
    rows = run(engine, name, before + f"MERGE {{t}} AS t USING {using} ON t.k = s.k "
                                      "WHEN MATCHED AND v = 'b' THEN UPDATE SET v = w "
                                      "WHEN NOT MATCHED THEN INSERT (k, v) VALUES (s.k, w) "
                                      "WHEN NOT MATCHED BY SOURCE AND v = 'c' THEN DELETE;")
    assert rows == [[1, "a"], [2, "B"], [4, "D"]]


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("late", LATE)
def test_a_column_both_sides_have_is_error_209(engine, name, late):
    before, using = LATE[late]
    message = refused(engine, name, before + f"MERGE {{t}} AS t USING {using} ON t.k = s.k "
                                             "WHEN MATCHED AND k = 2 THEN DELETE;", "Ambiguous column name 'k'.")
    assert "failed: Msg 209, Level 16: Ambiguous column name 'k'." in message  # the MERGE is the script's last


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("late", LATE)
def test_a_table_column_in_when_not_matched_is_refused_by_name(engine, name, late):
    before, using = LATE[late]
    message = refused(engine, name, before + f"MERGE {{t}} AS t USING {using} ON t.k = s.k "
                                             "WHEN NOT MATCHED THEN INSERT (k, v) VALUES (s.k, v);",
                      "WHEN NOT MATCHED [BY TARGET] reads only the source's columns")
    assert "This one reads v, which only the table has" in message


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("late", LATE)
def test_a_source_column_in_when_not_matched_by_source_is_refused_by_name(engine, name, late):
    before, using = LATE[late]
    message = refused(engine, name, before + f"MERGE {{t}} AS t USING {using} ON t.k = s.k "
                                             "WHEN NOT MATCHED BY SOURCE AND w = 'B' THEN DELETE;",
                      "WHEN NOT MATCHED BY SOURCE reads only the table's columns")
    assert "This one reads w, which only the source has" in message


# A table-valued function: STRING_SPLIT and GENERATE_SERIES give a column called value.
@pytest.mark.parametrize("name", TABLES)
def test_a_table_valued_function_runs_by_its_columns(engine, name):
    rows = run(engine, name, "MERGE {t} AS t USING STRING_SPLIT('2,4', ',') AS s ON t.k = CAST(s.value AS INT) "
                             "WHEN MATCHED THEN UPDATE SET v = value "
                             "WHEN NOT MATCHED THEN INSERT (k, v) VALUES (CAST(value AS INT), value);")
    assert rows == [[1, "a"], [2, "2"], [3, "c"], [4, "4"]]


@pytest.mark.parametrize("name", TABLES)
def test_a_table_valued_functions_column_in_when_not_matched_by_source_is_refused(engine, name):
    message = refused(engine, name, "MERGE {t} AS t USING GENERATE_SERIES(2, 4) AS s ON t.k = s.value "
                                    "WHEN NOT MATCHED BY SOURCE AND value > 1 THEN DELETE;",
                      "WHEN NOT MATCHED BY SOURCE reads only the table's columns")
    assert "This one reads value, which only the source has" in message


@pytest.mark.parametrize("delta", [False, True])
def test_a_table_valued_functions_column_the_table_has_too_is_error_209(engine, delta):
    using = " USING DELTA" if delta else ""
    engine.execute(f"CREATE TABLE dbo.vt{using} AS SELECT * FROM (VALUES (2, 'b'), (3, 'c')) v(value, v)")
    with pytest.raises(ValueError, match="Ambiguous column name 'value'."):
        engine.execute("MERGE dbo.vt AS t USING GENERATE_SERIES(2, 4) AS s ON t.value = s.value "
                       "WHEN MATCHED AND value = 2 THEN DELETE;", dialect="tsql")
    assert table(engine, "dbo.vt", "value, v") == [[2, "b"], [3, "c"]]


@pytest.mark.parametrize("name", TABLES)
def test_a_source_known_before_it_runs_is_placed_with_the_same_words(engine, name):
    """A source known before the MERGE runs is placed then (WR-253), with the same words."""
    engine.execute("CREATE TABLE dbo.src (k INT, w VARCHAR(5))")
    late = refused(engine, name, TEMP + "MERGE {t} AS t USING #src AS s ON t.k = s.k "
                                        "WHEN NOT MATCHED BY SOURCE AND w = 'B' THEN DELETE;", "only the source has")
    known = refused(engine, name, "MERGE {t} AS t USING dbo.src AS s ON t.k = s.k "
                                  "WHEN NOT MATCHED BY SOURCE AND w = 'B' THEN DELETE;", "only the source has")
    assert late.startswith("Statement 3 of 3 (MERGE) failed: ") and known in late
