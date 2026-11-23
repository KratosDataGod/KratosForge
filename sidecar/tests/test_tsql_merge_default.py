"""WR-268: ``DEFAULT`` as a value in a local T-SQL MERGE is refused by name, on a database table (CREATE TABLE,
changed by DuckDB) and on one of the project's Delta tables (USING DELTA, changed by delta-rs).

Microsoft's MERGE page gives Fabric Data Warehouse ``INSERT [(column_list)] VALUES (values_list)`` only, with no
``DEFAULT VALUES``, and values_list is "constants, variables, or expressions". A Fabric Warehouse's CREATE TABLE
takes no DEFAULT, so its columns have no default to give. Here a database table's MERGE inserted the column's local
default (or NULL), a Delta table's stopped with delta-rs's "No field named DEFAULT", and ``SET v = DEFAULT`` was
read as a column called DEFAULT (error 207). Nothing is changed.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql_merge

START = [[1, "a"], [2, "b"]]
TABLES = ["dbo.tgt", "dbo.dt"]  # a database table, and one of the project's Delta tables


@pytest.fixture()
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Merge default")["id"])
    rows = "SELECT * FROM (VALUES (1, 'a'), (2, 'b')) v(id, v)"
    e.execute(f"CREATE TABLE dbo.tgt AS {rows}")
    e.execute(f"CREATE TABLE dbo.dt USING DELTA AS {rows}")
    e.execute("CREATE TABLE dbo.src AS SELECT * FROM (VALUES (2, 'B'), (4, 'D')) v(id, v)")
    yield e
    local.close_all()


def table(engine, name):
    return [list(r) for r in engine.execute(f"SELECT id, v FROM {name} ORDER BY id")["rows"]]


MERGE = "MERGE {t} AS t USING dbo.src AS s ON t.id = s.id "


@pytest.mark.parametrize("name", TABLES)
@pytest.mark.parametrize("rest", [
    "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, DEFAULT);",
    "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, default);",
    "WHEN NOT MATCHED THEN INSERT VALUES (s.id, DEFAULT);",
    "WHEN MATCHED THEN UPDATE SET v = DEFAULT;",
    "WHEN MATCHED THEN UPDATE SET v = 'x' WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, DEFAULT);",
])
def test_default_as_a_value_is_refused_by_name(engine, name, rest):
    with pytest.raises(ValueError) as caught:
        engine.execute((MERGE + rest).format(t=name), dialect="tsql")
    assert str(caught.value) == tsql_merge.NO_DEFAULT
    assert table(engine, name) == START  # nothing changed


@pytest.mark.parametrize("name", TABLES)
def test_a_temp_source_is_refused_the_same(engine, name):
    sql = ("SELECT * INTO #s FROM dbo.src; MERGE {t} AS t USING #s AS s ON t.id = s.id "
           "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, DEFAULT);").format(t=name)
    with pytest.raises(ValueError) as caught:
        engine.execute(sql, dialect="tsql")
    assert tsql_merge.NO_DEFAULT in str(caught.value)
    assert table(engine, name) == START


@pytest.mark.parametrize("name", TABLES)
def test_a_column_called_default_in_brackets_is_a_column(engine, name):
    engine.execute("CREATE TABLE dbo.src2 AS SELECT * FROM (VALUES (4, 'Q')) v(id, \"default\")")
    sql = ("MERGE {t} AS t USING dbo.src2 AS s ON t.id = s.id "
           "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.[default]);").format(t=name)
    engine.execute(sql, dialect="tsql")
    assert table(engine, name) == START + [[4, "Q"]]


@pytest.mark.parametrize("name", TABLES)
def test_null_is_what_to_write(engine, name):
    engine.execute((MERGE + "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, NULL);").format(t=name),
                   dialect="tsql")
    assert table(engine, name) == START + [[4, None]]


def test_the_message_names_the_rule():
    assert tsql_merge.NO_DEFAULT.startswith("DEFAULT as a value isn't taken in this MERGE.")
    assert ": because" not in tsql_merge.NO_DEFAULT
