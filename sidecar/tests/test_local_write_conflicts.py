"""WR-232: two writers at once on a project's tables end in one success and one named conflict, never a lost update.

A Fabric Warehouse runs every transaction with snapshot isolation. When two change the same table, the first to
commit wins and the other is rolled back with error 24556 ("Snapshot isolation transaction aborted due to update
conflict"); an INSERT adds files and rarely conflicts. On this PC one project runs one statement at a time, so two
sessions (two SQL tabs, a tab and a pipeline step) take turns. Another writer on a project's Delta table (a
notebook, another tool) is caught by delta-rs's commit check: the change that loses is refused by name, nothing
of it is committed, and the table then shows the winner's change.
"""

from __future__ import annotations

import threading

import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_dml
from fabric_desktop.localdata import LocalData

ROUNDS = 40


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Conflicts")["id"])
    e.execute("CREATE TABLE plain AS SELECT * FROM (VALUES (1, 0), (2, 0)) v(id, n)")
    e.execute("CREATE TABLE lake USING DELTA AS SELECT * FROM (VALUES (1, 0), (2, 0)) v(id, n)")
    yield e
    data.close_all()


def folder(engine):
    return engine.tables_dir / "dbo" / "lake"


def n_of(engine, table, id=1):
    return engine.execute(f"SELECT n FROM {table} WHERE id = {id}")["rows"][0][0]


def two_sessions(*bodies):
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
        t.join(120)
    assert not any(t.is_alive() for t in threads)
    return errors


# ---- two sessions in this app take turns: every change lands

@pytest.mark.parametrize("table", ["plain", "lake"])
@pytest.mark.parametrize("dialect", ["tsql", "duckdb"])
def test_two_sessions_adding_to_one_row_at_once_lose_no_update(engine, table, dialect):
    def add():
        for _ in range(ROUNDS if table == "plain" else ROUNDS // 4):
            engine.execute(f"UPDATE {table} SET n = n + 1 WHERE id = 1", dialect=dialect)
    assert two_sessions(add, add) == []
    assert n_of(engine, table) == 2 * (ROUNDS if table == "plain" else ROUNDS // 4)


def test_two_sessions_changing_and_reading_see_whole_changes_only(engine):
    seen: list[int] = []

    def change():
        for _ in range(ROUNDS):
            engine.execute("UPDATE plain SET n = n + 1", dialect="tsql")  # both rows in one statement

    def read():
        for _ in range(ROUNDS):
            a, b = (r[0] for r in engine.execute("SELECT n FROM plain ORDER BY id", dialect="tsql")["rows"])
            seen.append(a - b)
    assert two_sessions(change, read) == []
    assert set(seen) == {0}  # never one row changed and not the other


# ---- another writer on a project's Delta table

def other_writer_first(monkeypatch, engine, act):
    """delta_dml.apply, with another writer committing ``act`` after this statement read the table."""
    real = delta_dml.apply

    def apply(change, current, *args, **kwargs):
        act(DeltaTable(str(folder(engine))))
        return real(change, current, *args, **kwargs)
    monkeypatch.setattr(delta_dml, "apply", apply)


@pytest.mark.parametrize("act", [
    lambda t: t.update(updates={"n": "n + 100"}, predicate="id = 2"),  # another row of the same file
    lambda t: t.update(updates={"n": "n + 100"}, predicate="id = 1"),  # the same row
    lambda t: t.delete("id = 2"),
])
@pytest.mark.parametrize("dialect", ["tsql", "duckdb"])
def test_a_change_that_loses_to_another_writer_is_refused_by_name_and_commits_nothing(engine, monkeypatch, act,
                                                                                      dialect):
    before = DeltaTable(str(folder(engine))).version()
    other_writer_first(monkeypatch, engine, act)
    with pytest.raises(ValueError) as caught:
        engine.execute("UPDATE lake SET n = n + 1 WHERE id = 1", dialect=dialect)
    text = str(caught.value)
    assert "write-write conflict" in text and "Nothing was committed to" in text and "24556" in text, text
    assert "Help:" not in text and "isolation level can" not in text  # delta-rs's own advice isn't ours
    table = DeltaTable(str(folder(engine)))
    assert table.version() == before + 1  # only the other writer's commit
    assert "fabric_desktop.query" not in str(table.history(1)[0])


def test_after_a_conflict_the_table_shows_the_winners_change(engine):
    with pytest.MonkeyPatch.context() as mp:
        other_writer_first(mp, engine, lambda t: t.update(updates={"n": "n + 100"}, predicate="id = 1"))
        with pytest.raises(ValueError, match="write-write conflict"):
            engine.execute("UPDATE lake SET n = n + 1 WHERE id = 1", dialect="tsql")
    assert n_of(engine, "lake") == 100  # the winner's change, not ours, and not the old value
    engine.execute("UPDATE lake SET n = n + 1 WHERE id = 1", dialect="tsql")  # run again: it lands
    assert n_of(engine, "lake") == 101


def test_a_merge_that_loses_is_refused_the_same_way(engine):
    engine.execute("CREATE TABLE src AS SELECT 1 AS id, 5 AS n")
    with pytest.MonkeyPatch.context() as mp:
        other_writer_first(mp, engine, lambda t: t.delete("id = 1"))
        with pytest.raises(ValueError, match="write-write conflict"):
            engine.execute("MERGE INTO lake AS t USING src AS s ON t.id = s.id WHEN MATCHED THEN UPDATE SET n = s.n")
    assert engine.execute("SELECT id FROM lake ORDER BY id")["rows"] == [[2]]


def test_an_insert_beside_a_change_isnt_a_conflict_as_in_a_warehouse(engine):
    def append(t):
        write_deltalake(str(folder(engine)), t.to_pyarrow_table().slice(1, 1), mode="append")
    with pytest.MonkeyPatch.context() as mp:
        other_writer_first(mp, engine, append)
        engine.execute("UPDATE lake SET n = n + 1 WHERE id = 1", dialect="tsql")
    assert engine.execute("SELECT id, n FROM lake ORDER BY id, n")["rows"] == [[1, 1], [2, 0], [2, 0]]


@pytest.mark.parametrize("sql", ["DELETE FROM lake WHERE id = 1", "UPDATE lake SET n = 5 WHERE id = 1"])
def test_a_commit_refused_for_another_reason_keeps_delta_rss_reason(engine, sql):
    DeltaTable(str(folder(engine))).alter.set_table_properties({"delta.appendOnly": "true"})
    before = DeltaTable(str(folder(engine))).version()
    with pytest.raises(ValueError) as caught:
        engine.execute(sql, dialect="tsql")
    text = str(caught.value)
    assert "append-only" in text and "write-write conflict" not in text and "nothing was committed" in text, text
    assert DeltaTable(str(folder(engine))).version() == before


def test_a_failed_read_after_a_conflict_still_shows_the_conflict(engine, monkeypatch):
    other_writer_first(monkeypatch, engine, lambda t: t.update(updates={"n": "n + 100"}, predicate="id = 1"))

    def broken(*args, **kwargs):
        raise OSError("disk went away")
    monkeypatch.setattr(type(engine), "_register_own", broken)
    with pytest.raises(ValueError, match="write-write conflict"):
        engine.execute("UPDATE lake SET n = n + 1 WHERE id = 1", dialect="tsql")
