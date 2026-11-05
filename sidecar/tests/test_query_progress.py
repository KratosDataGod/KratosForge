"""A running spark query reports how far it is, for the line under a running cell (spark_local.progress)."""

import time

import pytest

from fabric_desktop import spark_local
from fabric_desktop.notebook import Kernel


QUERY = "SELECT a % 1000 AS k, max(md5(a::varchar)) FROM big GROUP BY 1"


@pytest.mark.parametrize("cell", [f"spark.sql(\"{QUERY}\").collect()",  # in the Spark API
                                  f"%%sql\n{QUERY}",  # shown as the cell's table (as_table)
                                  f"spark.sql(\"{QUERY}\")"])  # a DataFrame as the cell's last line
def test_a_running_query_reports_its_progress_and_a_finished_one_doesnt(cell):
    k = Kernel()
    assert k.run("spark.conn.execute('CREATE OR REPLACE TABLE big AS SELECT range AS a FROM range(20000000)')")["status"] == "ok"
    k.start(cell)
    seen = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.2)
        if snap["status"] != "running":
            break
        if snap.get("progress") is not None:
            seen = snap["progress"]
            break
    k.interrupt()
    assert seen is not None and 0 < seen <= 100
    while k.poll(-1, 0.5)["status"] == "running":
        pass
    assert "progress" not in k.poll(-1, 0)
    assert spark_local.progress() is None
    k.run("spark.conn.execute('DROP TABLE big')")


def test_a_query_from_a_thread_the_cell_started_shows_no_progress_under_the_cell():
    k = Kernel()
    assert k.run("spark.conn.execute('CREATE OR REPLACE TABLE big AS SELECT range AS a FROM range(20000000)')")["status"] == "ok"
    k.start("import threading, time\n"
            "t = threading.Thread(target=lambda: spark.sql(\"SELECT a % 1000 AS k, max(md5(a::varchar)) FROM big GROUP BY 1\").collect())\n"
            "t.start()\n"
            "while t.is_alive():\n"
            "    time.sleep(0.05)")
    seen, during = [], False
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.2)
        if snap["status"] != "running":
            break
        during = during or spark_local.progress() is not None  # the thread's query is under way
        seen.append(snap.get("progress"))
    assert during, "the query finished before it could be seen"
    assert seen and all(p is None for p in seen)
    k.run("spark.conn.execute('DROP TABLE big')")


@pytest.mark.parametrize("raw, shown", [(0.04, None), (0.06, 0.1), (0.0, None), (-1.0, None),
                                        (37.25, 37.2), (100.0, 100.0), (None, None)])
def test_progress_that_rounds_to_nothing_is_not_reported(monkeypatch, raw, shown):
    class Conn:
        def query_progress(self):
            return raw

    class Session:
        conn = Conn()

    monkeypatch.setattr(spark_local, "_session", Session())
    assert spark_local.progress() == shown
