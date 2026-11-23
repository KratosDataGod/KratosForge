"""WR-262: a big result streams batch by batch, on a warehouse and in a local project, and is never held whole.

- A warehouse run (warehouse.run) reads the driver's rows a page of BATCH_ROWS at a time and writes each page to
  the result's Arrow file before it asks for the next. A stand-in driver serves 2 million rows, made as they're
  asked for, and counts the pages still alive each time it's asked: never more than the one just written.
  Python's peak memory (tracemalloc) stays under a fourth of what the rows would take held whole.
- A local query (LocalEngine.execute_reader) hands its result over in batches of ``batch_size`` rows: a 5 million
  row result comes in 500 batches. The process's resident memory, read between batches, grows by much less than
  the 280 MB the result takes as Arrow. That reading caught #1340, where DuckDB 2.0's reader over an executed
  result copied every row before the first batch (360 MB more here); streamed, it grows by about 60 MB.
  It needs /proc (Linux), so it's skipped elsewhere.
"""

from __future__ import annotations

import gc
import math
import os
import tracemalloc
import weakref

import pyarrow as pa
import pytest

from fabric_desktop import warehouse
from fabric_desktop.localdata import LocalData

TWO_MILLION = 2_000_000
FIVE_MILLION = 5_000_000
MB = 1024 * 1024


class _Page(list):
    """A page of rows that can be watched with a weakref, to see when the run lets go of it."""


class _Cursor:
    def __init__(self, n: int):
        self.description = None
        self.rowcount = -1
        self.n = n
        self.sent = 0
        self.asked: list[int] = []
        self.pages: list[weakref.ref] = []
        self.most_alive = 0

    def execute(self, query, *params):
        self.description = [("n", int, None, None, 19, 0, True), ("name", str, None, 20, None, None, True)]

    def nextset(self):
        return False

    def fetchmany(self, size):
        gc.collect()
        alive = sum(1 for page in self.pages if page() is not None)
        self.most_alive = max(self.most_alive, alive)
        self.asked.append(size)
        page = _Page((i, f"row {i:>12}") for i in range(self.sent, min(self.sent + size, self.n)))
        self.sent += len(page)
        self.pages.append(weakref.ref(page))
        return page


class _Connection:
    timeout = 0

    def __init__(self, n: int):
        self.cursor_ = _Cursor(n)

    def cursor(self):
        return self.cursor_

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture()
def two_million(monkeypatch):
    con = _Connection(TWO_MILLION)
    monkeypatch.setattr(warehouse, "connect", lambda server, database, token, login_timeout=60: con)
    return con


def _run(tmp_path, said):
    job = {"query": "SELECT n, name FROM dbo.numbers", "warehouse": {"server": "x.datawarehouse.fabric.microsoft.com",
                                                                      "database": "wh"},
           "out": str(tmp_path / "result.arrow"), "parts_dir": str(tmp_path)}
    warehouse.run(job, lambda: "token", lambda event, **kw: said.append((event, kw)))
    return tmp_path / "result.arrow"


def test_a_warehouse_result_of_2_million_rows_streams_page_by_page(tmp_path, two_million):
    said: list = []
    tracemalloc.start()
    try:
        out = _run(tmp_path, said)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    pages = math.ceil(TWO_MILLION / warehouse.BATCH_ROWS)
    done = dict(said)["done"]
    assert done["rows"] == TWO_MILLION
    assert len(done["batches"]) == pages and max(done["batches"]) == warehouse.BATCH_ROWS
    cursor = two_million.cursor_
    assert cursor.asked == [warehouse.BATCH_ROWS] * (pages + 1)  # the last ask finds no rows left
    assert cursor.most_alive <= 1  # only the page just written is still held when the next is asked for
    # 2 million (int, str) rows as Python tuples take over 250 MB; one page of 65,536 takes under 10 MB
    assert peak < 64 * MB, f"peak {peak / MB:.0f} MB"
    with pa.memory_map(str(out)) as source:
        reader = pa.ipc.open_file(source)
        assert reader.num_record_batches == pages
        assert sum(reader.get_batch(i).num_rows for i in range(pages)) == TWO_MILLION
        assert reader.get_batch(pages - 1).column(0)[-1].as_py() == TWO_MILLION - 1


@pytest.fixture()
def engine(tmp_path):
    store = LocalData(tmp_path / "app" / "local-data")
    e = store.engine(store.create("Streaming")["id"])
    yield e
    store.close_all()


FIVE_MILLION_ROWS = f"SELECT range AS n, repeat('x', 40) || CAST(range AS VARCHAR) AS name FROM range({FIVE_MILLION})"


def _resident_mb() -> float:
    with open("/proc/self/status", encoding="ascii") as status:
        return next(int(line.split()[1]) for line in status if line.startswith("VmRSS:")) / 1024


def test_a_local_result_of_5_million_rows_streams_batch_by_batch(engine):
    sizes: list[int] = []
    last = -1
    with engine.execute_reader(FIVE_MILLION_ROWS, batch_size=10_000) as stream:
        for batch in stream:
            sizes.append(batch.num_rows)
            last = batch.column(0)[-1].as_py()
    assert sum(sizes) == FIVE_MILLION and last == FIVE_MILLION - 1
    assert len(sizes) == FIVE_MILLION // 10_000 and max(sizes) == 10_000


@pytest.mark.skipif(not os.path.exists("/proc/self/status"), reason="reads resident memory from /proc (Linux)")
def test_a_local_result_is_never_held_whole(engine):
    engine.execute("SELECT 1", dialect="duckdb")  # the project's database is open before the baseline
    before = _resident_mb()
    grew = 0.0
    rows = 0
    with engine.execute_reader(FIVE_MILLION_ROWS, batch_size=10_000) as stream:
        for batch in stream:
            rows += batch.num_rows
            grew = max(grew, _resident_mb() - before)
    assert rows == FIVE_MILLION
    # held whole, the result is about 280 MB as Arrow; streamed, a few batches and DuckDB's buffers
    assert grew < 160, f"resident memory grew {grew:.0f} MB"


def test_closing_a_local_stream_early_frees_the_project(engine):
    with engine.execute_reader(FIVE_MILLION_ROWS, batch_size=10_000) as stream:
        first = next(iter(stream))
        assert first.num_rows <= 10_000
    assert engine.execute("SELECT 1 AS one")["rows"] == [[1]]
