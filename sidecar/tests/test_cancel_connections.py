"""Cancel stops a query on a DuckDB connection the notebook opened itself (EA-016)."""

from __future__ import annotations

import threading
import time

import duckdb

from fabric_desktop import spark_local
from fabric_desktop.notebook import Kernel

LONG = "SELECT count(*) FROM range(3000000000) a WHERE (a.range * 7919) % 104729 = 3"


def interrupted(k: Kernel, code: str) -> dict:
    res: dict = {}
    th = threading.Thread(target=lambda: res.setdefault("out", k.run(code)), daemon=True)
    th.start()
    time.sleep(1.5)
    t0 = time.monotonic()
    k.interrupt()
    th.join(15)
    assert not th.is_alive() and time.monotonic() - t0 < 10
    return res["out"]


def test_cancel_stops_a_query_on_the_notebooks_own_connection():
    out = interrupted(Kernel(), f"import duckdb\ncon = duckdb.connect()\nprint(con.sql({LONG!r}).fetchall())")
    assert out["status"] in ("error", "cancelled"), out


def test_a_connection_from_an_earlier_cell_is_reached_too():
    k = Kernel()
    assert k.run("import duckdb\ncon = duckdb.connect()")["status"] == "ok"
    out = interrupted(k, f"print(con.execute({LONG!r}).fetchall())")
    assert out["status"] in ("error", "cancelled"), out
    assert k.run("print(con.sql('SELECT 42').fetchall())")["outputs"][0]["text"] == "[(42,)]\n"  # still usable


def test_connections_opened_outside_a_cell_are_not_recorded():
    k = Kernel()
    k.run("x = 1")  # installs the recorder
    before = len(k._duckdb_connections)
    conn = duckdb.connect()  # the app's own, on a thread that isn't running a cell
    assert len(k._duckdb_connections) == before and conn not in k._duckdb_connections
    assert threading.get_ident() not in spark_local._cell_connections



def test_connections_the_app_opens_from_a_cell_are_not_recorded():
    """A module of the app calling duckdb.connect on a cell's thread (a lazily made helper connection)
    isn't the notebook's, so a notebook's Cancel never interrupts it."""
    k = Kernel()
    out = k.run("import duckdb\nmine = duckdb.connect()\n"
                "app_module = {'__name__': 'fabric_desktop.helper', 'duckdb': duckdb}\n"
                "exec('def make():\\n    return duckdb.connect()', app_module)\n"
                "app = app_module['make']()")
    assert out["status"] == "ok", out
    recorded = list(k._duckdb_connections)
    assert len(recorded) == 1 and recorded[0] is k.namespace["mine"]
