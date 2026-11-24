"""WR-213: local T-SQL MERGE and multi-row changes give a Fabric Warehouse's answers, or stop by name.

These tests cover:
- MERGE with WHEN MATCHED and WHEN NOT MATCHED BY TARGET and BY SOURCE, and the row count it returns;
- a table row that more than one source row would change, which T-SQL refuses for MERGE;
- UPDATE and DELETE written T-SQL's way, with the table named in FROM and joined to others;
- an UPDATE ... FROM that joins a table row to more than one row, whose answer is undefined in T-SQL;
- OUTPUT and @@ROWCOUNT, which are refused by name.

A database table (CREATE TABLE in T-SQL) is changed by DuckDB, and a Delta table by delta-rs.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData

DUPLICATE_MERGE = "The MERGE statement attempted to UPDATE or DELETE the same row more than once"
DUPLICATE_UPDATE = "The UPDATE would set a row of dbo.tgt from more than one row of its FROM"
TWO = "(SELECT 2 AS id, 'X' AS v UNION ALL SELECT 2, 'Y')"


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Changes")["id"])
    e.execute("CREATE TABLE dbo.tgt AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, v)")
    e.execute("CREATE TABLE dbo.src AS SELECT * FROM (VALUES (2, 'B'), (4, 'D')) v(id, v)")
    yield e
    local.close_all()


def run(engine, sql, dialect="tsql"):
    return [list(r) for r in engine.execute(sql, dialect=dialect)["rows"]]


def table(engine, name="dbo.tgt"):
    return run(engine, f"SELECT id, v FROM {name} ORDER BY id, v", "duckdb")


START = [[1, "a"], [2, "b"], [3, "c"]]


# (T-SQL change, the row count it returns, dbo.tgt after it)
CHANGES = [
 (("MERGE dbo.tgt AS t USING dbo.src AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v "
  "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);"), 2, [[1, "a"], [2, "B"], [3, "c"], [4, "D"]]),
 (("MERGE dbo.tgt AS t USING dbo.src AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v "
  "WHEN NOT MATCHED BY TARGET THEN INSERT (id, v) VALUES (s.id, s.v) WHEN NOT MATCHED BY SOURCE THEN DELETE;"),
  4, [[2, "B"], [4, "D"]]),
 ("MERGE dbo.tgt AS t USING dbo.src AS s ON t.id = s.id WHEN MATCHED THEN DELETE;", 1, [[1, "a"], [3, "c"]]),
 ("MERGE dbo.tgt AS t USING dbo.src AS s ON t.id = s.id WHEN NOT MATCHED BY SOURCE AND t.id > 2 THEN DELETE;",
  1, [[1, "a"], [2, "b"]]),
 # two source rows for one table row: fine when only one of them is acted on, and for inserts
 (f"MERGE dbo.tgt AS t USING {TWO} AS s ON t.id = s.id WHEN MATCHED AND s.v = 'X' THEN UPDATE SET v = s.v;",
  1, [[1, "a"], [2, "X"], [3, "c"]]),
 (("MERGE dbo.tgt AS t USING (SELECT 5 AS id, 'E' AS v UNION ALL SELECT 5, 'F') AS s ON t.id = s.id "
  "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);"), 2, START + [[5, "E"], [5, "F"]]),
 ("MERGE dbo.tgt AS t USING (SELECT 1 AS lo, 2 AS hi) AS s ON t.id BETWEEN s.lo AND s.hi WHEN MATCHED THEN DELETE;",
  2, [[3, "c"]]),
 # UPDATE and DELETE with the table named in FROM
 ("UPDATE t SET v = s.v FROM dbo.tgt t JOIN dbo.src s ON t.id = s.id", 1, [[1, "a"], [2, "B"], [3, "c"]]),
 ("UPDATE t SET v = s.v FROM dbo.tgt AS t INNER JOIN dbo.src AS s ON t.id = s.id WHERE s.v = 'nothing'", 0, START),
 ("UPDATE dbo.tgt SET v = s.v FROM dbo.tgt JOIN dbo.src s ON tgt.id = s.id", 1, [[1, "a"], [2, "B"], [3, "c"]]),
 ("UPDATE dbo.tgt SET v = s.v FROM dbo.src s WHERE tgt.id = s.id", 1, [[1, "a"], [2, "B"], [3, "c"]]),
 ("UPDATE t SET v = 'z' FROM dbo.tgt t WHERE t.id = 3", 1, [[1, "a"], [2, "b"], [3, "z"]]),
 ("UPDATE t SET v = x.v FROM dbo.tgt t JOIN dbo.src s ON t.id = s.id JOIN (SELECT 4 AS id, 'Q' AS v) x ON x.id = s.id + 2",
  1, [[1, "a"], [2, "Q"], [3, "c"]]),
 ("DELETE t FROM dbo.tgt t JOIN dbo.src s ON t.id = s.id", 1, [[1, "a"], [3, "c"]]),
 (f"DELETE t FROM dbo.tgt t JOIN {TWO} s ON t.id = s.id", 1, [[1, "a"], [3, "c"]]),
 ("DELETE dbo.tgt FROM dbo.src s WHERE dbo.tgt.id = s.id", 1, [[1, "a"], [3, "c"]]),
 # each statement returns the rows it changed, which T-SQL's @@ROWCOUNT would be
 ("INSERT INTO dbo.tgt VALUES (5, 'e'), (6, 'f')", 2, START + [[5, "e"], [6, "f"]]),
 ("INSERT INTO dbo.tgt SELECT id, v FROM dbo.src", 2, [[1, "a"], [2, "B"], [2, "b"], [3, "c"], [4, "D"]]),
 ("UPDATE dbo.tgt SET v = 'z' WHERE id > 9", 0, START),
 ("DELETE FROM dbo.tgt WHERE id >= 2", 2, [[1, "a"]]),
]


@pytest.mark.parametrize(("sql", "count", "after"), CHANGES, ids=[c[0] for c in CHANGES])
def test_each_change_gives_tsqls_rows_and_count(engine, sql, count, after):
    assert run(engine, sql) == [[count]]
    assert table(engine) == after


@pytest.mark.parametrize("sql", [
    f"MERGE dbo.tgt AS t USING {TWO} AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v;",
    f"MERGE dbo.tgt AS t USING {TWO} AS s ON t.id = s.id WHEN MATCHED THEN DELETE;",
    (f"MERGE dbo.tgt AS t USING {TWO} AS s ON t.id = s.id WHEN MATCHED AND s.v = 'Z' THEN DELETE "
    "WHEN MATCHED THEN UPDATE SET v = s.v;"),
    "MERGE dbo.tgt AS t USING (SELECT 2 AS id UNION ALL SELECT 3) AS s ON t.id >= s.id WHEN MATCHED THEN DELETE;",
])
def test_a_merge_that_would_change_a_row_twice_is_refused_and_changes_nothing(engine, sql):
    with pytest.raises(ValueError, match=DUPLICATE_MERGE):
        engine.execute(sql, dialect="tsql")
    assert table(engine) == START


def test_a_merge_that_would_change_a_row_twice_is_refused_in_duckdb_sql_too(engine):
    with pytest.raises(ValueError, match=DUPLICATE_MERGE):
        engine.execute(f"MERGE INTO dbo.tgt AS t USING {TWO} AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v",
                       dialect="duckdb")
    assert table(engine) == START


@pytest.mark.parametrize("sql", [
    f"UPDATE t SET v = s.v FROM dbo.tgt t JOIN {TWO} s ON t.id = s.id",
    f"UPDATE dbo.tgt SET v = s.v FROM {TWO} s WHERE dbo.tgt.id = s.id",
])
def test_an_update_from_that_joins_a_row_twice_is_refused_and_changes_nothing(engine, sql):
    with pytest.raises(ValueError, match=DUPLICATE_UPDATE):
        engine.execute(sql, dialect="tsql")
    assert table(engine) == START


@pytest.mark.parametrize(("sql", "words"), [
    ("UPDATE t SET v = s.v FROM dbo.tgt t LEFT JOIN dbo.src s ON t.id = s.id", "with an outer join"),
    ("DELETE t FROM dbo.tgt t RIGHT JOIN dbo.src s ON t.id = s.id", "with an outer join"),
    ("UPDATE t SET v = s.v FROM dbo.src s JOIN dbo.tgt t ON t.id = s.id", "when the table it changes isn't the first one in FROM"),
    ("UPDATE t SET v = s.v FROM dbo.tgt t JOIN dbo.src s USING (id)", "with JOIN ... USING"),
])
def test_a_joined_change_that_isnt_mapped_is_refused_by_name(engine, sql, words):
    with pytest.raises(ValueError, match="This T-SQL can't be run on the local engine: (UPDATE|DELETE) isn't mapped yet "
                                         + words.replace(".", r"\.").replace("(", r"\(")):
        engine.execute(sql, dialect="tsql")
    assert table(engine) == START


@pytest.mark.parametrize("sql", [
    "INSERT INTO dbo.tgt OUTPUT inserted.id VALUES (9, 'q')",
    "UPDATE dbo.tgt SET v = 'z' OUTPUT inserted.id, deleted.v WHERE id = 1",
    "DELETE FROM dbo.tgt OUTPUT deleted.id WHERE id = 1",
    "MERGE dbo.tgt AS t USING dbo.src AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v OUTPUT $action, inserted.id;",
])
def test_output_is_refused_by_name_and_changes_nothing(engine, sql):
    with pytest.raises(ValueError, match="This T-SQL can't be run on the local engine: OUTPUT isn't mapped yet"):
        engine.execute(sql, dialect="tsql")
    assert table(engine) == START


def test_a_column_called_output_still_runs(engine):
    assert run(engine, "SELECT id AS [output], 'OUTPUT' AS \"Output\" FROM dbo.tgt WHERE id = 1") == [[1, "OUTPUT"]]


def test_rowcount_is_the_rows_the_last_statement_changed(engine):
    # WR-705: @@ROWCOUNT runs now (it was refused by name)
    assert run(engine, "UPDATE dbo.tgt SET v = 'z' WHERE id < 3; SELECT @@ROWCOUNT") == [[2]]


def test_a_delta_table_keeps_its_own_checks(engine):
    engine.execute("CREATE TABLE dbo.dt USING DELTA AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(id, v)",
                   dialect="duckdb")
    # WR-244: in T-SQL a Delta table's MERGE is refused with T-SQL's error 8672, as a warehouse's table is
    with pytest.raises(ValueError, match=DUPLICATE_MERGE + r".* Nothing in dbo\.dt was changed"):
        engine.execute(f"MERGE dbo.dt AS t USING {TWO} AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v;",
                       dialect="tsql")
    with pytest.raises(ValueError, match="UPDATE ... FROM isn't supported on a Delta table"):
        engine.execute("UPDATE t SET v = s.v FROM dbo.dt t JOIN dbo.src s ON t.id = s.id", dialect="tsql")
    assert table(engine, "dbo.dt") == START


def test_joined_changes_are_written_duckdbs_way():
    from fabric_desktop.localdata.tsql import translate
    cases = {
        "UPDATE t SET v = s.v FROM dbo.tgt t JOIN dbo.src s ON t.id = s.id WHERE s.v > 1":
            "UPDATE dbo.tgt AS t SET v = s.v FROM dbo.src AS s WHERE (t.id = s.id) AND (s.v > 1)",
        "DELETE t FROM dbo.tgt t JOIN dbo.src s ON t.id = s.id":
            "DELETE FROM dbo.tgt AS t USING dbo.src AS s WHERE (t.id = s.id)",
        "DELETE dbo.tgt FROM dbo.src s WHERE dbo.tgt.id = s.id":
            "DELETE FROM dbo.tgt USING dbo.src AS s WHERE dbo.tgt.id = s.id",
        "UPDATE dbo.tgt SET v = 1 WHERE id = 2": "UPDATE dbo.tgt SET v = 1 WHERE id = 2",
    }
    for tsql, duckdb in cases.items():
        assert translate(tsql)[0] == [duckdb], tsql
