"""WR-244: local T-SQL MERGE at its edges gives a Fabric Warehouse's rows, or stops by name, on a database table
(CREATE TABLE, changed by DuckDB) and on one of the project's Delta tables (USING DELTA, changed by delta-rs).

These tests cover:
- a table row that more than one source row would update or delete, refused with SQL Server's error 8672 word for
  word, and the other way round (one source row for several table rows), which T-SQL runs;
- duplicates that no WHEN MATCHED clause acts on, and duplicates that are only inserted, which T-SQL runs;
- the order of the WHEN clauses: the first whose condition holds takes the row, in whatever order they're written;
- the clause rules of Microsoft's MERGE page: at most two WHEN MATCHED and two WHEN NOT MATCHED BY SOURCE (the first
  with AND, one UPDATE and one DELETE), one WHEN NOT MATCHED [BY TARGET], the columns each clause may read, a
  column set once, no INSERT DEFAULT VALUES, and the semicolon a MERGE ends with.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.engine import TSQL_SAME_ROW_TWICE

# SQL Server's message 8672, as SQL Server writes it.
ERROR_8672 = ("The MERGE statement attempted to UPDATE or DELETE the same row more than once. This happens when a "
              "target row matches more than one source row. A MERGE statement cannot UPDATE/DELETE the same row of "
              "the target table multiple times. Refine the ON clause to ensure a target row matches at most one "
              "source row, or use the GROUP BY clause to group the source rows.")
TWO = "(SELECT 2 AS id, 'X' AS v UNION ALL SELECT 2, 'Y')"
START = [[1, "a"], [2, "b"], [3, "c"]]
TABLES = ["dbo.tgt", "dbo.dt"]  # a database table, and one of the project's Delta tables


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Merge edges")["id"])
    rows = "SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, v)"
    e.execute(f"CREATE TABLE dbo.tgt AS {rows}")
    e.execute(f"CREATE TABLE dbo.dt USING DELTA AS {rows}")
    e.execute("CREATE TABLE dbo.src AS SELECT * FROM (VALUES (2, 'B'), (4, 'D')) v(id, v)")
    yield e
    local.close_all()


def table(engine, name):
    return [list(r) for r in engine.execute(f"SELECT id, v FROM {name} ORDER BY id, v")["rows"]]


def merge(engine, name, rest, source="dbo.src"):
    return engine.execute(f"MERGE {name} AS t USING {source} AS s ON {rest}", dialect="tsql")


def refused(engine, name, sql, words):
    with pytest.raises(ValueError) as caught:
        engine.execute(sql.format(t=name), dialect="tsql")
    assert words in str(caught.value)
    assert table(engine, name) == START
    return str(caught.value)


def test_the_message_is_sql_servers_8672_word_for_word():
    assert TSQL_SAME_ROW_TWICE == ERROR_8672


# A table row matched by more than one source row that a WHEN MATCHED clause acts on: refused by T-SQL.
TWICE = [
    "WHEN MATCHED THEN UPDATE SET v = s.v;",
    "WHEN MATCHED THEN DELETE;",  # T-SQL refuses a delete too; Delta's own rule allows it
    "WHEN MATCHED AND s.v = 'X' THEN UPDATE SET v = s.v WHEN MATCHED THEN DELETE;",  # one updates, one deletes
    "WHEN MATCHED AND s.v IN ('X', 'Y') THEN UPDATE SET v = s.v;",
    "WHEN MATCHED THEN UPDATE SET v = s.v WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);",
]


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("clauses", TWICE)
def test_a_table_row_matched_by_two_source_rows_is_refused_with_8672(engine, name, clauses):
    message = refused(engine, name, f"MERGE {{t}} AS t USING {TWO} AS s ON t.id = s.id {clauses}", ERROR_8672)
    assert message == f"{ERROR_8672} Nothing in {name} was changed."


def test_a_dry_run_on_a_delta_table_gets_the_same_refusal(engine):
    with pytest.raises(ValueError, match="The MERGE statement attempted to UPDATE or DELETE the same row"):
        engine.change_delta(f"MERGE dbo.dt AS t USING {TWO} AS s ON t.id = s.id WHEN MATCHED THEN DELETE;",
                            dialect="tsql", dry_run=True)
    assert table(engine, "dbo.dt") == START


@pytest.mark.parametrize("name", TABLES)
def test_one_source_row_for_several_table_rows_changes_each_once(engine, name):
    # the other direction: one source row matches three table rows, and T-SQL updates each of them once
    merge(engine, name, "t.id BETWEEN s.lo AND s.hi WHEN MATCHED THEN UPDATE SET v = s.v;",
          "(SELECT 1 AS lo, 3 AS hi, 'Z' AS v)")
    assert table(engine, name) == [[1, "Z"], [2, "Z"], [3, "Z"]]


@pytest.mark.parametrize("name", TABLES)
def test_one_source_row_for_several_table_rows_deletes_each_once(engine, name):
    merge(engine, name, "t.id >= s.id WHEN MATCHED THEN DELETE;", "(SELECT 2 AS id)")
    assert table(engine, name) == [[1, "a"]]


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize(("clauses", "after"), [
    # only one of the two source rows is acted on, so no table row is changed twice
    ("WHEN MATCHED AND s.v = 'X' THEN UPDATE SET v = s.v;", [[1, "a"], [2, "X"], [3, "c"]]),
    ("WHEN MATCHED AND s.v = 'X' THEN UPDATE SET v = s.v WHEN MATCHED AND s.v = 'Z' THEN DELETE;",
     [[1, "a"], [2, "X"], [3, "c"]]),
    ("WHEN MATCHED AND s.v = 'Q' THEN DELETE;", START),
])
def test_duplicates_no_clause_acts_on_run(engine, name, clauses, after):
    merge(engine, name, f"t.id = s.id {clauses}", TWO)
    assert table(engine, name) == after


@pytest.mark.parametrize("name", TABLES)
def test_duplicate_source_rows_that_are_inserted_are_both_inserted(engine, name):
    merge(engine, name, "t.id = s.id WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);",
          "(SELECT 5 AS id, 'E' AS v UNION ALL SELECT 5, 'F')")
    assert table(engine, name) == START + [[5, "E"], [5, "F"]]


def test_a_delta_table_keeps_deltas_rule_in_duckdb_sql(engine):
    # Outside T-SQL a Delta table follows Delta: an unconditional DELETE with duplicate source rows runs.
    engine.execute(f"MERGE INTO dbo.dt AS t USING {TWO} AS s ON t.id = s.id WHEN MATCHED THEN DELETE")
    assert table(engine, "dbo.dt") == [[1, "a"], [3, "c"]]


# (the clauses, the table after): the first clause whose condition holds takes the row
ORDER = [
    ("WHEN MATCHED AND s.v = 'B' THEN DELETE WHEN MATCHED THEN UPDATE SET v = 'u';", [[1, "a"], [3, "c"]]),
    ("WHEN MATCHED AND s.v = 'B' THEN UPDATE SET v = 'u' WHEN MATCHED THEN DELETE;", [[1, "a"], [2, "u"], [3, "c"]]),
    ("WHEN MATCHED AND s.v = 'nothing' THEN UPDATE SET v = 'u' WHEN MATCHED THEN DELETE;", [[1, "a"], [3, "c"]]),
    ("WHEN MATCHED AND s.v = 'B' THEN UPDATE SET v = 'u' WHEN MATCHED AND s.v = 'B' THEN DELETE;",
     [[1, "a"], [2, "u"], [3, "c"]]),
    # WHEN NOT MATCHED BY SOURCE: rows 1 and 3; the first clause takes row 1, the second what's left of 3
    (("WHEN NOT MATCHED BY SOURCE AND t.id = 1 THEN UPDATE SET v = 'nb' WHEN NOT MATCHED BY SOURCE AND t.id < 9 "
     "THEN DELETE;"), [[1, "nb"], [2, "b"]]),
    (("WHEN NOT MATCHED BY SOURCE AND t.id < 9 THEN DELETE WHEN NOT MATCHED BY SOURCE AND t.id = 1 "
     "THEN UPDATE SET v = 'nb';"), [[2, "b"]]),
    # clauses in any order, each kind with its condition
    (("WHEN NOT MATCHED BY SOURCE AND t.id = 3 THEN DELETE WHEN NOT MATCHED BY TARGET AND s.v = 'D' "
     "THEN INSERT (id, v) VALUES (s.id, s.v) WHEN MATCHED AND t.v = 'b' THEN UPDATE SET t.v = s.v;"),
     [[1, "a"], [2, "B"], [4, "D"]]),
    ("WHEN NOT MATCHED BY TARGET AND s.v = 'nothing' THEN INSERT (id, v) VALUES (s.id, s.v);", START),
    ("WHEN NOT MATCHED THEN INSERT VALUES (s.id, s.v);", START + [[4, "D"]]),  # every column, in order
    ("WHEN MATCHED THEN UPDATE SET t.v = s.v;", [[1, "a"], [2, "B"], [3, "c"]]),  # SET with the table's alias
]


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize(("clauses", "after"), ORDER, ids=[c for c, _ in ORDER])
def test_the_first_clause_that_holds_takes_the_row(engine, name, clauses, after):
    merge(engine, name, f"t.id = s.id {clauses}")
    assert table(engine, name) == after


def test_an_unconditional_second_clause_takes_the_rest_on_a_database_table(engine):
    # On a Delta table an unconditional WHEN NOT MATCHED BY SOURCE needs confirm (local_change_delta).
    merge(engine, "dbo.tgt", "t.id = s.id WHEN NOT MATCHED BY SOURCE AND t.id = 1 THEN UPDATE SET v = 'nb' "
          "WHEN NOT MATCHED BY SOURCE THEN DELETE;")
    assert table(engine, "dbo.tgt") == [[1, "nb"], [2, "b"]]


REFUSED = [
    ("WHEN MATCHED THEN UPDATE SET v = 'u' WHEN MATCHED THEN DELETE;",
     "The first of two WHEN MATCHED clauses needs AND <condition> in T-SQL"),
    ("WHEN MATCHED AND s.id = 2 THEN UPDATE SET v = 'u1' WHEN MATCHED THEN UPDATE SET v = 'u2';",
     "Of two WHEN MATCHED clauses, T-SQL needs one to UPDATE and the other to DELETE, and these both UPDATE"),
    ("WHEN MATCHED AND s.id = 2 THEN DELETE WHEN MATCHED THEN DELETE;",
     "Of two WHEN MATCHED clauses, T-SQL needs one to UPDATE and the other to DELETE, and these both DELETE"),
    (("WHEN MATCHED AND s.id = 2 THEN UPDATE SET v = 'a' WHEN MATCHED AND s.id = 3 THEN DELETE "
     "WHEN MATCHED THEN DELETE;"), "A MERGE has at most two WHEN MATCHED clauses in T-SQL, and this one has 3"),
    ("WHEN NOT MATCHED BY SOURCE THEN DELETE WHEN NOT MATCHED BY SOURCE AND t.id = 1 THEN UPDATE SET v = 'nb';",
     "The first of two WHEN NOT MATCHED BY SOURCE clauses needs AND <condition>"),
    ("WHEN NOT MATCHED BY SOURCE AND t.id = 1 THEN DELETE WHEN NOT MATCHED BY SOURCE AND t.id = 3 THEN DELETE;",
     "Of two WHEN NOT MATCHED BY SOURCE clauses, T-SQL needs one to UPDATE and the other to DELETE"),
    (("WHEN NOT MATCHED BY SOURCE AND t.id = 1 THEN DELETE WHEN NOT MATCHED BY SOURCE AND t.id = 3 THEN UPDATE "
     "SET v = 'x' WHEN NOT MATCHED BY SOURCE AND t.id = 2 THEN DELETE;"),
     "A MERGE has at most two WHEN NOT MATCHED BY SOURCE clauses in T-SQL, and this one has 3"),
    (("WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v) WHEN NOT MATCHED BY TARGET AND s.id > 0 "
     "THEN INSERT (id, v) VALUES (s.id, 'q');"),
     "A MERGE has at most one WHEN NOT MATCHED [BY TARGET] clause in T-SQL, and this one has 2"),
    ("WHEN NOT MATCHED BY SOURCE AND s.id = 1 THEN DELETE;",
     "WHEN NOT MATCHED BY SOURCE reads only the table's columns in T-SQL"),
    ("WHEN NOT MATCHED BY SOURCE AND t.id = 1 THEN UPDATE SET v = s.v;",
     "This one reads s.v, and a Fabric Warehouse refuses it"),
    ("WHEN NOT MATCHED AND t.id IS NULL THEN INSERT (id, v) VALUES (s.id, s.v);",
     "WHEN NOT MATCHED [BY TARGET] reads only the source's columns in T-SQL"),
    ("WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, t.v);", "This one reads t.v"),
    ("WHEN MATCHED THEN UPDATE SET v = s.v, v = 'x';", "The column v is set more than once in one UPDATE SET"),
    ("WHEN MATCHED THEN UPDATE SET s.v = 'x';", "SET s.v names a column of s, but a MERGE's UPDATE sets only"),
    ("WHEN NOT MATCHED THEN INSERT (id, id) VALUES (s.id, s.id);", "The column id is named more than once"),
    ("WHEN NOT MATCHED THEN INSERT DEFAULT VALUES;", "A Fabric Warehouse's MERGE doesn't take INSERT DEFAULT VALUES"),
    ("WHEN NOT MATCHED THEN INSERT;", "A MERGE's INSERT needs VALUES (...) in T-SQL"),
    ("WHEN NOT MATCHED THEN INSERT (id, v);", "A MERGE's INSERT needs VALUES (...) in T-SQL"),
    ("WHEN MATCHED THEN UPDATE SET v = s.v", "A MERGE must end with a semicolon (;) in T-SQL"),
    ("WHEN MATCHED THEN DELETE\nSELECT 1;", "A MERGE must end with a semicolon (;) in T-SQL"),
]


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize(("clauses", "words"), REFUSED, ids=[c for c, _ in REFUSED])
def test_what_a_warehouse_refuses_is_refused_by_name_and_changes_nothing(engine, name, clauses, words):
    refused(engine, name, f"MERGE {{t}} AS t USING dbo.src AS s ON t.id = s.id {clauses}", words)


@pytest.mark.parametrize("name", TABLES)
def test_insert_values_without_columns_needs_a_value_for_each_column(engine, name):
    with pytest.raises(ValueError, match=r"INSERT VALUES without a column list gives a value for each column of .*"
                                         r"it has 2 \(id, v\), but VALUES gives 1"):
        merge(engine, name, "t.id = s.id WHEN NOT MATCHED THEN INSERT VALUES (s.id);")
    assert table(engine, name) == START


def test_the_semicolon_rule_reads_only_merge(engine):
    # A MERGE with its semicolon, followed by more in the batch or after GO, runs; a SELECT needs none.
    out = engine.execute("MERGE dbo.tgt AS t USING dbo.src AS s ON t.id = s.id WHEN MATCHED THEN DELETE;\n"
                         "SELECT count(*) FROM dbo.tgt\nGO\n"
                         "SELECT count(*) FROM dbo.tgt a INNER MERGE JOIN dbo.src b ON a.id = b.id", dialect="tsql")
    assert out["rows"] == [[0]]
    assert table(engine, "dbo.tgt") == [[1, "a"], [3, "c"]]
    with pytest.raises(ValueError, match=r"A MERGE must end with a semicolon \(;\)"):
        engine.execute("MERGE dbo.tgt AS t USING dbo.src AS s ON t.id = s.id WHEN MATCHED THEN DELETE\nGO\n"
                       "SELECT 1", dialect="tsql")
    with pytest.raises(ValueError, match=r"A MERGE must end with a semicolon \(;\)"):
        engine.execute("WITH c AS (SELECT 1 AS id) MERGE dbo.tgt AS t USING c ON t.id = c.id WHEN MATCHED "
                       "THEN DELETE", dialect="tsql")
