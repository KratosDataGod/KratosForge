"""WR-602b: the SQL page's scripts, stops, kept results and parameter values under stress (sql_exec.py,
sql_worker.py). Real worker processes over local Delta folders. Nothing reaches Fabric or OneLake."""

from __future__ import annotations

import logging
import os
import time

import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop import sql_exec
from tests.test_sql_exec import SLOW, limits, wait

PARAM_VALUE = "Zq9-parameter-value-7731"


@pytest.fixture
def lake(tmp_path):
    write_deltalake(str(tmp_path / "lake" / "sales"), pa.table({"id": [1, 2, 3], "region": ["West", "East", "West"]}))
    return {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Test"}


def manager(tmp_path):
    runs = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "test-token", audit=lambda *a: runs.lines.append(a),
                                  limits=limits(), downloads=lambda: tmp_path / "Downloads", test_lake=tmp_path / "lake")
    runs.lines = []  # type: ignore[attr-defined]
    return runs


@pytest.fixture
def runs(tmp_path):
    m = manager(tmp_path)
    yield m
    m.shutdown()


def test_a_stop_in_the_middle_of_a_script_keeps_the_results_before_it(runs, lake):
    run = runs.start("", lake, statements=["SELECT count(*) AS n FROM sales", "SELECT id FROM sales ORDER BY id", SLOW, "SELECT 4 AS never"])
    end = time.monotonic() + 60
    while len(runs.status(run["id"]).get("parts", [])) < 2:
        assert time.monotonic() < end, runs.status(run["id"])
        time.sleep(0.05)
    runs.cancel(run["id"])
    status = wait(runs, run["id"])
    assert status["state"] == "cancelled"
    assert [p["statement"] for p in status["parts"]] == [1, 2], "the slow one and the one after it have no result"
    assert runs.page(run["id"], part=1)["rows"] == [[3]]
    assert runs.page(run["id"], part=2)["rows"] == [[1], [2], [3]]
    assert runs.page(run["id"], part=2, sort=[{"column": 0, "desc": True}])["rows"][0] == [3], "and still sort"
    with pytest.raises(ValueError, match="Statement 3 has no result"):
        runs.page(run["id"], part=3)


def test_results_and_pins_last_for_the_session_and_a_restart_asks_to_run_again(tmp_path, lake):
    first = manager(tmp_path)
    done = wait(first, first.start("SELECT id FROM sales ORDER BY id", lake)["id"])
    first.pin(done["id"])
    folder = first._get(done["id"]).folder
    assert folder.is_dir()
    first.shutdown()
    # The app starts again: the History and pins were this session's. A result this session didn't run is never
    # read, pinned or not: asking for it says to run the query again. Its folder goes at the first run once it is
    # past the retention time, as any other.
    second = manager(tmp_path)
    try:
        with pytest.raises(KeyError, match="run the query again"):
            second.page(done["id"])
        old = time.time() - 25 * 3600
        os.utime(folder, (old, old))
        wait(second, second.start("SELECT 1", lake)["id"])
        assert not folder.exists()
        with pytest.raises(KeyError, match="run the query again"):
            second.pin(done["id"])
    finally:
        second.shutdown()


@pytest.mark.parametrize("query", [
    "SELECT id FROM sales WHERE region = :region",                    # matches nothing: the value isn't in the rows
    "SELECT CAST(:region AS INTEGER) AS n",                          # fails, with the value in DuckDB's message
    "SELECT id FROM sales WHERE region = :region; SELECT CAST(:region AS INTEGER)",
])
def test_a_parameter_value_never_reaches_the_audit_the_logs_or_the_run_folder(runs, lake, tmp_path, caplog, query):
    caplog.set_level(logging.DEBUG)
    statements = [s for s in query.split("; ")] if "; " in query else None
    status = wait(runs, runs.start("" if statements else query, lake, statements=statements,
                                   params=[{"name": "region", "type": "text", "value": PARAM_VALUE}])["id"])
    assert status["state"] in ("completed", "failed")
    assert PARAM_VALUE not in repr(runs.lines), "the audit"
    assert PARAM_VALUE not in caplog.text, "the app's logs"
    assert runs._get(status["id"]).params == []
    for path in (tmp_path / "data").rglob("*"):
        if path.is_file():
            assert PARAM_VALUE.encode() not in path.read_bytes(), f"{path.relative_to(tmp_path)} holds the value"
    listed = repr(runs.recent())
    assert PARAM_VALUE not in listed.replace(repr(status.get("error") or ""), ""), "only the run's own error may show it, to the person"
