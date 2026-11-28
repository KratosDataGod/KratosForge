"""Editor parity S14: a script run statement by statement, each with a result of its own (sql_exec.py
``statements``, sql_worker.py, warehouse.py). Real worker processes over local Delta folders; the
warehouse is a fake connection in process. Nothing reaches Fabric or OneLake.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop import sql_exec, warehouse
from tests.test_sql_exec import limits, wait, wait_export

SERVER = "abc.datawarehouse.fabric.microsoft.com"


@pytest.fixture
def lake(tmp_path):
    write_deltalake(str(tmp_path / "lake" / "sales"), pa.table({"id": [1, 2, 3], "country": ["NL", "DE", "US"]}))
    return {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Test"}


@pytest.fixture
def runs(tmp_path):
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "SECRET-TOKEN", audit=lambda *a: None,
                                     limits=limits(), downloads=lambda: tmp_path / "Downloads",
                                     test_lake=tmp_path / "lake")
    yield manager
    manager.shutdown()


def test_each_statement_has_a_result_of_its_own(runs, lake):
    status = wait(runs, runs.start("", lake, statements=[
        "SELECT count(*) AS n FROM sales", "SELECT id FROM sales WHERE country <> 'NL' ORDER BY id",
        "SELECT 'done' AS word"])["id"])
    assert status["state"] == "completed" and status["statements"] == 3 and status["failed_statement"] == 0
    assert [(p["statement"], p["rows"]) for p in status["parts"]] == [(1, 1), (2, 2), (3, 1)]
    assert "batches" not in status["parts"][0] and "offsets" not in status["parts"][0]
    assert runs.page(status["id"], part=1)["rows"] == [[3]]
    two = runs.page(status["id"], part=2)
    assert two["rows"] == [[2], [3]] and two["columns"] == [{"name": "id", "type": "int64"}] and two["part"] == 2
    assert runs.page(status["id"])["rows"] == [["done"]], "the run's own result is its last statement's"


def test_statements_share_one_session(runs, lake):
    status = wait(runs, runs.start("", lake, allow=["local_mutation"], statements=[
        "CREATE TEMP VIEW v AS SELECT id * 10 AS x FROM sales", "SELECT count(*) AS n FROM v WHERE x > 10"])["id"])
    assert status["state"] == "completed"
    assert status["parts"][0]["rows"] <= 1, "DuckDB may say a count for a view it made"
    assert runs.page(status["id"], part=2)["rows"] == [[2]]


def test_the_run_stops_at_the_first_statement_that_fails_and_keeps_the_ones_before(runs, lake):
    status = wait(runs, runs.start("", lake, statements=[
        "SELECT id FROM sales ORDER BY id", "SELECT * FROM no_such_table", "SELECT 3 AS never"])["id"])
    assert status["state"] == "failed" and status["failed_statement"] == 2
    assert [p["statement"] for p in status["parts"]] == [1]
    assert runs.page(status["id"], part=1)["rows"] == [[1], [2], [3]], "a finished statement can still be read"
    with pytest.raises(ValueError, match="Statement 3 has no result"):
        runs.page(status["id"], part=3)
    with pytest.raises(ValueError, match="failed"):
        runs.page(status["id"])
    folder = runs._get(status["id"]).folder
    assert sorted(f.name for f in folder.iterdir()) == ["result-1.arrow"]


def test_a_statement_that_cant_be_read_is_named(runs, lake):
    status = wait(runs, runs.start("", lake, statements=["SELECT 1 AS a", "SELEC oops FROM"])["id"])
    assert status["state"] == "failed" and status["failed_statement"] == 2 and "Statement 2" in status["error"]
    assert status["parts"] == [], "nothing ran: every statement is read before the first runs"


def test_a_refusal_names_the_script_statement_and_nothing_runs(runs, lake):
    status = wait(runs, runs.start("", lake, statements=["SELECT 1 AS a; SELECT 2 AS b", "DELETE FROM sales"])["id"])
    assert status["state"] == "failed" and status["error_kind"] == "permission"
    assert status["refused"]["statement"] == 2 and status["error"].startswith("Statement 2 ")
    assert status["parts"] == []


def test_one_statements_result_sorts_filters_and_profiles_on_its_own(runs, lake):
    status = wait(runs, runs.start("", lake, statements=["SELECT id, country FROM sales", "SELECT 'done' AS word"])["id"])
    one = runs.page(status["id"], sort=[{"column": 0, "desc": True}], filters=[{"column": 1, "text": "!= NL"}], part=1)
    assert one["rows"] == [[3, "US"], [2, "DE"]] and one["total"] == 2 and one["all_rows"] == 3 and one["part"] == 1
    assert runs.page(status["id"], sort=[{"column": 0, "desc": True}])["rows"] == [["done"]], "without part: the last statement"
    stats = runs.column_stats(status["id"], 0, part=1)
    assert stats["part"] == 1 and "part" not in runs.column_stats(status["id"], 0)
    with pytest.raises(ValueError, match="Statement 3 has no result"):
        runs.column_stats(status["id"], 0, part=3)


def test_one_statement_can_be_exported(runs, lake, tmp_path):
    status = wait(runs, runs.start("", lake, statements=["SELECT id FROM sales", "SELECT 1 AS one"])["id"])
    out = wait_export(runs, runs.export(status["id"], "csv", "q", part=1)["id"])
    assert out["state"] == "completed" and out["rows"] == 3 and out["total"] == 3


@pytest.mark.parametrize("kwargs,match", [
    ({"statements": []}, "1 to"),
    ({"statements": ["SELECT 1", "  "]}, "none of them empty"),
    ({"statements": ["SELECT 1"] * (sql_exec.MAX_STATEMENTS + 1)}, "1 to"),
    ({"statements": ["SELECT 1"], "profile": True}, "lakehouse or a warehouse"),
    ({"statements": ["SELECT 1"], "estimated_plan": True}, "lakehouse or a warehouse"),
])
def test_statements_are_checked(runs, lake, kwargs, match):
    with pytest.raises(ValueError, match=match):
        runs.start("", lake, **kwargs)


def test_a_dry_run_numbers_each_statement(runs, lake):
    plan = runs.start("", lake, dry_run=True, statements=["SELECT 1", "CREATE TEMP VIEW v AS SELECT 1"])
    assert [s["permission"] for s in plan["statements"]] == ["read_only", "local_mutation"]


# ---------------------------------------------------------------- the warehouse


def test_warehouse_reads_are_checked_one_statement_at_a_time():
    ok = warehouse.unit_verdicts(["SELECT 1 AS a", "SELECT b FROM dbo.t"])
    assert [(v.statement, v.kind) for v in ok] == [(1, "read_only"), (2, "read_only")]
    bad = warehouse.unit_verdicts(["SELECT 1 AS a", "DELETE FROM dbo.t", "SELECT 2"])
    stop = sql_exec.sql_policy.refused(bad, ("read_only",))
    assert stop is not None and stop.statement == 2
    two = warehouse.unit_verdicts(["SELECT 1 AS a; SELECT 2 AS b"])
    assert sql_exec.sql_policy.refused(two, ("read_only",)) is not None, "each part is still one query"


def test_a_change_wrapped_in_begin_and_commit_loses_them():
    assert warehouse.change_units(["BEGIN TRAN", "UPDATE dbo.t SET a = 1", "COMMIT;"]) == ["UPDATE dbo.t SET a = 1"]
    assert warehouse.change_units(["BEGIN TRAN", "COMMIT"]) == ["BEGIN TRAN", "COMMIT"]
    assert warehouse.join_units(["SELECT 1", "SELECT 2"]) == "SELECT 1\nGO\nSELECT 2"


class _Sets:
    """A cursor whose statements give a result set (rows) or a row count, keyed by the SQL text."""

    def __init__(self, answers):
        self.answers, self.description, self._rows, self.rowcount = answers, None, [], -1
        self.ran = []

    def execute(self, query):
        self.ran.append(query)
        answer = self.answers[query]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            self.description, self._rows, self.rowcount = None, [], answer
        else:
            name, rows = answer
            self.description, self._rows, self.rowcount = [(name, int, None, None, 10, 0, True)], rows, -1

    def fetchmany(self, size):
        out, self._rows = self._rows[:size], self._rows[size:]
        return out

    def nextset(self):
        return False


class _Con:
    timeout = 0

    def __init__(self, cursor):
        self._cursor, self.events = cursor, []

    def cursor(self):
        return self._cursor

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.events.append("close")


def _run_wh(tmp_path, monkeypatch, answers, units, writes=False):
    cur = _Sets(answers)
    con = _Con(cur)
    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: con)
    said = []
    job = {"query": warehouse.join_units(units), "statements": units, "parts_dir": str(tmp_path),
           "out": str(tmp_path / "result.arrow"), "writes": writes, "warehouse": {"server": SERVER, "database": "d"}}
    try:
        warehouse.run(job, lambda: "t", lambda event, **f: said.append((event, f)))
    except Exception as exc:  # noqa: BLE001 - the test looks at what was said before it
        said.append(("raised", {"error": str(exc)}))
    return said, con, cur


def test_a_warehouse_script_reads_one_statement_at_a_time_on_one_connection(tmp_path, monkeypatch):
    said, con, cur = _run_wh(tmp_path, monkeypatch, {"SELECT 1 AS a": ("a", [(1,)]), "SELECT b FROM dbo.t": ("b", [(2,), (3,)])},
                             ["SELECT 1 AS a", "SELECT b FROM dbo.t"])
    parts = [f for e, f in said if e == "part"]
    assert [(p["statement"], p["rows"]) for p in parts] == [(1, 1), (2, 2)]
    assert pa.ipc.open_file(str(tmp_path / "result-2.arrow")).read_all().to_pylist() == [{"b": 2}, {"b": 3}]
    assert said[-1][0] == "done" and con.events == ["rollback", "close"], "a read is still rolled back"
    assert cur.ran == ["SELECT 1 AS a", "SELECT b FROM dbo.t"]


def test_a_warehouse_change_says_what_each_statement_changed_and_commits_once(tmp_path, monkeypatch):
    units = ["UPDATE dbo.t SET a = 1", "SELECT a FROM dbo.t"]
    said, con, _cur = _run_wh(tmp_path, monkeypatch, {units[0]: 14, units[1]: ("a", [(1,)])}, units, writes=True)
    parts = [f for e, f in said if e == "part"]
    assert parts[0]["affected"] == 14 and parts[0]["rows"] == 0 and "affected" not in parts[1]
    assert said[-1][1]["affected"] == 14 and con.events == ["commit", "close"]


def test_a_warehouse_change_stops_at_the_statement_that_fails_and_rolls_back(tmp_path, monkeypatch):
    units = ["UPDATE dbo.t SET a = 1", "SELECT * FROM dbo.nope", "SELECT 1 AS a"]
    said, con, cur = _run_wh(tmp_path, monkeypatch, {units[0]: 3, units[1]: RuntimeError("Invalid object name 'dbo.nope'."),
                                                     units[2]: ("a", [(1,)])}, units, writes=True)
    assert [f["statement"] for e, f in said if e == "part"] == [1]
    assert said[-1][0] == "raised" and "Statement 2 of 3" in said[-1][1]["error"] and "rolled back" in said[-1][1]["error"]
    assert cur.ran == units[:2] and con.events == ["rollback", "close"]


# ---------------------------------------------------------------- pinned results (S27)


def test_a_pinned_result_outlives_the_runs_kept_and_unpinning_lets_it_go(runs, lake, monkeypatch):
    monkeypatch.setattr(sql_exec, "MAX_KEPT", 1)
    first = wait(runs, runs.start("SELECT 1 AS a", lake)["id"])
    assert runs.pin(first["id"])["pinned"] is True
    for _ in range(2):
        wait(runs, runs.start("SELECT 2 AS b", lake)["id"])
    assert runs.page(first["id"])["rows"] == [[1]], "kept past MAX_KEPT while pinned"
    assert "pinned" not in runs.pin(first["id"], False)
    with pytest.raises(KeyError):
        runs.status(first["id"])


def test_only_so_many_results_can_be_pinned(runs, lake, monkeypatch):
    monkeypatch.setattr(sql_exec, "MAX_PINNED", 1)
    a = wait(runs, runs.start("SELECT 1 AS a", lake)["id"])
    b = wait(runs, runs.start("SELECT 2 AS a", lake)["id"])
    runs.pin(a["id"])
    with pytest.raises(ValueError, match="Up to 1 results"):
        runs.pin(b["id"])
    failed = wait(runs, runs.start("SELECT * FROM nothing_here", lake)["id"])
    with pytest.raises(ValueError, match="has none"):
        runs.pin(failed["id"])
