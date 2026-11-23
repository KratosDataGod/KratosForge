"""WR-264: a local T-SQL MERGE binds its names as a Fabric Warehouse does, before anything runs, on a database
table (CREATE TABLE, changed by DuckDB) and on one of the project's Delta tables (USING DELTA, changed by delta-rs).

- A column neither side has is error 207 and an alias neither side has error 4104, even with a source that has two
  rows for one table row (which would be error 8672), and a refused MERGE changes nothing.
- An unqualified name in WHEN NOT MATCHED [BY TARGET] is the source's, and in WHEN NOT MATCHED BY SOURCE the
  table's, without regard to case; in ON and WHEN MATCHED one both sides have is error 209.
- On a Delta table, a subquery or a function delta-rs doesn't have (T-SQL's UPPER) in a WHEN clause is worked out
  by DuckDB as the source is read, when it reads only the source; one that reads the table is refused by name.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql_errors

START = [[1, "a"], [2, "b"], [3, "c"]]
TABLES = ["dbo.tgt", "dbo.dt"]  # a database table, and one of the project's Delta tables
TWO = "(SELECT 2 AS id, 'X' AS v UNION ALL SELECT 2, 'Y')"  # two source rows for table row 2: error 8672


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Merge names")["id"])
    rows = "SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, v)"
    e.execute(f"CREATE TABLE dbo.tgt AS {rows}")
    e.execute(f"CREATE TABLE dbo.dt USING DELTA AS {rows}")
    e.execute("CREATE TABLE dbo.src AS SELECT * FROM (VALUES (2, 'B'), (4, 'D')) v(id, v)")
    e.execute("CREATE TABLE dbo.other AS SELECT * FROM (VALUES (2, 'B', 7)) v(id, v, w)")
    yield e
    local.close_all()


def table(engine, name):
    return [list(r) for r in engine.execute(f"SELECT id, v FROM {name} ORDER BY id")["rows"]]


def run(engine, name, sql):
    engine.execute(sql.format(t=name), dialect="tsql")
    return table(engine, name)


def refused(engine, name, sql):
    with pytest.raises(ValueError) as caught:
        engine.execute(sql.format(t=name), dialect="tsql")
    assert table(engine, name) == START  # a refused MERGE changes nothing
    return str(caught.value)


INVALID = "Msg 207, Level 16: Invalid column name 'nope'."
MERGE = f"MERGE {{t}} AS t USING {TWO} AS s "


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("rest", [
    "ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.nope;",
    "ON t.id = s.id WHEN MATCHED THEN UPDATE SET nope = s.v;",
    "ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = t.nope;",
    "ON t.id = s.id WHEN MATCHED AND t.nope = 1 THEN DELETE;",
    "ON t.id = s.id WHEN MATCHED AND nope = 1 THEN DELETE;",
    "ON t.nope = s.id WHEN MATCHED THEN DELETE;",
    "ON t.id = s.nope WHEN MATCHED THEN DELETE;",
    "ON t.id = s.id WHEN NOT MATCHED THEN INSERT (id, nope) VALUES (s.id, s.v);",
    "ON t.id = s.id WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.nope);",
    "ON t.id = s.id WHEN NOT MATCHED BY SOURCE AND t.nope = 1 THEN DELETE;",
])
def test_an_unknown_column_is_error_207_before_error_8672(engine, name, rest):
    assert refused(engine, name, MERGE + rest) == INVALID


@pytest.mark.parametrize("name", TABLES)
def test_names_are_bound_without_regard_to_case(engine, name):
    message = refused(engine, name, MERGE + "ON T.ID = S.Id WHEN MATCHED AND T.NOPE = 1 THEN DELETE;")
    assert message == "Msg 207, Level 16: Invalid column name 'NOPE'."  # as written


@pytest.mark.parametrize("name", TABLES)
def test_a_column_both_sides_have_in_set_is_error_209_before_error_8672(engine, name):
    message = refused(engine, name, MERGE + "ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = v;")
    assert message == "Msg 209, Level 16: Ambiguous column name 'v'."


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize(("rest", "written"), [
    ("ON t.id = x.id WHEN MATCHED THEN DELETE;", "x.id"),
    ("ON t.id = s.id WHEN MATCHED AND X.Id = 1 THEN DELETE;", "X.Id"),
    ("ON t.id = s.id WHEN NOT MATCHED THEN INSERT (id, v) VALUES (x.id, s.v);", "x.id"),
])
def test_an_unknown_alias_is_error_4104(engine, name, rest, written):
    message = refused(engine, name, MERGE + rest)
    assert message == f'Msg 4104, Level 16: The multi-part identifier "{written}" could not be bound.'


@pytest.mark.parametrize("name", TABLES)
def test_the_tables_name_after_an_alias_is_error_4104(engine, name):
    """Once the table has an alias, a warehouse binds it by the alias only."""
    short = name.split(".")[1]
    message = refused(engine, name, f"MERGE {{t}} AS t USING dbo.src AS s ON {short}.id = s.id "
                                    "WHEN MATCHED THEN DELETE;")
    assert message == f'Msg 4104, Level 16: The multi-part identifier "{short}.id" could not be bound.'


def test_error_4104_is_numbered():
    message = 'The multi-part identifier "x.id" could not be bound.'
    assert tsql_errors.numbered(message) == f"Msg 4104, Level 16: {message}"
    assert tsql_errors.numbered(message + "\n") == message + "\n"  # fullmatch: a final line break isn't taken


@pytest.mark.parametrize("name", TABLES)
def test_two_source_rows_for_one_table_row_with_good_names_is_still_error_8672(engine, name):
    message = refused(engine, name, MERGE + "ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v;")
    assert "attempted to UPDATE or DELETE the same row more than once" in message


MIXED = ("MERGE {t} AS T USING dbo.other AS S ON T.ID = S.Id AND W = 7 "
         "WHEN MATCHED AND S.V <> 'x' THEN UPDATE SET V = UPPER(S.v) + 'b' "
         "WHEN NOT MATCHED BY SOURCE AND ID = 3 THEN UPDATE SET V = 'C' "
         "WHEN NOT MATCHED THEN INSERT (ID, V) VALUES (Id, W);")


@pytest.mark.parametrize("name", TABLES)
def test_each_when_clause_places_a_name_in_its_own_scope(engine, name):
    """It was refused with a false error 209 on ID, which both sides have, in the two NOT MATCHED clauses."""
    assert run(engine, name, MIXED) == [[1, "a"], [2, "Bb"], [3, "C"]]


@pytest.mark.parametrize("name", TABLES)
def test_an_unqualified_insert_value_is_the_sources(engine, name):
    rows = run(engine, name, "MERGE {t} AS t USING dbo.src AS s ON t.id = s.id "
                             "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (ID, V);")
    assert rows == START + [[4, "D"]]


@pytest.mark.parametrize("name", TABLES)
def test_an_unqualified_name_in_when_not_matched_by_source_is_the_tables(engine, name):
    rows = run(engine, name, "MERGE {t} AS t USING dbo.src AS s ON t.id = s.id "
                             "WHEN NOT MATCHED BY SOURCE AND id = 3 AND v = 'c' THEN UPDATE SET v = CONCAT(v, 'z');")
    assert rows == [[1, "a"], [2, "b"], [3, "cz"]]


@pytest.mark.parametrize("name", TABLES)
def test_a_temp_table_source_places_names_by_scope_too(engine, name):
    """WR-257's source known only when it runs, placed by the same rules."""
    rows = run(engine, name, "CREATE TABLE #o (id INT, v VARCHAR(5), w INT); INSERT INTO #o VALUES (2, 'B', 7); "
                             + MIXED.replace("dbo.other", "#o"))
    assert rows == [[1, "a"], [2, "Bb"], [3, "C"]]


@pytest.mark.parametrize("name", TABLES)
def test_a_column_both_sides_have_in_on_is_error_209(engine, name):
    message = refused(engine, name, "MERGE {t} AS t USING dbo.src AS s ON id = s.id WHEN MATCHED THEN DELETE;")
    assert message == "Msg 209, Level 16: Ambiguous column name 'id'."


@pytest.mark.parametrize("name", TABLES)
def test_a_column_both_sides_have_in_when_matched_is_still_error_209(engine, name):
    message = refused(engine, name, "MERGE {t} AS t USING dbo.src AS s ON t.id = s.id "
                                    "WHEN MATCHED AND ID = 2 THEN DELETE;")
    assert message == "Msg 209, Level 16: Ambiguous column name 'ID'."


# ---- a WHEN clause delta-rs can't run, on a Delta table

@pytest.mark.parametrize("name", TABLES)
def test_a_subquery_in_a_when_condition_runs(engine, name):
    """On a Delta table it stopped with delta-rs's Invalid function 'transform', or was refused for its subquery."""
    rows = run(engine, name, "MERGE {t} AS t USING dbo.src AS s ON t.id = s.id "
                             "WHEN MATCHED AND s.id IN (SELECT w - 5 FROM dbo.other) THEN UPDATE SET v = s.v;")
    assert rows == [[1, "a"], [2, "B"], [3, "c"]]


@pytest.mark.parametrize("name", TABLES)
def test_subqueries_and_upper_in_values_and_insert_conditions_run(engine, name):
    rows = run(engine, name, "MERGE {t} AS t USING dbo.src AS s ON t.id = s.id "
                             "WHEN MATCHED AND EXISTS (SELECT 1 FROM dbo.other AS o WHERE o.id = s.id) "
                             "THEN UPDATE SET v = (SELECT CONCAT(LOWER(o.v), '!') FROM dbo.other AS o WHERE o.id = s.id) "
                             "WHEN NOT MATCHED AND s.id NOT IN (SELECT id FROM dbo.other) "
                             "THEN INSERT (id, v) VALUES (s.id, UPPER(CONCAT(s.v, 'x')));")
    assert rows == [[1, "a"], [2, "b!"], [3, "c"], [4, "DX"]]


@pytest.mark.parametrize("name", TABLES)
def test_a_subquery_condition_that_holds_for_no_row_changes_nothing(engine, name):
    rows = run(engine, name, "MERGE {t} AS t USING dbo.src AS s ON t.id = s.id "
                             "WHEN MATCHED AND s.id IN (SELECT w FROM dbo.other) THEN DELETE;")
    assert rows == START


@pytest.mark.parametrize("sql", [
    "MERGE dbo.dt AS t USING dbo.src AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = UPPER(t.v);",
    ("MERGE dbo.dt AS t USING dbo.src AS s ON t.id = s.id "
     "WHEN MATCHED AND t.id IN (SELECT id FROM dbo.other) THEN DELETE;"),
    ("MERGE dbo.dt AS t USING dbo.src AS s ON t.id = s.id "
     "WHEN NOT MATCHED BY SOURCE AND t.id IN (SELECT 3) THEN DELETE;"),
])
def test_one_that_reads_the_delta_table_is_refused_by_name(engine, sql):
    message = refused(engine, "dbo.dt", sql)
    assert "a Delta table's MERGE (delta-rs) can't run that" in message
    assert "transform" not in message


@pytest.mark.parametrize(("sql", "want"), [
    ("MERGE {t} AS t USING dbo.src AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = UPPER(t.v);",
     [[1, "a"], [2, "B"], [3, "c"]]),
    (("MERGE {t} AS t USING dbo.src AS s ON t.id = s.id "
      "WHEN NOT MATCHED BY SOURCE AND t.id IN (SELECT 3) THEN DELETE;"), [[1, "a"], [2, "b"]]),
])
def test_the_same_runs_on_a_database_table(engine, sql, want):
    assert run(engine, "dbo.tgt", sql) == want
