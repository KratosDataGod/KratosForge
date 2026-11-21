"""The table list doesn't wait for a running query: while a query holds the project's connection, a cursor on
that connection lists the tables (localdata/engine.py tables())."""

from __future__ import annotations

import threading
import time

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.fixture
def engine(local):
    e = local.engine(local.create("Tables wait")["id"])
    e.execute("CREATE TABLE sales USING DELTA AS SELECT 1 AS id, 10 AS amount")
    return e


def _listed_while_held(engine):
    listed: list = []
    engine._lock.acquire()  # as a running query holds it
    try:
        t = threading.Thread(target=lambda: listed.append(engine.tables()), daemon=True)
        started = time.monotonic()
        t.start()
        t.join(5)
        return listed, time.monotonic() - started
    finally:
        engine._lock.release()
        t.join(5)


def test_the_table_list_doesnt_wait_for_a_running_query(engine):
    engine.open()
    listed, seconds = _listed_while_held(engine)
    assert listed and {x["table"] for x in listed[0]} >= {"sales"}
    assert seconds < 4


def test_the_list_opens_the_project_when_nothing_is_open(engine):
    engine.close()
    assert {x["table"] for x in engine.tables()} >= {"sales"}
    assert engine._lock.acquire(blocking=False)  # and leaves the lock free
    engine._lock.release()


def test_with_nothing_open_to_share_it_waits_for_the_lock(engine):
    engine.close()
    listed: list = []
    engine._lock.acquire()
    t = threading.Thread(target=lambda: listed.append(engine.tables()), daemon=True)
    t.start()
    time.sleep(0.3)
    assert not listed  # no connection to take a cursor on, so it waits
    engine._lock.release()
    t.join(10)
    assert listed and {x["table"] for x in listed[0]} >= {"sales"}


def test_a_failed_open_leaves_the_lock_free(engine, monkeypatch):
    engine.close()

    def broken():
        raise RuntimeError("no database")

    monkeypatch.setattr(engine, "_open_locked", broken)
    with pytest.raises(RuntimeError):
        engine.tables()
    assert engine._lock.acquire(blocking=False)
    engine._lock.release()


def test_its_own_temporary_tables_are_listed_when_nothing_runs(engine):
    engine.execute("CREATE TEMP TABLE scratch AS SELECT 1 AS x")
    kinds = {x["table"]: x["kind"] for x in engine.tables()}
    assert kinds.get("scratch") == "temp table"
