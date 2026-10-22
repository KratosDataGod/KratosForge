"""SQL-004 (W4): read-only queries on a Fabric warehouse or SQL analytics endpoint.

No test reaches Fabric: the worker runs as a real process with a stand-in driver (FakeDriver
below), and Fabric's item lookups are stand-ins too. The one live query is a separate, asked-for run.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pyarrow as pa
import pytest

from fabric_desktop import auth, sql_exec, warehouse
from fabric_desktop.service import Service

SIDECAR = Path(__file__).resolve().parents[1]
WH = {"workspace_id": "ws", "item_id": "wh1", "kind": "warehouse"}
SERVER = "abc123.datawarehouse.fabric.microsoft.com"


def limits(**changes):
    base = {"sql_page_rows": 1000, "sql_time_limit_minutes": 10, "sql_memory_limit_percent": 30,
            "sql_max_concurrent": 2, "sql_result_retention_hours": 24, "sql_temp_dir": ""}
    base.update(changes)
    return lambda: base


class FakeFabric:
    def __init__(self, server=SERVER):
        self.server, self.calls = server, []

    def get_warehouse(self, ws, item):
        self.calls.append(("warehouse", ws, item))
        return {"displayName": "Sales WH", "properties": {"connectionString": self.server}}

    def get_lakehouse(self, ws, item):
        self.calls.append(("lakehouse", ws, item))
        return {"displayName": "LH_Gold", "properties": {"sqlEndpointProperties": {"connectionString": self.server,
                                                                                   "id": "ep1"}}}


# The worker process: the real sql_worker with mssql_python's connect swapped for a stand-in that
# serves rows for "SELECT ..." and sleeps for "WAITFOR". It records what it was given in log.txt.
WORKER = textwrap.dedent('''
    import datetime as dt, decimal, json, sys, time, uuid
    sys.path.insert(0, {sidecar!r})
    from fabric_desktop import sql_worker, warehouse
    LOG = {log!r}

    class Cursor:
        def __init__(self):
            self.description, self.rows = None, []
        def execute(self, q):
            if "WAITFOR" in q.upper():
                time.sleep(60)
            n = 70000 if "BIG" in q.upper() else 2
            self.description = [("id", int, None, None, 10, 0, True), ("name", str, None, None, None, None, True),
                                ("amount", decimal.Decimal, None, None, 18, 2, True),
                                ("at", dt.datetime, None, None, None, None, True),
                                ("key", uuid.UUID, None, None, None, None, True)]
            self.rows = [(i, None if i == 1 else f"n{{i}}", decimal.Decimal("1.25") * i,
                          dt.datetime(2026, 10, 22, 12, 0), uuid.UUID(int=i)) for i in range(n)]
        def fetchmany(self, size):
            out, self.rows = self.rows[:size], self.rows[size:]
            return out

    class Connection:
        timeout = 0
        def __init__(self, server, database, token):
            self.seen = {{"server": server, "database": database, "token": token}}
        def cursor(self):
            return Cursor()
        def rollback(self):
            self.seen["rolled_back"] = True
        def close(self):
            self.seen["timeout"] = self.timeout
            open(LOG, "w").write(json.dumps(self.seen))

    warehouse.connect = lambda server, database, token, login_timeout=60: Connection(server, database, token)
    sql_worker.main()
''')


@pytest.fixture
def runs(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(WORKER.format(sidecar=str(SIDECAR), log=str(tmp_path / "log.txt")), encoding="utf-8")
    fabric, audit, tokens = FakeFabric(), [], []

    def token():
        tokens.append(1)
        return "SECRET-SQL-TOKEN"
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "STORAGE", audit=lambda *a: audit.append(a),
                                     limits=limits(), command=[sys.executable, str(script)], warehouse_token=token,
                                     resolve_warehouse=lambda ref: warehouse.resolve(fabric, ref))
    manager.fabric, manager.audit_lines, manager.tokens, manager.log = fabric, audit, tokens, tmp_path / "log.txt"
    yield manager
    manager.shutdown()


def wait(manager, run_id, states=sql_exec.FINISHED, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        status = manager.status(run_id)
        if status["state"] in states:
            return status
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still {manager.status(run_id)['state']}")


def start(manager, query, **kw):
    return manager.start(query, dialect="tsql", target="warehouse", warehouse_ref=kw.pop("ref", WH), **kw)


def test_a_query_runs_on_the_warehouse_fabric_names_and_pages_like_any_run(runs):
    status = wait(runs, start(runs, "SELECT id, name FROM dbo.sales")["id"])
    assert status["state"] == "completed" and status["rows"] == 2 and status["warehouse"] == "Sales WH"
    assert [c["type"] for c in status["columns"]] == ["int64", "string", "decimal128(18, 2)", "timestamp[us]", "string"]
    page = runs.page(status["id"], 0, 10)
    assert page["rows"][1][:2] == [1, None]
    seen = json.loads(runs.log.read_text())
    assert seen == {"server": SERVER, "database": "Sales WH", "token": "SECRET-SQL-TOKEN", "rolled_back": True,
                    "timeout": 600}  # the time limit (Settings › SQL) is the driver's query timeout too
    assert runs.fabric.calls == [("warehouse", "ws", "wh1")] and runs.tokens == [1]


def test_results_bigger_than_a_batch_stream_in_batches(runs):
    status = wait(runs, start(runs, "SELECT * FROM big")["id"])
    assert status["rows"] == 70000 and runs._get(status["id"]).batches == [65536, 4464]


def test_a_sql_analytics_endpoint_is_found_through_its_lakehouse(runs):
    status = wait(runs, start(runs, "SELECT 1 AS one", ref={"workspace_id": "ws", "item_id": "lh1",
                                                           "kind": "endpoint"})["id"])
    assert status["state"] == "completed" and status["warehouse"] == "LH_Gold"
    assert runs.fabric.calls == [("lakehouse", "ws", "lh1")]


@pytest.mark.parametrize("query, says", [
    ("SELECT * INTO dbo.copy FROM dbo.sales", "SELECT ... INTO creates a table"),
    ("SELECT 1; DROP TABLE dbo.sales", "one query at a time"),
    ("DELETE FROM dbo.sales", "DELETE isn't a query that only reads"),
    ("EXEC dbo.load", "EXECUTE isn't"),
    ("DECLARE @x INT = 1", "DECLARE isn't"),
    ("BEGIN TRAN", "isn't a query that only reads"),
    ("CREATE TABLE dbo.t (a INT)", "CREATE isn't"),
])
def test_anything_but_one_read_is_refused_before_fabric_is_asked(runs, query, says):
    status = start(runs, query)
    assert status["state"] == "failed" and status["error_kind"] == "permission" and says in status["error"]
    assert "nothing was sent" in status["error"] and status["refused"]["permission"] == "fabric_mutation"
    assert runs.fabric.calls == [] and runs.tokens == [] and not runs.log.exists()
    assert runs.audit_lines[-1][1] == "error" and runs.audit_lines[-1][3]["target"] == "warehouse"


def test_t_sql_that_cant_be_read_is_refused_not_sent(runs):
    with pytest.raises(ValueError, match="can't be shown to only read"):
        start(runs, "SELECT FROM WHERE (")
    assert runs.fabric.calls == []


def test_outside_reads_are_refused():
    v = warehouse.verdicts("SELECT * FROM OPENQUERY(srv, 'SELECT 1')")
    assert v[0].kind != "read_only"


def test_a_dry_run_says_what_would_run_and_sends_nothing(runs):
    plan = start(runs, "SELECT 1 AS one", dry_run=True)
    assert plan["refused"] is None and plan["permission"] == "read_only" and plan["target"] == "warehouse"
    plan = start(runs, "UPDATE dbo.t SET a = 1", dry_run=True)
    assert plan["refused"]["permission"] == "fabric_mutation"
    assert runs.fabric.calls == [] and runs.tokens == []


def test_only_reading_is_ever_allowed_and_only_in_t_sql(runs):
    with pytest.raises(PermissionError, match="only reads"):
        start(runs, "SELECT 1", allow=["local_mutation"])
    with pytest.raises(ValueError, match="T-SQL"):
        runs.start("SELECT 1", dialect="spark", target="warehouse", warehouse_ref=WH)
    with pytest.raises(ValueError, match="Pick a warehouse"):
        start(runs, "SELECT 1", ref={"workspace_id": "ws", "item_id": "x", "kind": "database"})


def test_stop_ends_the_run_and_its_connection(runs):
    run_id = start(runs, "SELECT 1 AS waitfor")["id"]  # the stand-in driver waits a minute
    wait(runs, run_id, ("running",))
    time.sleep(0.5)
    status = wait(runs, runs.cancel(run_id)["id"], seconds=20)
    assert status["state"] == "cancelled" and not runs.log.exists()  # the process ended mid-query


def test_the_time_limit_stops_a_slow_warehouse_query(runs):
    runs._limits = limits(sql_time_limit_minutes=0.02)
    status = wait(runs, start(runs, "SELECT 1 AS waitfor")["id"])
    assert status["state"] == "timed_out" and "time limit" in status["error"]


def test_the_audit_line_names_the_warehouse_never_the_sql_or_the_token(runs):
    wait(runs, start(runs, "SELECT 'private' AS x")["id"])
    action, outcome, _seconds, params = runs.audit_lines[-1]
    assert action == "sql_execution" and outcome == "ok"
    assert params["warehouse"] == "Sales WH" and params["warehouse_id"] == "wh1" and params["workspace_id"] == "ws"
    assert "private" not in repr(params) and "SECRET" not in repr(params)


# ---------------------------------------------------------------- the server comes only from Fabric


@pytest.mark.parametrize("server", ["evil.example.com", "abc.datawarehouse.fabric.microsoft.com.evil.com",
                                    "abc.datawarehouse.fabric.microsoft.com;Database=x", ""])
def test_a_server_that_isnt_fabrics_is_never_used(server):
    with pytest.raises(ValueError):
        warehouse.resolve(FakeFabric(server), WH)


def test_resolve_reads_the_item_from_fabric():
    assert warehouse.resolve(FakeFabric(), WH) == warehouse.Endpoint(SERVER, "Sales WH")


# ---------------------------------------------------------------- types, in process


class _Cursor:
    def __init__(self, description, rows):
        self.description, self._rows = description, rows

    def execute(self, query):
        pass

    def fetchmany(self, size):
        out, self._rows = self._rows[:size], self._rows[size:]
        return out


class _Con:
    timeout = 0

    def __init__(self, cursor):
        self._cursor, self.events = cursor, []

    def cursor(self):
        return self._cursor

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.events.append("close")


def test_types_become_arrow_types(tmp_path, monkeypatch):
    utc = dt.timezone.utc
    desc = [("b", bool, None, None, 1, 0, True), ("f", float, None, None, 53, 0, True),
            ("wide", decimal.Decimal, None, None, 38, 4, True), ("huge", decimal.Decimal, None, None, 0, 0, True),
            ("d", dt.date, None, None, 10, 0, True), ("t", dt.time, None, None, 16, 7, True),
            ("off", dt.datetime, None, None, 34, 7, True), ("bin", bytes, None, None, 8, 0, True),
            ("g", uuid.UUID, None, None, 36, 0, True), (None, str, None, None, 0, 0, True)]
    row = (True, 1.5, decimal.Decimal("12.3456"), decimal.Decimal("1.5"), dt.date(2026, 1, 2), dt.time(3, 4, 5),
           dt.datetime(2026, 1, 2, 5, 0, tzinfo=dt.timezone(dt.timedelta(hours=2))), b"\x01", uuid.UUID(int=7), "x")
    con = _Con(_Cursor(desc, [row, (None,) * len(row)]))
    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: con)
    said = []
    out = tmp_path / "r.arrow"
    warehouse.run({"query": "SELECT 1", "out": str(out), "warehouse": {"server": SERVER, "database": "d"}},
                  lambda: "t", lambda event, **f: said.append((event, f)))
    table = pa.ipc.open_file(str(out)).read_all()
    assert [str(f.type) for f in table.schema] == ["bool", "double", "decimal128(38, 4)", "string", "date32[day]",
                                                   "time64[us]", "timestamp[us, tz=UTC]", "binary", "string", "string"]
    assert table.schema.names[-1] == "column10"
    first = table.to_pylist()[0]
    assert first["off"] == dt.datetime(2026, 1, 2, 3, 0, tzinfo=utc) and first["g"] == str(uuid.UUID(int=7))
    assert said[-1][0] == "done" and said[-1][1]["rows"] == 2 and con.events == ["rollback", "close"]


def test_a_failed_query_still_rolls_back_and_closes(tmp_path, monkeypatch):
    class Boom(_Cursor):
        def execute(self, query):
            raise RuntimeError("Invalid object name 'dbo.nope'.")
    con = _Con(Boom([], []))
    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: con)
    with pytest.raises(RuntimeError, match="dbo.nope"):
        warehouse.run({"query": "SELECT * FROM dbo.nope", "out": str(tmp_path / "r.arrow"),
                       "warehouse": {"server": SERVER, "database": "d"}}, lambda: "t", lambda *a, **k: None)
    assert con.events == ["rollback", "close"]


# ---------------------------------------------------------------- sign-in and the service


def test_the_token_is_for_azure_sql():
    assert auth.WAREHOUSE_SCOPES == ["https://database.windows.net/user_impersonation"]
    assert auth.default_scope(auth.WAREHOUSE_SCOPES) == "https://database.windows.net/.default"


def test_the_service_passes_the_warehouse_through(tmp_path):
    svc = Service(root=tmp_path / "app")
    plan = svc.sql_start("SELECT 1 AS one", dialect="tsql", target="warehouse", warehouse_ref=WH, dry_run=True)
    assert plan["refused"] is None and plan["target"] == "warehouse"


def test_a_warehouse_fabric_cant_find_fails_the_run_without_connecting(runs):
    runs.fabric.server = ""
    status = wait(runs, start(runs, "SELECT 1 AS one")["id"])
    assert status["state"] == "failed" and status["error_kind"] == "fabric" and "SQL connection" in status["error"]
    assert runs.tokens == [] and not runs.log.exists()


def test_the_worker_checks_again_and_never_connects_for_a_write(tmp_path, monkeypatch):
    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: pytest.fail("connected"))
    said = []
    warehouse.run({"query": "DROP TABLE dbo.sales", "out": str(tmp_path / "r.arrow"),
                   "warehouse": {"server": SERVER, "database": "d"}},
                  lambda: pytest.fail("asked for a token"), lambda event, **f: said.append((event, f)))
    assert said[0][0] == "error" and said[0][1]["kind"] == "permission" and "nothing was sent" in said[0][1]["message"]
