"""WR-243: DDL beside DML on one table ends in the right answer or a named refusal, never a stale read or a broken table.

A Fabric Warehouse runs each statement against a snapshot and takes a schema lock for DDL, so a statement sees a
table's columns and rows as of one moment, and a change that loses to another writer is refused by name (error 24556,
see test_local_write_conflicts.py). On this PC one project runs one statement at a time, so DDL and DML from two
sessions take turns. Another writer on a project's Delta table (a notebook, Spark on this PC, another tool, Explorer)
commits beside it: the next statement reads the table as that writer left it, and a statement that was running when
the table changed under it is refused by name with nothing of it kept.
"""

from __future__ import annotations

import shutil
import threading

import duckdb
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_dml
from fabric_desktop.localdata import LocalData

ROUNDS = 30


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Races")["id"])
    e.execute("CREATE TABLE plain AS SELECT * FROM (VALUES (1, 0), (2, 0)) v(id, n)")
    e.execute("CREATE TABLE lake USING DELTA AS SELECT * FROM (VALUES (1, 0), (2, 0)) v(id, n)")
    yield e
    data.close_all()


def folder(engine, table="lake"):
    return str(engine.tables_dir / "dbo" / table)


def rows(engine, sql, dialect="tsql"):
    return engine.execute(sql, dialect=dialect)["rows"]


def ints(**columns):
    return pa.table({k: pa.array(v, pa.int32()) for k, v in columns.items()})


def sessions(*bodies):
    errors: list[BaseException] = []
    start = threading.Barrier(len(bodies))

    def run(body):
        try:
            start.wait()
            body()
        except BaseException as exc:  # noqa: BLE001 - the test reads it
            errors.append(exc)
    threads = [threading.Thread(target=run, args=(b,)) for b in bodies]
    for t in threads:
        t.start()
    for t in threads:
        t.join(180)
    assert not any(t.is_alive() for t in threads)
    return errors


# ---- another writer commits: the very next statement reads the table as it now is

@pytest.mark.parametrize("dialect", ["tsql", "duckdb"])
def test_rows_another_writer_added_are_read_by_the_next_statement(engine, dialect):
    rows(engine, "SELECT count(*) FROM lake", dialect)  # read once, so a stale view would be kept
    write_deltalake(folder(engine), ints(id=[3], n=[0]), mode="append")
    assert rows(engine, "SELECT id FROM lake ORDER BY id", dialect) == [[1], [2], [3]]


def test_rows_another_writer_deleted_are_gone_from_the_next_statement(engine):
    rows(engine, "SELECT count(*) FROM lake")
    DeltaTable(folder(engine)).delete("id = 1")
    assert rows(engine, "SELECT id FROM lake") == [[2]]


def test_a_vacuum_by_another_writer_doesnt_break_the_next_read(engine):
    """Before WR-243 the view kept the old file list, and after a VACUUM a read failed on files no longer there."""
    rows(engine, "SELECT count(*) FROM lake")
    DeltaTable(folder(engine)).delete("id = 1")
    DeltaTable(folder(engine)).optimize.compact()
    DeltaTable(folder(engine)).vacuum(retention_hours=0, enforce_retention_duration=False, dry_run=False)
    assert rows(engine, "SELECT id, n FROM lake") == [[2, 0]]


def test_a_column_another_writer_added_is_read_by_the_next_statement(engine):
    rows(engine, "SELECT count(*) FROM lake")
    write_deltalake(folder(engine), pa.table({"id": pa.array([3], pa.int32()), "n": pa.array([0], pa.int32()),
                                              "note": ["new"]}), mode="append", schema_mode="merge")
    assert rows(engine, "SELECT id, note FROM lake ORDER BY id") == [[1, None], [2, None], [3, "new"]]
    assert [c["name"] for t in engine.tables() if t["table"] == "lake" for c in t["columns"]] == ["id", "n", "note"]


def test_a_table_another_writer_replaced_with_new_columns_is_read_as_replaced(engine):
    rows(engine, "SELECT count(*) FROM lake")
    write_deltalake(folder(engine), pa.table({"k": ["a"]}), mode="overwrite", schema_mode="overwrite")
    assert rows(engine, "SELECT k FROM lake") == [["a"]]
    with pytest.raises(duckdb.Error, match="n"):
        rows(engine, "SELECT n FROM lake")


def test_a_table_another_tool_made_in_tables_is_read_without_a_refresh(engine):
    write_deltalake(folder(engine, "made"), ints(id=[7]))
    assert rows(engine, "SELECT id FROM made") == [[7]]


def test_a_table_another_tool_deleted_is_gone_not_read_from_files_that_are_gone(engine):
    rows(engine, "SELECT count(*) FROM lake")
    shutil.rmtree(folder(engine))
    with pytest.raises(duckdb.Error, match="lake does not exist"):
        rows(engine, "SELECT * FROM lake")
    assert "lake" not in [t["table"] for t in engine.tables()]


def test_the_apps_own_writes_dont_read_the_table_again_needlessly(engine, monkeypatch):
    calls = []
    real = type(engine)._register_own
    monkeypatch.setattr(type(engine), "_register_own", lambda self, *a, **k: calls.append(a[2]) or real(self, *a, **k))
    rows(engine, "UPDATE lake SET n = n + 1 WHERE id = 1")
    assert calls == ["lake"]  # once, after its own commit
    rows(engine, "SELECT n FROM lake WHERE id = 1")
    assert calls == ["lake"]  # and not again on the next statement


# ---- another writer's DDL while a change runs

def other_writer_first(monkeypatch, act):
    real = delta_dml.apply

    def apply(change, current, *args, **kwargs):
        act()
        return real(change, current, *args, **kwargs)
    monkeypatch.setattr(delta_dml, "apply", apply)


@pytest.mark.parametrize("act", [
    pytest.param(lambda f: write_deltalake(f, pa.table({"id": pa.array([3], pa.int32()), "n": pa.array([0], pa.int32()),
                                                        "c": ["x"]}), mode="append", schema_mode="merge"),
                 id="column added"),
    pytest.param(lambda f: write_deltalake(f, pa.table({"id": pa.array([1], pa.int64()), "z": ["q"]}),
                                           mode="overwrite", schema_mode="overwrite"), id="replaced, new columns"),
    pytest.param(lambda f: DeltaTable(f).alter.set_table_properties({"delta.logRetentionDuration": "interval 30 days"}),
                 id="property set"),
    pytest.param(lambda f: DeltaTable(f).alter.add_constraint({"pos": "n >= 0"}), id="constraint added"),
])
def test_a_change_beside_another_writers_ddl_is_refused_by_name_and_keeps_theirs(engine, monkeypatch, act):
    before = DeltaTable(folder(engine)).version()
    other_writer_first(monkeypatch, lambda: act(folder(engine)))
    with pytest.raises(ValueError, match="Nothing was committed to dbo.lake.*24556"):
        rows(engine, "UPDATE lake SET n = n + 1 WHERE id = 1")
    monkeypatch.undo()
    table = DeltaTable(folder(engine))
    assert table.version() == before + 1  # only the other writer's commit
    assert "fabric_desktop.query" not in str(table.history(1)[0])
    rows(engine, "SELECT * FROM lake")  # readable as the other writer left it


def test_a_change_to_a_table_another_writer_dropped_meanwhile_is_refused_by_name(engine, monkeypatch):
    other_writer_first(monkeypatch, lambda: shutil.rmtree(folder(engine)))
    with pytest.raises(ValueError) as caught:
        rows(engine, "UPDATE lake SET n = 1 WHERE id = 1")
    text = str(caught.value)
    assert "dbo.lake was dropped by another writer" in text and "Invalid object name 'dbo.lake'" in text, text
    monkeypatch.undo()
    assert "lake" not in [t["table"] for t in engine.tables()]


# ---- DDL on a project Delta table that isn't mapped is refused by name, not in DuckDB's words

@pytest.mark.parametrize("dialect", ["tsql", "duckdb"])
@pytest.mark.parametrize("sql", ["ALTER TABLE lake ADD c int", "ALTER TABLE dbo.lake DROP COLUMN n",
                                 "ALTER TABLE lake ADD COLUMN c int"])
def test_alter_table_on_a_delta_table_is_refused_with_the_way_to_do_it(engine, sql, dialect):
    if dialect == "tsql" and "COLUMN c" in sql:
        sql = sql.replace("COLUMN ", "")
    with pytest.raises(ValueError, match="isn't mapped yet. Write it again with the columns you want: CREATE OR "
                                         "REPLACE TABLE"):
        rows(engine, sql, dialect)
    assert rows(engine, "SELECT * FROM lake ORDER BY id") == [[1, 0], [2, 0]]


@pytest.mark.parametrize("dialect", ["tsql", "duckdb"])
def test_truncate_on_a_delta_table_is_refused_with_delete_named(engine, dialect):
    with pytest.raises(ValueError, match="DELETE FROM dbo.lake removes every row"):
        rows(engine, "TRUNCATE TABLE dbo.lake", dialect)
    assert len(rows(engine, "SELECT * FROM lake")) == 2


def test_the_way_named_for_alter_works(engine):
    rows(engine, "CREATE OR REPLACE TABLE lake USING DELTA AS SELECT id, n, 1 AS c FROM lake", "duckdb")
    assert rows(engine, "SELECT id, n, c FROM lake ORDER BY id") == [[1, 0, 1], [2, 0, 1]]


def test_ddl_on_a_plain_table_still_runs(engine):
    rows(engine, "ALTER TABLE plain ADD c int")
    rows(engine, "TRUNCATE TABLE plain")
    assert rows(engine, "SELECT count(*) FROM plain") == [[0]]


# ---- two sessions in this app: DDL and DML take turns, and every statement sees a whole table

def test_columns_added_and_dropped_beside_inserts_lose_no_row(engine):
    def ddl():
        for i in range(ROUNDS):
            rows(engine, f"ALTER TABLE plain ADD c{i} int")
            rows(engine, f"ALTER TABLE plain DROP COLUMN c{i}")

    def dml():
        for i in range(ROUNDS):
            rows(engine, f"INSERT INTO plain (id, n) VALUES ({10 + i}, {i})")
    assert sessions(ddl, dml) == []
    assert rows(engine, "SELECT count(*), CAST(sum(n) AS int) FROM plain") == [[2 + ROUNDS, sum(range(ROUNDS))]]
    assert [c["name"] for t in engine.tables() if t["table"] == "plain" for c in t["columns"]] == ["id", "n"]


def test_a_table_dropped_and_made_beside_reads_is_read_whole_or_named_missing(engine):
    seen: list[int] = []
    missing: list[str] = []

    def ddl():
        for _ in range(ROUNDS):
            rows(engine, "DROP TABLE plain")
            rows(engine, "CREATE TABLE plain AS SELECT * FROM (VALUES (1, 0), (2, 0), (3, 0)) v(id, n)")

    def read():
        for _ in range(ROUNDS):
            try:
                seen.append(rows(engine, "SELECT count(*) FROM plain")[0][0])
            except duckdb.CatalogException as exc:
                missing.append(str(exc))
    assert sessions(ddl, read) == []
    assert set(seen) <= {2, 3}
    assert all("plain does not exist" in m for m in missing)
    assert rows(engine, "SELECT count(*) FROM plain") == [[3]]


def test_a_delta_table_replaced_beside_changes_keeps_every_change_in_order(engine):
    def replace():
        for _ in range(ROUNDS // 3):
            rows(engine, "CREATE OR REPLACE TABLE lake USING DELTA AS SELECT id, n FROM lake", "duckdb")

    def change():
        for _ in range(ROUNDS // 3):
            rows(engine, "UPDATE lake SET n = n + 1 WHERE id = 1")
    assert sessions(replace, change) == []
    assert rows(engine, "SELECT n FROM lake WHERE id = 1") == [[ROUNDS // 3]]
    history = DeltaTable(folder(engine)).history()
    assert len(history) == 1 + 2 * (ROUNDS // 3)  # every statement one version, none lost


def test_ddl_waits_for_a_streamed_read_and_the_read_sees_the_table_before_it(engine):
    reader = engine.execute_reader("SELECT id, n FROM plain ORDER BY id", dialect="tsql")
    done = threading.Event()

    def ddl():
        rows(engine, "ALTER TABLE plain ADD c int")
        done.set()
    t = threading.Thread(target=ddl)
    t.start()
    assert not done.wait(0.5)  # the read holds the project; the ALTER waits its turn
    read = reader.read_all()
    reader.close()
    t.join(60)
    assert done.is_set()
    assert read.column_names == ["id", "n"] and read.num_rows == 2
    assert rows(engine, "SELECT * FROM plain ORDER BY id") == [[1, 0, None], [2, 0, None]]
