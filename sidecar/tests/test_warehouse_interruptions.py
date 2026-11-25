"""WR-207: a warehouse run the network or the warehouse ends part way says so by name.

A dropped connection, a sign-in that expires during a long query, throttling, and a cancel each end
with a named error that says what was kept and what to do next. None reports success for a partial
batch, and the run's status, audit line and history keep the batch that failed. The worker runs as a
real process with a stand-in driver that fails where the SQL says (markers below); nothing reaches Fabric.
"""

from __future__ import annotations

import json
import sys
import textwrap
import time

import pytest

from fabric_desktop import sql_exec, warehouse
from tests.test_warehouse_queries import SIDECAR, FakeFabric, limits, start, wait

# Markers in the SQL pick the failure: DROP (the link fails), EXPIRED (the sign-in), BUSY (40501),
# CAPACITY (Fabric's throttling text), CANCELME (the warehouse cancels), SLOWLY (the driver's time limit),
# FETCHDROP (the link fails after the first rows), COMMITDROP, COMMITSLOWLY and COMMITCANCEL (the commit
# is cut off by a drop, the time limit or a cancel), COMMITHANG
# (the commit never answers), CONFLICT (the commit is refused), HANG (a statement that never ends).
WORKER = textwrap.dedent('''
    import json, sys, time
    sys.path.insert(0, {sidecar!r})
    from fabric_desktop import sql_worker, warehouse
    LOG = {log!r}
    RAN = []
    FAILS = {{
        "DROP": "[Microsoft][ODBC Driver 18 for SQL Server]Communication link failure (08S01)",
        "EXPIRED": "Login failed for user '<token-identified principal>'. The token is expired. (18456)",
        "BUSY": "The service is currently busy. Retry the request after 10 seconds. (40501)",
        "CAPACITY": "Request was throttled: the capacity has exceeded its limits.",
        "CANCELME": "Operation canceled (HY008)",
        "SLOWLY": "Query timeout expired (HYT00)",
    }}

    class Cursor:
        def __init__(self):
            self.description, self.rows, self.rowcount = None, [], -1
        def nextset(self):
            return False
        def execute(self, q):
            RAN.append(q)
            if "HANG" in q and "COMMITHANG" not in q:
                time.sleep(60)
            for marker, text in FAILS.items():
                if marker in q and "FETCHDROP" not in q and "COMMIT" not in q:
                    raise RuntimeError(text)
            if q.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
                self.description, self.rowcount = None, 3
                return
            self.description = [("id", int, None, None, 10, 0, True)]
            self.rows = [(i,) for i in range(5)]
            self.fetched = 0
        def fetchmany(self, size):
            if "FETCHDROP" in RAN[-1] and self.fetched:
                raise RuntimeError(FAILS["DROP"])
            self.fetched += 1
            out, self.rows = self.rows[:2], self.rows[2:]
            return out

    class Connection:
        timeout = 0
        def __init__(self):
            self.seen = {{}}
        def cursor(self):
            return Cursor()
        def rollback(self):
            if any(m in q for q in RAN for m in ("DROP", "EXPIRED")):
                raise RuntimeError(FAILS["DROP"])  # the connection is gone, so rolling back fails too
            self.seen["rolled_back"] = True
        def commit(self):
            if any("COMMITHANG" in q for q in RAN):
                time.sleep(60)
            if any("COMMITDROP" in q for q in RAN):
                raise RuntimeError(FAILS["DROP"])
            if any("COMMITSLOWLY" in q for q in RAN):
                raise RuntimeError(FAILS["SLOWLY"])
            if any("COMMITCANCEL" in q for q in RAN):
                raise RuntimeError(FAILS["CANCELME"])
            if any("CONFLICT" in q for q in RAN):
                raise RuntimeError("Snapshot isolation transaction aborted due to update conflict. (24556)")
            self.seen["committed"] = True
        def close(self):
            self.seen["ran"] = list(RAN)
            open(LOG, "w").write(json.dumps(self.seen))

    warehouse.connect = lambda server, database, token, login_timeout=60: Connection()
    sql_worker.main()
''')


@pytest.fixture
def runs(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(WORKER.format(sidecar=str(SIDECAR), log=str(tmp_path / "log.txt")), encoding="utf-8")
    fabric, audit = FakeFabric(), []
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "STORAGE", audit=lambda *a: audit.append(a),
                                     limits=limits(), command=[sys.executable, str(script)],
                                     warehouse_token=lambda: "SECRET-SQL-TOKEN",
                                     resolve_warehouse=lambda ref: warehouse.resolve(fabric, ref))
    manager.audit_lines, manager.log = audit, tmp_path / "log.txt"
    yield manager
    manager.shutdown()


def change(manager, query):
    return start(manager, query, allow=["warehouse_write"], confirm=True, confirm_name="Sales WH")


def seen(manager):
    return json.loads(manager.log.read_text())


def audited(manager, run_id):
    return next(a[3] for a in manager.audit_lines if a[0] == "sql_execution" and a[3]["execution"] == run_id)


SCRIPT = "UPDATE dbo.sales SET a = 1\nGO\nUPDATE dbo.sales SET b = 2 -- {marker}\nGO\nUPDATE dbo.sales SET c = 3"


@pytest.mark.parametrize(("marker", "kind", "says"), [
    ("DROP", "connection", "The connection to the warehouse dropped during batch 2 of 3 (UPDATE dbo.sales SET b = 2"),
    ("EXPIRED", "signin", "Your sign-in to Fabric expired during batch 2 of 3"),
    ("BUSY", "throttled", "because its capacity is busy (error 40501)"),
    ("CAPACITY", "throttled", "because its capacity is busy, so"),
    ("CANCELME", "cancelled", "The warehouse cancelled the run during batch 2 of 3"),
])
def test_a_change_interrupted_mid_batch_is_named_rolled_back_and_keeps_its_batch(runs, marker, kind, says):
    status = wait(runs, change(runs, SCRIPT.format(marker=marker))["id"])
    assert status["state"] == "failed" and status["error_kind"] == kind
    assert says in status["error"]
    assert "so the whole change was rolled back and nothing was kept" in status["error"]
    assert status["committed"] is False and status["affected"] == 0  # no success for the batch that did run
    assert status["failed_batch"] == 2
    line = audited(runs, status["id"])
    assert (line["failed_batch"], line["error_kind"], line["committed"]) == (2, kind, False)
    ran = seen(runs)
    assert "committed" not in ran and len(ran["ran"]) == 2  # batch 3 was never sent


@pytest.mark.parametrize(("marker", "next_step"), [
    ("DROP", "Check this PC's network"),
    ("EXPIRED", "Sign in again, then run it again."),
    ("BUSY", "Wait a few minutes and run it again"),
    ("CANCELME", "check the warehouse's activity in Fabric"),
])
def test_each_interruption_says_what_to_do_next(runs, marker, next_step):
    status = wait(runs, start(runs, f"SELECT id FROM dbo.sales -- {marker}")["id"])
    assert status["state"] == "failed" and next_step in status["error"]
    assert "while the query ran" in status["error"] and "so no result was kept" in status["error"]
    assert status["failed_batch"] == 1


def test_a_connection_that_drops_after_the_first_rows_keeps_no_partial_result(runs):
    status = wait(runs, start(runs, "SELECT id FROM dbo.sales -- FETCHDROP")["id"])
    assert status["state"] == "failed" and status["error_kind"] == "connection"
    assert status["rows"] == 0 and not status["snapshot"]
    with pytest.raises(ValueError, match="no rows to show"):
        runs.page(status["id"], 0, 10)


def test_the_drivers_time_limit_is_a_timed_out_run(runs):
    status = wait(runs, change(runs, SCRIPT.format(marker="SLOWLY"))["id"])
    assert status["state"] == "timed_out" and status["error_kind"] == "timeout"
    assert status["error"].startswith("Stopped after the time limit (10 minutes, Settings › SQL).")
    assert status["error"].endswith("The change was rolled back, so nothing was kept.")
    assert status["failed_batch"] == 2 and status["committed"] is False


@pytest.mark.parametrize("marker", ["COMMITDROP", "COMMITSLOWLY", "COMMITCANCEL"])
def test_a_commit_cut_off_says_the_change_may_have_been_kept(runs, marker):
    status = wait(runs, change(runs, f"UPDATE dbo.sales SET a = 1 -- {marker}")["id"])
    assert status["state"] == "failed" and status["error_kind"] == "commit_unknown"
    assert "may or may not have been kept. Check the tables it changes" in status["error"]
    assert status["committed"] is False


def test_a_refused_commit_keeps_nothing(runs):
    status = wait(runs, change(runs, "UPDATE dbo.sales SET a = 1 -- CONFLICT")["id"])
    assert status["state"] == "failed" and status["error_kind"] == "sql"
    assert status["error"].startswith("The warehouse didn't commit the change, so nothing was kept.")
    assert "update conflict" in status["error"]


def test_stopping_a_change_before_its_commit_keeps_nothing(runs):
    run_id = change(runs, "UPDATE dbo.sales SET a = 1\nGO\nUPDATE dbo.sales SET b = 2 -- HANG")["id"]
    wait(runs, run_id, states=("running",))
    runs.cancel(run_id)
    status = wait(runs, run_id)
    assert status["state"] == "cancelled" and status["error"] == "Stopped. The change was rolled back, so nothing was kept."
    assert status["committed"] is False


def test_stopping_a_change_while_it_commits_says_it_may_have_been_kept(runs):
    run_id = change(runs, "UPDATE dbo.sales SET a = 1 -- COMMITHANG")["id"]
    for _ in range(400):
        if runs._runs[run_id].committing:
            break
        time.sleep(0.05)
    assert runs._runs[run_id].committing
    runs.cancel(run_id)
    status = wait(runs, run_id)
    assert status["state"] == "cancelled" and "may or may not have been kept" in status["error"]


@pytest.mark.parametrize(("text", "why"), [
    (("[Microsoft][ODBC Driver 18 for SQL Server]TCP Provider: An existing connection was forcibly closed by the "
      "remote host. (10054)"), ("connection", None)),
    ("A transport-level error has occurred when receiving results from the server.", ("connection", None)),
    ("The token has expired. AADSTS70043", ("signin", None)),
    ("Login failed for user '<token-identified principal>'. (18456)", ("signin", None)),
    ("Database 'WH' on server 'x' is not currently available. (40613)", ("throttled", 40613)),
    ("Query timeout expired (HYT00)", ("timeout", None)),
    ("Operation canceled (HY008)", ("cancelled", None)),
    ("Invalid column name 'nope'. (207)", None),
    ("Divide by zero error encountered. (8134)", None),
])
def test_the_driver_text_tells_an_interruption_from_the_sqls_own_error(text, why):
    assert warehouse.interruption(RuntimeError(text)) == why


def test_this_pcs_own_trouble_writing_a_result_keeps_its_own_message(tmp_path, monkeypatch):
    # A change whose batch returns rows, on a full disk: the disk's message, not "fix that batch" (Quality, #2779).
    import errno

    from fabric_desktop import sql_worker

    class Cursor:
        description, rowcount = [("id", int, None, None, 10, 0, True)], -1

        def execute(self, q):
            pass

        def fetchmany(self, size):
            raise OSError(errno.ENOSPC, "No space left on device")

        def nextset(self):
            return False

    class Connection:
        timeout = 0

        def cursor(self):
            return Cursor()

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: Connection())
    job = {"query": "UPDATE dbo.t SET a = 1\nGO\nSELECT id FROM dbo.t", "writes": True, "target": "warehouse",
           "out": str(tmp_path / "out.arrow"), "warehouse": {"server": "s", "database": "WH"}}
    with pytest.raises(OSError) as failed:
        warehouse.run(job, lambda: "tok", lambda *a, **k: None)
    assert not isinstance(failed.value, warehouse.Interrupted)
    kind, message = sql_worker.error_message(failed.value, job)
    assert kind == "disk" and "Fix that batch" not in message


def test_a_run_the_app_closes_on_is_in_the_audit_before_the_engine_exits(runs):
    # The reader that records how a run ended is a daemon thread; shutdown waits for it, so the line
    # is there when the engine's main thread returns and the process ends.
    run_id = change(runs, "UPDATE dbo.sales SET a = 1 -- HANG")["id"]
    wait(runs, run_id, states=("running",))
    runs.shutdown()
    line = audited(runs, run_id)
    assert line["execution"] == run_id and runs.status(run_id)["state"] == "cancelled"
