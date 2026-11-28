"""WR-225: a large result never comes out short without saying so, on a warehouse or in a local project.

A warehouse run streams every row into its snapshot, a page of the driver's rows at a time; the stand-in
driver here returns 10 million rows across 153 pages. Every row is then read back by paging and by export.
A run that Stop, the time limit or its process ending cuts off part way keeps no rows and says why. A
snapshot on this PC that holds fewer rows than the run wrote is never shown, sorted, profiled, compared or
exported as if it were whole. ``local_query`` and the deprecated ``local_query_status`` answer with the
counts and what to change whenever they hold fewer rows than the query returned, including a limit above
the most one answer holds. Nothing reaches Fabric.
"""

from __future__ import annotations

import json
import sys
import textwrap
import time

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_desktop import sql_exec, warehouse
from fabric_desktop.localdata.engine import MAX_QUERY_ROWS, shown_rows_note
from tests.test_warehouse_queries import SIDECAR, FakeFabric, limits, start, wait

TEN_MILLION = 10_000_000

# The real sql_worker with the driver swapped for one that serves ``n`` whole numbers, a fetchmany page at a
# time, never all at once. STOPAT and DIEAT make it hang or end its process after the first page.
WORKER = textwrap.dedent('''
    import os, sys, time
    sys.path.insert(0, {sidecar!r})
    from fabric_desktop import sql_worker, warehouse

    class Cursor:
        def __init__(self):
            self.description, self.rowcount, self.sent, self.n, self.q = None, -1, 0, 0, ""
        def nextset(self):
            return False
        def execute(self, q):
            self.q = q
            self.description = [("n", int, None, None, 19, 0, True)]
            self.n = int(q.split("TOP ")[1].split()[0])
        def fetchmany(self, size):
            if self.sent and "STOPAT" in self.q:
                time.sleep(60)
            if self.sent and "DIEAT" in self.q:
                os._exit(9)
            page = [(i,) for i in range(self.sent, min(self.sent + size, self.n))]
            self.sent += len(page)
            return page

    class Connection:
        timeout = 0
        def __init__(self, *a):
            pass
        def cursor(self):
            return Cursor()
        def rollback(self):
            pass
        def commit(self):
            pass
        def close(self):
            pass

    warehouse.connect = lambda server, database, token, login_timeout=60: Connection()
    sql_worker.main()
''')


def numbers(n, marker=""):
    return f"SELECT TOP {n} n FROM dbo.numbers{' -- ' + marker if marker else ''}"


def manager_for(tmp_path, **limit_changes):
    script = tmp_path / "worker.py"
    script.write_text(WORKER.format(sidecar=str(SIDECAR)), encoding="utf-8")
    fabric = FakeFabric()
    return sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "STORAGE", audit=lambda *a: None,
                                  limits=limits(**limit_changes), command=[sys.executable, str(script)],
                                  warehouse_token=lambda: "SQL-TOKEN", downloads=lambda: tmp_path / "Downloads",
                                  resolve_warehouse=lambda ref: warehouse.resolve(fabric, ref))


@pytest.fixture
def runs(tmp_path):
    manager = manager_for(tmp_path)
    yield manager
    manager.shutdown()


def finish_export(manager, export_id, seconds=240):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        status = manager.export_status(export_id)
        if status["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("the export is still running")


# ---- 10 million rows from a warehouse, across the driver's pages

@pytest.fixture(scope="module")
def ten_million(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("ten-million")
    manager = manager_for(tmp_path)
    status = wait(manager, start(manager, numbers(TEN_MILLION))["id"], seconds=600)
    yield manager, status
    manager.shutdown()


def test_every_one_of_ten_million_warehouse_rows_reaches_the_snapshot(ten_million):
    manager, status = ten_million
    assert status["state"] == "completed", status.get("error")
    assert status["rows"] == TEN_MILLION
    pages = -(-TEN_MILLION // warehouse.BATCH_ROWS)
    with pa.memory_map(str(manager._get(status["id"]).snapshot)) as source:
        reader = pa.ipc.open_file(source)
        assert reader.num_record_batches == pages == 153
        assert sum(reader.get_batch(i).num_rows for i in range(pages)) == TEN_MILLION


def test_paging_reaches_the_last_of_ten_million_rows_and_across_the_drivers_pages(ten_million):
    manager, status = ten_million
    run = status["id"]
    first = manager.page(run, 0, 1000)
    assert first["rows"][0] == [0] and first["total"] == first["all_rows"] == TEN_MILLION and first["has_next"]
    edge = warehouse.BATCH_ROWS * 7 - 3  # three rows before a driver page ends, seven after
    across = manager.page(run, edge, 10)
    assert across["rows"] == [[n] for n in range(edge, edge + 10)]
    last = manager.page(run, TEN_MILLION - 1000, 1000)
    assert last["rows"][-1] == [TEN_MILLION - 1] and len(last["rows"]) == 1000 and not last["has_next"]
    # A page asked for past the end shows the last row, and a page bigger than Settings › SQL allows is its top.
    assert manager.page(run, TEN_MILLION + 5, 10)["rows"] == [[TEN_MILLION - 1]]
    big = manager.page(run, 0, 50_000)
    assert big["limit"] == 10_000 and len(big["rows"]) == 10_000 and big["has_next"]


def test_an_export_of_ten_million_rows_holds_every_row(ten_million):
    manager, status = ten_million
    done = finish_export(manager, manager.export(status["id"], "parquet", "numbers")["id"])
    assert done["state"] == "completed", done["error"]
    assert done["rows"] == done["total"] == TEN_MILLION
    table = pq.read_table(done["path"])
    assert table.num_rows == TEN_MILLION
    assert table.column(0)[0].as_py() == 0 and table.column(0)[-1].as_py() == TEN_MILLION - 1


def test_ten_million_rows_refuse_what_works_on_fewer_by_name_and_count(ten_million):
    manager, status = ten_million
    with pytest.raises(ValueError, match="up to 5,000,000 rows, and this one has 10,000,000"):
        manager.page(status["id"], 0, 100, sort=[{"column": 0, "desc": True}])
    with pytest.raises(ValueError, match="up to 5,000,000 rows, and this one has 10,000,000"):
        manager.column_stats(status["id"], 0)
    with pytest.raises(ValueError, match="Excel holds at most 1,048,575 rows and this result has 10,000,000"):
        manager.export(status["id"], "xlsx")


# ---- cut off part way: no rows, and why

@pytest.mark.parametrize("how", ["stop", "time limit", "process ends"])
def test_a_warehouse_run_cut_off_part_way_keeps_no_rows_and_says_why(tmp_path, how):
    manager = manager_for(tmp_path, sql_time_limit_minutes=0.02 if how == "time limit" else 10)
    try:
        marker = "DIEAT" if how == "process ends" else "STOPAT"
        run = start(manager, numbers(TEN_MILLION, marker))["id"]
        if how == "stop":
            wait(manager, run, states=("running",))
            time.sleep(1.5)  # the first page is written; the second never comes
            manager.cancel(run)
        status = wait(manager, run, seconds=120)
        expected = {"stop": ("cancelled", "Stopped."), "time limit": ("timed_out", "Stopped after the time limit"),
                    "process ends": ("failed", "stopped unexpectedly")}[how]
        assert status["state"] == expected[0] and expected[1] in status["error"]
        assert status["rows"] == 0 and not status["snapshot"]
        with pytest.raises(ValueError, match=f"The query is {expected[0].replace('_', ' ')}, so there are no rows"):
            manager.page(run)
        with pytest.raises(ValueError, match="so there is no result to export"):
            manager.export(run, "csv")
    finally:
        manager.shutdown()


# ---- a snapshot that holds fewer rows than the run wrote

def _cut_short(manager, run_id):
    """Point the run at a copy of its snapshot that holds the first batch only, as a damaged or half-copied
    file would. The run moves to a folder of its own, since Windows won't replace a file another handle has open."""
    run = manager._get(run_id)
    with pa.OSFile(str(run.snapshot)) as source:
        reader = pa.ipc.open_file(source)
        schema, first = reader.schema, reader.get_batch(0)
    run.folder = run.folder.with_name(run.folder.name + "-short")
    run.folder.mkdir()
    with pa.ipc.new_file(str(run.snapshot), schema) as writer:
        writer.write_batch(first)


def test_a_snapshot_with_fewer_rows_than_the_run_wrote_is_never_shown_as_whole(runs):
    status = wait(runs, start(runs, numbers(200_000))["id"])
    assert status["state"] == "completed" and status["rows"] == 200_000
    _cut_short(runs, status["id"])
    assert runs.page(status["id"], 0, 100)["rows"][-1] == [99]  # rows the file still holds show
    for read in (lambda: runs.page(status["id"], warehouse.BATCH_ROWS - 10, 100),
                 lambda: runs.page(status["id"], 150_000, 100),
                 lambda: runs.page(status["id"], 0, 100, sort=[{"column": 0, "desc": True}]),
                 lambda: runs.page(status["id"], 0, 100, filters=[{"column": 0, "text": "> 5"}]),
                 lambda: runs.column_stats(status["id"], 0),
                 lambda: runs.table(status["id"])):
        with pytest.raises(sql_exec.RerunNeeded, match="holds .*of the 200,000 rows|fewer rows than the 200,000"):
            read()
    for fmt in ("csv", "parquet", "json"):
        done = finish_export(runs, runs.export(status["id"], fmt, "short")["id"])
        assert done["state"] == "failed", fmt
        assert done["error"] == (f"The export read {warehouse.BATCH_ROWS:,} of the result's 200,000 rows from its "
                                 "snapshot on this PC, so the file was removed. Run the query again and export it.")
    assert not list((runs._downloads()).glob("short*"))


def test_the_rpc_answers_a_short_snapshot_with_run_it_again(tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    manager = manager_for(tmp_path)
    monkeypatch.setattr(Service, "sql_runs", property(lambda self: manager))
    svc = Service(root=tmp_path / "app")
    try:
        status = wait(manager, start(manager, numbers(100_000))["id"])
        _cut_short(manager, status["id"])
        answer = svc.sql_page(status["id"], 90_000, 100)
        assert answer["rerun_needed"] and "fewer rows than the 100,000" in answer["message"]
    finally:
        manager.shutdown()


# ---- local_query and local_query_status

def test_the_note_says_both_counts_and_what_to_change():
    assert shown_rows_note(1000, 1000, False, 1000) == ""
    assert shown_rows_note(1000, 2_500_000, True, 1000) == (
        'Showing the first 1,000 of 2,500,000 rows. Pass a larger limit (up to 100,000), or run it with sql_start '
        '(target "local") to page through and export every row.')
    assert shown_rows_note(1000, None, True, 1000) == (
        'Showing the first 1,000 rows; the query returned more. Pass a larger limit (up to 100,000), or run it '
        'with sql_start (target "local") to page through and export every row.')
    assert shown_rows_note(MAX_QUERY_ROWS, TEN_MILLION, True, 500_000) == (
        'Showing the first 100,000 of 10,000,000 rows. A limit of 500,000 is more than one answer holds (100,000). '
        'Run it with sql_start (target "local") to page through and export every row.')


@pytest.fixture
def local(tmp_path):
    from fabric_desktop.localdata import LocalData
    data = LocalData(tmp_path / "local-data")
    pid = data.create("Big")["id"]
    engine = data.engine(pid)
    engine.execute("CREATE TABLE numbers AS SELECT range AS n FROM range(10000000)")
    yield engine
    data.close_all()


@pytest.mark.parametrize("dialect, query", [
    ("duckdb", "SELECT range AS n FROM range(10000000)"),
    ("tsql", "SELECT n FROM dbo.numbers ORDER BY n"),
])
def test_local_query_of_ten_million_rows_says_it_shows_the_first(local, dialect, query):
    out = local.execute(query, dialect=dialect, limit=1000)
    assert len(out["rows"]) == 1000 and out["row_count"] == TEN_MILLION and out["truncated"]
    assert out["note"].startswith("Showing the first 1,000 of 10,000,000 rows.")


def test_local_query_says_when_a_limit_is_above_what_one_answer_holds(local):
    out = local.execute("SELECT range AS n FROM range(150000)", limit=500_000)
    assert len(out["rows"]) == MAX_QUERY_ROWS and out["row_count"] == 150_000
    assert "A limit of 500,000 is more than one answer holds (100,000)." in out["note"]
    uncounted = local.execute("SELECT range AS n FROM range(5000)", limit=10, count=False)
    assert uncounted["row_count"] is None and uncounted["note"].startswith(
        "Showing the first 10 rows; the query returned more.")
    whole = local.execute("SELECT range AS n FROM range(10)", limit=10)
    assert whole["row_count"] == 10 and not whole["truncated"] and "note" not in whole


def test_a_local_run_of_ten_million_rows_pages_to_the_last(tmp_path, local):
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "", audit=lambda *a: None, limits=limits(),
                                     downloads=lambda: tmp_path / "Downloads", local_engine=lambda pid: local)
    try:
        status = wait(manager, manager.start("SELECT n FROM dbo.numbers ORDER BY n", target="local",
                                             project="Big", dialect="tsql")["id"], seconds=300)
        assert status["state"] == "completed" and status["rows"] == TEN_MILLION
        assert manager.page(status["id"], TEN_MILLION - 1, 10)["rows"] == [[TEN_MILLION - 1]]
    finally:
        manager.shutdown()


def test_local_query_status_returns_every_row_asked_for_past_one_page(tmp_path):
    # Before WR-225, a limit above the SQL page's size (10,000) came back as one page of rows, with
    # truncated false: 10,000 of 20,000 rows and nothing said.
    from fabric_desktop import server
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path / "app")

    def call(method, **params):
        reply = server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
        assert "error" not in reply, reply
        return reply["result"]

    def until_done(query_id):
        for _ in range(1200):
            status = call("local_query_status", query_id=query_id)
            if status["state"] != "running":
                return status
            time.sleep(0.05)
        raise AssertionError("still running")
    try:
        pid = call("local_create_project", name="Old")["id"]
        every = until_done(call("local_query_start", project=pid, query="SELECT range AS n FROM range(20000)",
                                limit=50_000)["query_id"])["result"]
        assert every["rows"] == [[n] for n in range(20_000)] and not every["truncated"] and "note" not in every
        some = until_done(call("local_query_start", project=pid, query="SELECT range AS n FROM range(30000)",
                               limit=25_000)["query_id"])["result"]
        assert len(some["rows"]) == 25_000 and some["rows"][-1] == [24_999] and some["truncated"]
        assert some["note"].startswith("Showing the first 25,000 of 30,000 rows.")
        over = until_done(call("local_query_start", project=pid, query="SELECT range AS n FROM range(120000)",
                               limit=200_000)["query_id"])["result"]
        assert len(over["rows"]) == MAX_QUERY_ROWS and over["truncated"]
        assert "A limit of 200,000 is more than one answer holds (100,000)." in over["note"]
        answer = call("local_query", project=pid, query="SELECT range AS n FROM range(2000)", limit=5)
        assert answer["note"].startswith("Showing the first 5 of 2,000 rows.")
    finally:
        svc.sql_runs.shutdown()
        svc.local_data.close_all()
