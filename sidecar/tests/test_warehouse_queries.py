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

    RAN = []

    class Cursor:
        def __init__(self):
            self.description, self.rows, self.rowcount = None, [], -1
        def nextset(self):
            return False
        def execute(self, q):
            RAN.append(q)
            if "WAITFOR" in q.upper():
                time.sleep(60)
            if q.lstrip().upper().startswith(("UPDATE", "CREATE", "EXEC", "DELETE", "INSERT")):
                if "FAIL" in q.upper():
                    raise RuntimeError("Invalid column name 'nope'.")
                self.description, self.rowcount = None, 3
                return
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
        def commit(self):
            self.seen["committed"], self.seen["ran"] = True, list(RAN)
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


@pytest.mark.parametrize("query", [
    "SELECT TOP 100 * FROM dbo.FactSales OPTION (FOR TIMESTAMP AS OF '2026-10-20T10:00:00.000')",
    "select count_big(*) from [dbo].[FactSales]\noption ( for timestamp as of '2026-10-20T10:00:00' ) ;\n",
])
def test_a_read_at_a_past_time_is_a_read(query):
    (v,) = warehouse.verdicts(query)
    assert v.kind == "read_only", v


@pytest.mark.parametrize("query", [
    "DELETE FROM dbo.FactSales OPTION (FOR TIMESTAMP AS OF '2026-10-20T10:00:00')",
    "SELECT 1; DROP TABLE dbo.t OPTION (FOR TIMESTAMP AS OF '2026-10-20T10:00:00')",
    "SELECT * INTO dbo.copy FROM dbo.t OPTION (FOR TIMESTAMP AS OF '2026-10-20T10:00:00')",
    "SELECT 1 OPTION (FOR TIMESTAMP AS OF '2026-10-20T10:00:00' + (SELECT 'x'))",
    "SELECT 1 OPTION (FOR TIMESTAMP AS OF @when)",
])
def test_the_past_time_hint_never_lets_anything_but_one_read_through(query):
    try:
        verdicts = warehouse.verdicts(query)
    except ValueError:
        return  # can't be read: refused
    assert verdicts[0].kind != "read_only" or len(verdicts) > 1 and verdicts[1].kind != "read_only", verdicts


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
    with pytest.raises(PermissionError, match="reads, or changes the warehouse after you confirm it"):
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


# ---------------------------------------------------------------- changes (WH-10)


def change(manager, query, **kw):
    kw.setdefault("confirm", True)
    kw.setdefault("confirm_name", "Sales WH")
    return start(manager, query, allow=["warehouse_write"], **kw)


SCRIPT = "UPDATE dbo.sales SET region = 'West' WHERE id = 7;\nGO\nEXEC dbo.load_totals;\ngo\nSELECT id, name FROM dbo.sales"


def test_a_confirmed_change_runs_its_batches_in_one_transaction_and_commits(runs):
    status = wait(runs, change(runs, SCRIPT)["id"])
    assert status["state"] == "completed", status["error"]
    assert (status["change"], status["affected"], status["committed"], status["rows"]) == (True, 6, True, 2)
    assert status["permission"] == "warehouse_write"
    seen = json.loads(runs.log.read_text())
    assert seen["committed"] and "rolled_back" not in seen
    assert seen["ran"] == ["UPDATE dbo.sales SET region = 'West' WHERE id = 7;", "EXEC dbo.load_totals;",
                           "SELECT id, name FROM dbo.sales"]
    action, outcome, _seconds, params = runs.audit_lines[-1]
    assert (params["permission"], params["affected"], params["committed"]) == ("warehouse_write", 6, True)
    assert "West" not in repr(params) and "load_totals" not in repr(params)


def test_a_change_that_fails_keeps_nothing(runs):
    status = wait(runs, change(runs, "UPDATE dbo.sales SET region = 'West';\nGO\nUPDATE dbo.sales SET fail = 1")["id"])
    assert status["state"] == "failed" and "nope" in status["error"] and status["committed"] is False
    seen = json.loads(runs.log.read_text())
    assert seen["rolled_back"] and "committed" not in seen


def test_a_change_needs_its_confirm_and_the_warehouses_name(runs):
    with pytest.raises(PermissionError, match="after you confirm it, naming the warehouse"):
        change(runs, "UPDATE dbo.sales SET a = 1", confirm=False)
    with pytest.raises(PermissionError, match="naming the warehouse"):
        change(runs, "UPDATE dbo.sales SET a = 1", confirm_name="  ")
    assert runs.fabric.calls == [] and runs.tokens == []


def test_a_change_named_for_another_warehouse_is_never_sent(runs):
    status = wait(runs, change(runs, "UPDATE dbo.sales SET a = 1", confirm_name="Sales WH Prod")["id"])
    assert status["state"] == "failed" and status["error_kind"] == "permission"
    assert "You confirmed a change to Sales WH Prod, but Fabric names this warehouse Sales WH" in status["error"]
    assert runs.tokens == [] and not runs.log.exists()


def test_a_run_that_wasnt_started_as_a_change_still_only_reads(runs):
    status = start(runs, "UPDATE dbo.sales SET a = 1", confirm=True, confirm_name="Sales WH")
    assert status["state"] == "failed" and "only reads" in status["error"]


def test_a_dry_run_says_what_a_change_does_without_a_confirm(runs):
    plan = start(runs, SCRIPT, allow=["warehouse_write"], dry_run=True)
    assert plan["refused"] is None and plan["permission"] == "warehouse_write"
    assert [(p["permission"], p["reason"]) for p in plan["statements"]] == [
        ("warehouse_write", "UPDATE dbo.sales changes the warehouse"),
        ("warehouse_write", "EXEC dbo.load_totals changes the warehouse"),
        ("read_only", "reads the warehouse's tables")]
    assert runs.fabric.calls == [] and runs.tokens == []


def test_changes_turned_off_in_settings_are_refused(runs, monkeypatch):
    from fabric_desktop import app_settings
    real = app_settings.load

    def off(source):
        def load():
            out = real()
            out["sql_warehouse_writes"] = {"value": False, "source": source}
            return out
        return load
    monkeypatch.setattr(app_settings, "load", off("user"))
    with pytest.raises(PermissionError, match=r"turned off in Settings › SQL\.$"):
        change(runs, "UPDATE dbo.sales SET a = 1")
    monkeypatch.setattr(app_settings, "load", off("policy"))
    with pytest.raises(PermissionError, match="by your organization"):
        change(runs, "UPDATE dbo.sales SET a = 1", dry_run=True)
    assert wait(runs, start(runs, "SELECT 1 AS one")["id"])["state"] == "completed", "reading still works"


def test_the_setting_is_on_by_default_and_a_broken_policy_turns_it_off(tmp_path, monkeypatch):
    from fabric_desktop import app_settings
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    assert app_settings.load()["sql_warehouse_writes"] == {"value": True, "source": "default"}
    (tmp_path / "policy.json").write_text(json.dumps({"sql_warehouse_writes": "no"}), encoding="utf-8")
    assert app_settings.load()["sql_warehouse_writes"] == {"value": False, "source": "policy"}


@pytest.mark.parametrize("query, kind, says", [
    ("UPDATE dbo.t SET a = 1 WHERE b = 2", "warehouse_write", "UPDATE dbo.t changes the warehouse"),
    ("CREATE TABLE dbo.x (a INT)", "warehouse_write", "CREATE TABLE dbo.x"),
    ("SELECT * INTO dbo.n FROM dbo.t", "warehouse_write", "SELECT ... INTO creates a table"),
    ("TRUNCATE TABLE dbo.t", "warehouse_write", "TRUNCATE dbo.t"),
    ("DECLARE @n INT = 1", "read_only", "sets a variable"),
    ("SELECT 'BEGIN TRAN; USE other' AS x -- COMMIT", "read_only", "reads"),
    ("SELECT [Use], [Deny], \"Grant\" FROM dbo.t", "read_only", "reads"),
    ("CREATE OR ALTER PROCEDURE dbo.p AS BEGIN UPDATE dbo.t SET a = 1; SELECT 1 AS done; END", "warehouse_write",
     "defines PROCEDURE dbo.p"),
    ("CREATE PROCEDURE dbo.p AS BEGIN COMMIT; GRANT CONTROL TO someone; END", "fabric_mutation", "one transaction"),
    ("CREATE PROCEDURE dbo.p AS GRANT CONTROL TO someone", "fabric_mutation", "permissions, roles and users"),
    ("CREATE PROCEDURE dbo.p WITH EXECUTE AS OWNER AS SELECT 1", "fabric_mutation", "runs as someone else"),
    ("UPDATE dbo.t SET a = 1; EXEC('COMMIT')", "fabric_mutation", "SQL made from text"),
    ("EXEC('GRANT CONTROL TO someone')", "fabric_mutation", "SQL made from text"),
    ("EXECUTE (@sql)", "fabric_mutation", "SQL made from text"),
    ("EXEC @proc", "fabric_mutation", "SQL made from text"),
    ("EXEC sp_executesql N'USE other'", "fabric_mutation", "SQL made from text"),
    ("EXEC @rc = dbo.load", "warehouse_write", "EXEC"),
    ("EXECUTE AS USER = 'someone'", "fabric_mutation", "runs as someone else"),
    ("REVERT", "fabric_mutation", "runs as someone else"),
    ("ALTER ROLE db_owner ADD MEMBER [someone]", "fabric_mutation", "permissions, roles and users"),
    ("CREATE USER [someone] FROM EXTERNAL PROVIDER", "fabric_mutation", "permissions, roles and users"),
    ("CREATE ROLE loaders", "fabric_mutation", "permissions, roles and users"),
    ("ALTER AUTHORIZATION ON SCHEMA::dbo TO someone", "fabric_mutation", "permissions, roles and users"),
    ("EXEC sp_addrolemember 'db_owner', 'someone'", "fabric_mutation", "permissions, roles and users"),
    ("EXEC [sp_executesql] N'COMMIT'", "fabric_mutation", "SQL made from text"),
    ("EXEC [sys].[sp_executesql] N'COMMIT'", "fabric_mutation", "SQL made from text"),
    ('EXEC "sp_executesql" N\'COMMIT\'', "fabric_mutation", "SQL made from text"),
    ("EXEC [sp_addrolemember] 'db_owner', 'someone'", "fabric_mutation", "permissions, roles and users"),
    ("EXEC @r = @p N'COMMIT'", "fabric_mutation", "SQL made from text"),
    ("UPDATE dbo.t SET a = 1 GRANT CONTROL TO someone", "fabric_mutation", "permissions, roles and users"),
    ("UPDATE dbo.t SET a = 1\rGRANT CONTROL TO someone", "fabric_mutation", "permissions, roles and users"),
    ("top: GRANT CONTROL TO someone", "fabric_mutation", "permissions, roles and users"),
    ("IF (1=1) GRANT CONTROL TO someone", "fabric_mutation", "permissions, roles and users"),
    ("IF (1=1) USE OtherWH", "fabric_mutation", "open that warehouse instead"),
    ("UPDATE dbo.t SET a = 1 USE OtherWH", "fabric_mutation", "open that warehouse instead"),
    ("CREATE SCHEMA s AUTHORIZATION [u]", "fabric_mutation", "permissions, roles and users"),
    ("CREATE PROCEDURE [dbo].[p] AS UPDATE dbo.t SET a = 1", "warehouse_write", "defines PROCEDURE dbo.p"),
    ("CREATE PROCEDURE dbo.p AS SELECT * FROM [OPENROWSET]", "fabric_mutation", "OPENROWSET reads outside"),
    ("IF 1 = 1 UPDATE dbo.t SET a = 1", "fabric_mutation", "couldn't be read as T-SQL"),
    ("BEGIN TRAN", "fabric_mutation", "the run is one transaction"),
    ("IF 1 = 1 BEGIN COMMIT END", "fabric_mutation", "the run is one transaction"),
    ("USE OtherWH", "fabric_mutation", "open that warehouse instead"),
    ("IF 1 = 1 BEGIN USE OtherWH END", "fabric_mutation", "open that warehouse instead"),
    ("GRANT SELECT ON dbo.t TO someone", "fabric_mutation", "permissions, roles and users are managed in Fabric"),
    ("COPY INTO dbo.t FROM 'https://x/y.csv'", "fabric_mutation", "COPY INTO loads files"),
    ("DROP DATABASE WH_Gold", "fabric_mutation", "database"),
    ("UPDATE OtherWH.dbo.t SET a = 1", "fabric_mutation", "OtherWH.dbo.t is in another database"),
    ("EXEC OtherWH.dbo.load", "fabric_mutation", "OtherWH.dbo.load is in another database"),
    ("INSERT INTO dbo.t SELECT * FROM OPENROWSET(BULK 'x') AS r", "fabric_mutation", "OPENROWSET reads outside"),
    # Whichever of a comment, a string or a quoted name starts first wins, so none hides the rest.
    ("CREATE PROCEDURE p AS BEGIN SELECT 1 AS [it's] GRANT SELECT ON dbo.t TO [x] SELECT 'z' END", "fabric_mutation",
     "permissions, roles and users"),
    ("CREATE PROCEDURE p AS BEGIN SELECT 1 AS [a--b] COMMIT END", "fabric_mutation", "the run is one transaction"),
    ("CREATE PROCEDURE p AS BEGIN SELECT 1 AS [a/*b] COMMIT SELECT 1 AS [c*/d] END", "fabric_mutation",
     "the run is one transaction"),
    ('CREATE PROCEDURE p AS BEGIN SELECT 1 AS "it\'s" USE OtherWH SELECT \'z\' END', "fabric_mutation",
     "open that warehouse instead"),
    ("CREATE PROCEDURE p AS BEGIN SELECT 1 AS [x]]'y] EXEC sp_executesql N'COMMIT' END", "fabric_mutation",
     "SQL made from text"),
    ("UPDATE dbo.t SET a = '[' GRANT CONTROL TO someone SELECT ']'", "fabric_mutation", "permissions, roles and users"),
    ("UPDATE dbo.t SET a = 1 -- [\nGRANT CONTROL TO someone -- ]", "fabric_mutation", "permissions, roles and users"),
    ("SELECT 1 AS [it's], 'GRANT' AS [b--c], [Use] FROM dbo.t", "read_only", "reads"),
])
def test_what_a_change_may_do(query, kind, says):
    v = warehouse.verdicts(query, writes=True)
    assert v[0].kind == kind and says in v[0].reason, v


def test_a_word_refused_as_a_name_says_how_to_write_it():
    assert "written in brackets, such as [Use]" in warehouse.verdicts("SELECT Use FROM dbo.t", writes=True)[0].reason
    assert "such as [Grant]" in warehouse.verdicts("SELECT Deny FROM dbo.t", writes=True)[0].reason


def test_go_lines_split_a_script_into_batches():
    assert warehouse.batches("SELECT 1\nGO\nSELECT 'GO'\n  go ;  \nSELECT 2\r\nGO\r\n") == [
        "SELECT 1", "SELECT 'GO'", "SELECT 2"]
    with pytest.raises(ValueError, match="empty"):
        warehouse.verdicts("GO\nGO", writes=True)


def test_the_worker_checks_a_change_again_before_it_connects(tmp_path, monkeypatch):
    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: pytest.fail("connected"))
    said = []
    warehouse.run({"query": "BEGIN TRAN", "out": str(tmp_path / "r.arrow"), "writes": True,
                   "warehouse": {"server": SERVER, "database": "d"}},
                  lambda: pytest.fail("asked for a token"), lambda event, **f: said.append((event, f)))
    assert said[0][0] == "error" and "A warehouse change can't do this" in said[0][1]["message"]


def test_every_result_set_of_a_batch_is_read_and_the_last_is_shown(tmp_path, monkeypatch):
    class Sets(_Cursor):
        def __init__(self):
            super().__init__(None, [])
            self.sets, self.rowcount, self.ran = [], -1, []

        def execute(self, query):
            self.ran.append(query)
            self.sets = [(None, [], 5), ([("a", int, None, None, 10, 0, True)], [(1,)], -1),
                         ([("b", str, None, None, None, None, True)], [("x",), ("y",)], -1)]
            self.nextset()

        def nextset(self):
            if not self.sets:
                return False
            self.description, self._rows, self.rowcount = self.sets.pop(0)
            return True

    class Con(_Con):
        def commit(self):
            self.events.append("commit")
    cur = Sets()
    con = Con(cur)
    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: con)
    said, out = [], tmp_path / "r.arrow"
    warehouse.run({"query": "UPDATE dbo.t SET a = 1; SELECT 1 AS a; SELECT 'x' AS b", "out": str(out), "writes": True,
                   "warehouse": {"server": SERVER, "database": "d"}}, lambda: "t", lambda event, **f: said.append((event, f)))
    assert pa.ipc.open_file(str(out)).read_all().to_pylist() == [{"b": "x"}, {"b": "y"}]
    assert said[-1] == ("done", {"rows": 2, "batches": [2], "columns": [{"name": "b", "type": "string"}],
                                 "affected": 5, "committed": True})
    assert con.events == ["commit", "close"]


def test_a_procedure_that_commits_is_refused_even_when_the_script_calls_it_later():
    v = warehouse.verdicts("CREATE PROCEDURE dbo.p AS BEGIN COMMIT; GRANT CONTROL TO someone; END\nGO\nEXEC dbo.p", writes=True)
    assert v[0].kind == "fabric_mutation" and sql_exec.sql_policy.refused(v, ("read_only", "warehouse_write")) is v[0]


# ---------------------------------------------------------------- estimated plan (WH-09)

PLAN_XML = '<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan"><BatchSequence/></ShowPlanXML>'


def test_an_estimated_plan_turns_showplan_on_around_the_query_and_returns_its_xml(tmp_path, monkeypatch):
    ran = []

    class Plan(_Cursor):
        def execute(self, query):
            ran.append(query)
            self.description = ([("Microsoft SQL Server 2005 XML Showplan", str, None, None, None, None, True)]
                                if not query.startswith("SET") else None)
            self._rows = [(PLAN_XML,)] if self.description else []
    con = _Con(Plan(None, []))
    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: con)
    said = []
    out = tmp_path / "r.arrow"
    warehouse.run({"query": "SELECT * FROM dbo.FactSales", "out": str(out), "plan": True,
                   "warehouse": {"server": SERVER, "database": "d"}}, lambda: "t", lambda event, **f: said.append((event, f)))
    assert ran == ["SET SHOWPLAN_XML ON", "SELECT * FROM dbo.FactSales", "SET SHOWPLAN_XML OFF"]
    assert pa.ipc.open_file(str(out)).read_all().to_pylist() == [{"Microsoft SQL Server 2005 XML Showplan": PLAN_XML}]
    assert said[-1][0] == "done" and con.events == ["rollback", "close"]


def test_an_estimated_plan_is_only_for_a_warehouse_query_that_reads(runs):
    with pytest.raises(ValueError, match="estimated plan is for a warehouse query that reads"):
        change(runs, "UPDATE dbo.t SET a = 1", estimated_plan=True)
    refused = start(runs, "UPDATE dbo.t SET a = 1", estimated_plan=True)
    assert refused["state"] == "failed" and refused["error_kind"] == "permission", "still only reads"
    status = wait(runs, start(runs, "SELECT id FROM dbo.sales", estimated_plan=True)["id"])
    assert status["state"] == "completed" and status["plan"] is True
    assert runs.audit_lines[-1][3]["plan"] is True and "sales" not in repr(runs.audit_lines[-1][3])
    seen = json.loads(runs.log.read_text())
    assert "committed" not in seen and seen["rolled_back"]
