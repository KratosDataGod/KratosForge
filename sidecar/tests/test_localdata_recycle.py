"""A persistent project's connection is closed and opened again after RECYCLE_AFTER queries, to give back the
native memory DuckDB keeps per file-reading query (localdata/engine.py _recycle_locked). Only between queries,
with everything the connection held made again, and never for a memory project or a connection holding
temporary objects."""

from __future__ import annotations

import threading

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import engine as engine_mod

AFTER = 5


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "RECYCLE_AFTER", AFTER)
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


def _engine(local, tmp_path, storage):
    data = tmp_path / f"files-{storage}"
    data.mkdir()
    (data / "orders.csv").write_text("id,amount\n1,10\n2,20\n3,30\n", encoding="utf-8")
    pid = local.create(f"Recycle {storage}", storage=storage)["id"]
    local.add_source(pid, str(data))
    e = local.engine(pid)
    e.execute("CREATE TABLE kept USING DELTA AS SELECT 7 AS n")
    return e


@pytest.fixture
def persistent(local, tmp_path):
    return _engine(local, tmp_path, "persistent")


@pytest.fixture
def memory(local, tmp_path):
    return _engine(local, tmp_path, "memory")


_READ = "SELECT sum(amount) AS total, count(*) AS n FROM orders"
_STATE = ("SELECT current_setting('TimeZone'), current_setting('search_path'), current_setting('threads'), "
          "current_setting('memory_limit'), current_setting('enable_external_access'), "
          "current_setting('allowed_directories')::VARCHAR")


def _rows(engine, sql):
    return engine.execute(sql)["rows"]


def test_the_connection_is_replaced_and_nothing_changes(persistent):
    persistent.project.settings.threads = 2
    persistent.apply_settings()
    first = persistent._con
    before = (_rows(persistent, _READ), _rows(persistent, _STATE), _rows(persistent, "SELECT n FROM kept"))
    for _ in range(AFTER * 2):
        assert _rows(persistent, _READ) == before[0]
    assert persistent._con is not first and persistent.reopened >= 1
    after = (_rows(persistent, _READ), _rows(persistent, _STATE), _rows(persistent, "SELECT n FROM kept"))
    assert after == before
    assert before[1][0][0] == "UTC" and before[1][0][2] == 2
    with pytest.raises(Exception, match="(?i)permission|disabled|not allowed"):  # still locked down
        persistent.execute("SELECT * FROM read_csv('/etc/hostname')")


def test_a_temporary_table_keeps_the_connection(persistent):
    persistent.execute("CREATE TEMP TABLE scratch AS SELECT 42 AS v")
    first = persistent._con
    for _ in range(AFTER * 3):
        persistent.execute(_READ)
    assert persistent._con is first and persistent.reopened == 0
    assert [list(r) for r in _rows(persistent, "SELECT v FROM scratch")] == [[42]]
    persistent.execute("DROP TABLE scratch")
    for _ in range(AFTER * 2):  # gone: it's reopened at the next try
        persistent.execute(_READ)
    assert persistent._con is not first


def test_a_memory_project_is_never_reopened(memory):
    first = memory._con
    before = _rows(memory, "SELECT n FROM kept")
    for _ in range(AFTER * 4):
        memory.execute(_READ)
    assert memory._con is first and memory.reopened == 0
    assert _rows(memory, "SELECT n FROM kept") == before


def test_a_streaming_query_in_another_thread_isnt_cut_off(persistent):
    persistent.execute("CREATE TABLE big AS SELECT range AS i FROM range(200000)")
    persistent.queries_since_open = engine_mod.RECYCLE_AFTER - 1  # the stream's query reaches the threshold
    first = persistent._con
    stream = persistent.execute_reader("SELECT i FROM big ORDER BY i", batch_size=1000)
    seen = 0
    got: list = []
    errors: list = []

    def others() -> None:  # queued behind the stream; the recycle may only happen once it's done
        try:
            for _ in range(AFTER * 2):
                got.append(_rows(persistent, _READ))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    t = threading.Thread(target=others, daemon=True)
    t.start()
    t.join(0.3)
    assert not got and persistent._con is first  # waiting for the stream, connection untouched
    for batch in stream:
        seen += len(batch)
    assert seen == 200000
    t.join(30)
    assert not errors and len(got) == AFTER * 2
    assert persistent._con is not first and persistent.reopened >= 1


def test_a_borrowed_cursor_keeps_the_connection(persistent):
    persistent.queries_since_open = engine_mod.RECYCLE_AFTER
    first = persistent._con
    persistent._borrowed = 1  # tables() reading with a cursor while a query held the lock
    persistent.execute(_READ)
    assert persistent._con is first
    persistent._borrowed = 0
    for _ in range(AFTER * 2):
        persistent.execute(_READ)
    assert persistent._con is not first
