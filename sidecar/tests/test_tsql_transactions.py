"""WR-245: a failure in a T-SQL change leaves nothing behind, in a Fabric Warehouse run and on the local engine.

A warehouse change runs in one transaction that the app commits only once every batch succeeded (warehouse.md).
With XACT_ABORT OFF a warehouse reports a statement's run-time error (8134, 245) and goes on to the next statement
of the batch, so the error arrives with a later result set, from ``nextset``: the run must still end without its
commit. With XACT_ABORT ON the warehouse has already rolled the transaction back, so the app's own rollback fails,
and the error to keep is the statement's. On this PC transactions are refused by name before anything runs, every
statement is all or nothing, and a process killed in the middle of one leaves the table as it was.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from fabric_desktop import sql_exec, warehouse
from fabric_desktop.localdata import LocalData

SIDECAR = Path(__file__).resolve().parents[1]
WH = {"server": "x.datawarehouse.fabric.microsoft.com", "database": "d"}


class Cursor:
    """A batch of statements: each a set of results, the way a warehouse reports them one after the other. A set
    that is an exception is the warehouse's error for that statement, raised as the driver reaches it."""

    def __init__(self, sets_by_batch):
        self._batches, self._sets, self.ran = list(sets_by_batch), [], []
        self.description, self.rowcount = None, -1

    def execute(self, query):
        self.ran.append(query)
        self._sets = list(self._batches.pop(0))
        self._next()

    def nextset(self):
        if not self._sets:
            return False
        self._next()
        return True

    def _next(self):
        found = self._sets.pop(0)
        if isinstance(found, Exception):
            raise found
        self.description, self.rowcount = None, found

    def fetchmany(self, size):
        return []


class Con:
    timeout = 0

    def __init__(self, cursor, rollback_fails=False):
        self._cursor, self.events, self._rollback_fails = cursor, [], rollback_fails

    def cursor(self):
        return self._cursor

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")
        if self._rollback_fails:
            raise RuntimeError("The ROLLBACK TRANSACTION request has no corresponding BEGIN TRANSACTION.")

    def close(self):
        self.events.append("close")


def change(tmp_path, monkeypatch, query, con):
    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: con)
    said = []
    warehouse.run({"query": query, "writes": True, "out": str(tmp_path / "r.arrow"), "warehouse": WH},
                  lambda: "t", lambda event, **f: said.append((event, f)))
    return said


DIVIDE = RuntimeError("[22012] Divide by zero error encountered. (8134)")


def test_an_error_that_arrives_with_a_later_statement_of_the_batch_is_not_committed(tmp_path, monkeypatch):
    """XACT_ABORT OFF: the warehouse ran the UPDATE, failed the SELECT and went on; the error comes from nextset."""
    con = Con(Cursor([[3, DIVIDE, 2]]))
    query = "SET XACT_ABORT OFF\nUPDATE dbo.t SET a = 1\nINSERT INTO dbo.u SELECT 1/0\nUPDATE dbo.t SET b = 2"
    with pytest.raises(warehouse.Interrupted) as caught:
        change(tmp_path, monkeypatch, query, con)
    assert "rolled back and nothing was kept" in str(caught.value) and "Divide by zero" in str(caught.value)
    assert con.events == ["rollback", "close"]


def test_a_later_batch_that_fails_rolls_back_the_batches_before_it(tmp_path, monkeypatch):
    con = Con(Cursor([[3], [DIVIDE]]))
    with pytest.raises(warehouse.Interrupted, match=r"Batch 2 of 2 \(UPDATE dbo\.u SET a = 1/0\) failed, so the whole "
                                                    r"change was rolled back"):
        change(tmp_path, monkeypatch, "UPDATE dbo.t SET a = 1\nGO\nUPDATE dbo.u SET a = 1/0", con)
    assert con.events == ["rollback", "close"] and len(con._cursor.ran) == 2


def test_with_xact_abort_on_the_warehouse_rolled_back_already_and_the_statements_error_is_kept(tmp_path, monkeypatch):
    con = Con(Cursor([[None], [DIVIDE]]), rollback_fails=True)
    with pytest.raises(warehouse.Interrupted) as caught:
        change(tmp_path, monkeypatch, "SET XACT_ABORT ON\nGO\nUPDATE dbo.u SET a = 1/0", con)
    assert "Divide by zero" in str(caught.value) and "ROLLBACK" not in str(caught.value)
    assert con.events == ["rollback", "close"]


def test_a_change_commits_only_after_every_set_of_every_batch_was_read(tmp_path, monkeypatch):
    con = Con(Cursor([[None, 3, 2], [4]]))
    said = change(tmp_path, monkeypatch, "SET XACT_ABORT ON\nUPDATE dbo.t SET a = 1\nDELETE FROM dbo.t\nGO\n"
                                         "INSERT INTO dbo.t SELECT 1", con)
    assert con.events == ["commit", "close"] and said[-1][1]["affected"] == 9 and said[-1][1]["committed"]


@pytest.mark.parametrize("setting", ["SET XACT_ABORT ON", "SET XACT_ABORT OFF"])
def test_xact_abort_is_allowed_in_a_change_and_in_its_own_transaction(setting):
    for query in (f"{setting}\nUPDATE dbo.t SET a = 1", f"BEGIN TRAN\n{setting}\nUPDATE dbo.t SET a = 1\nCOMMIT"):
        verdicts = warehouse.verdicts(query, writes=True)
        assert sql_exec.sql_policy.refused(verdicts, ("read_only", "warehouse_write")) is None, (query, verdicts)


# ---------------------------------------------------------------- on this PC


@pytest.fixture
def local(tmp_path):
    data = LocalData(tmp_path)
    pid = data.create("Transactions")["id"]
    engine = data.engine(pid)
    engine.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1), (2), (3)) v(x)")
    yield data, pid, engine
    data.close_all()


def count(engine, table="t"):
    return engine.execute(f"SELECT count(*) FROM {table}")["rows"][0][0]


@pytest.mark.parametrize("sql", [
    "BEGIN TRAN;\nINSERT INTO t SELECT 4;\nCOMMIT",
    "INSERT INTO t SELECT 4;\nBEGIN TRANSACTION;\nINSERT INTO t SELECT 5;\nROLLBACK",
    "INSERT INTO t SELECT 4;\nSAVE TRANSACTION s1",
])
def test_a_transaction_is_refused_before_any_statement_runs(local, sql):
    _, _, engine = local
    with pytest.raises(ValueError, match="transactions"):
        engine.execute(sql, dialect="tsql")
    assert count(engine) == 3


@pytest.mark.parametrize("sql", [
    "INSERT INTO t SELECT 10 / (x - 3) FROM t",  # the third row fails after two were made
    "UPDATE t SET x = 10 / (x - 3)",
    "DELETE FROM t WHERE 10 / (x - 3) > 0",
])
def test_a_statement_that_fails_part_way_changes_no_row(local, sql):
    _, _, engine = local
    with pytest.raises(Exception, match="8134|[Dd]ivi"):  # noqa: B017 - DuckDB's own error type
        engine.execute(sql, dialect="tsql")
    assert engine.execute("SELECT x FROM t ORDER BY x")["rows"] == [[1], [2], [3]]


def test_with_xact_abort_on_the_script_stops_and_keeps_what_ran_before_as_a_warehouse_does(local):
    """Outside a transaction a warehouse keeps the statements before the error with XACT_ABORT ON, too."""
    _, _, engine = local
    with pytest.raises(Exception, match="Statement 2 of 3") as caught:  # noqa: B017
        engine.execute("SET XACT_ABORT ON;\nINSERT INTO t SELECT 4;\nINSERT INTO t SELECT 1/0;\nINSERT INTO t SELECT 5",
                       dialect="tsql")
    assert "Statement 1 ran" in str(caught.value)
    assert engine.execute("SELECT x FROM t ORDER BY x")["rows"] == [[1], [2], [3], [4]]


KILLED = textwrap.dedent('''
    import sys
    sys.path.insert(0, {sidecar!r})
    from fabric_desktop.localdata import LocalData
    engine = LocalData({root!r}).engine({pid!r})
    print("go", flush=True)
    engine.execute({sql!r}, dialect="tsql")
    print("finished", flush=True)
''')


@pytest.mark.parametrize("sql, table, rows", [
    ("INSERT INTO t SELECT a.x FROM big a CROSS JOIN big b CROSS JOIN big c", "t", 3),
    ("SELECT a.x INTO made FROM big a CROSS JOIN big b CROSS JOIN big c", "made", None),
    ("UPDATE t SET x = (SELECT count(*) FROM big a CROSS JOIN big b CROSS JOIN big c)", "t", 3),
])
def test_a_process_killed_in_the_middle_of_a_statement_leaves_the_table_as_it_was(tmp_path, sql, table, rows):
    data = LocalData(tmp_path)
    pid = data.create("Killed")["id"]
    engine = data.engine(pid)
    engine.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1), (2), (3)) v(x)")
    engine.execute("CREATE TABLE big AS SELECT range AS x FROM range(4000)")
    data.close_all()
    script = tmp_path / "killed.py"
    script.write_text(KILLED.format(sidecar=str(SIDECAR), root=str(tmp_path), pid=pid, sql=sql), encoding="utf-8")
    child = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "go"
        time.sleep(1.5)
        assert child.poll() is None, "the statement finished before it could be killed"
        child.kill()  # SIGKILL, or TerminateProcess on Windows: nothing of the engine runs after it
    finally:
        child.wait(30)
    again = LocalData(tmp_path)
    try:
        engine = again.engine(pid)
        if rows is None:
            assert engine.execute(f"SELECT count(*) FROM duckdb_tables() WHERE table_name = '{table}'")["rows"] == [[0]]
        else:
            assert engine.execute(f"SELECT x FROM {table} ORDER BY x")["rows"] == [[1], [2], [3]]
    finally:
        again.close_all()
