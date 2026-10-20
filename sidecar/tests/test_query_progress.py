"""A running spark query reports how far it is, for the line under a running cell (spark_local.progress)."""

import time

from fabric_desktop import spark_local
from fabric_desktop.notebook import Kernel


def test_a_running_query_reports_its_progress_and_a_finished_one_doesnt():
    k = Kernel()
    assert k.run("spark.conn.execute('CREATE OR REPLACE TABLE big AS SELECT range AS a FROM range(60000000)')")["status"] == "ok"
    k.start("spark.sql(\"SELECT a % 1000 AS k, max(md5(a::varchar)) FROM big GROUP BY 1\").collect()")
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
