"""Editor parity S17: query parameters, bound as typed values (sql_params.py, sql_worker.py, warehouse.py).
Real worker processes over local Delta folders; the warehouse is a fake connection in process."""

from __future__ import annotations

import datetime as dt
import decimal

import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop import sql_exec, sql_params, warehouse
from tests.test_sql_exec import SLOW, limits, wait

SERVER = "abc.datawarehouse.fabric.microsoft.com"


@pytest.fixture
def lake(tmp_path):
    write_deltalake(str(tmp_path / "lake" / "sales"), pa.table({
        "id": [1, 2, 3], "region": ["West", "East", "West"],
        "day": [dt.date(2026, 9, 30), dt.date(2026, 10, 1), dt.date(2026, 10, 2)]}))
    return {"workspace_id": "ws", "lakehouse_id": "lh", "name": "LH_Test"}


@pytest.fixture
def runs(tmp_path):
    manager = sql_exec.SqlExecutions(tmp_path / "data", token=lambda: "SECRET-TOKEN", audit=lambda *a: manager.lines.append(a),
                                     limits=limits(), downloads=lambda: tmp_path / "Downloads", test_lake=tmp_path / "lake")
    manager.lines = []  # type: ignore[attr-defined]
    yield manager
    manager.shutdown()


def test_parameters_are_found_outside_strings_comments_and_casts():
    text = "SELECT ':no', a::int, \"x:y\" -- :nope\nFROM t /* {{nope}} */ WHERE r = :region AND d >= {{ start_date }} AND x = :region"
    assert sql_params.names(text) == ["region", "start_date"]
    assert sql_params.as_colons(text).endswith("WHERE r = :region AND d >= :start_date AND x = :region")
    assert sql_params.names("SELECT [a:b], :c FROM t", tsql=True) == ["c"]
    assert sql_params.names("SELECT col:field FROM t") == [], "a path after a name isn't a parameter"


def test_positional_parameters_keep_their_order_and_repeat():
    text, values = sql_params.as_positional("SELECT * FROM t WHERE a = :x AND b = {{y}} OR c = :x", {"x": 1, "y": "z"})
    assert text == "SELECT * FROM t WHERE a = ? AND b = ? OR c = ?" and values == [1, "z", 1]
    with pytest.raises(ValueError, match=":y"):
        sql_params.as_positional("SELECT :y", {})


@pytest.mark.parametrize("kind,value,expected", [
    ("text", " West ", " West "), ("number", "42", 42), ("number", "3.50", decimal.Decimal("3.50")),
    ("date", "2026-10-01", dt.date(2026, 10, 1)), ("datetime", "2026-10-01 14:30", dt.datetime(2026, 10, 1, 14, 30)),
    ("boolean", "True", True),
])
def test_values_take_their_type(kind, value, expected):
    assert sql_params.typed([{"name": "p", "type": kind, "value": value}]) == {"p": expected}


@pytest.mark.parametrize("kind,value,match", [
    ("date", "1 Oct", "write it as 2026-10-01"), ("date", "2026-13-01", "2026-10-01"), ("number", "4,2", "42 or 3.5"),
    ("datetime", "2026-10-01", "14:30"), ("boolean", "yes", "true or false"), ("money", "1", "type must be"),
])
def test_a_value_that_doesnt_fit_names_the_parameter(kind, value, match):
    with pytest.raises(ValueError, match=match):
        sql_params.typed([{"name": "start_date", "type": kind, "value": value}])


def test_a_lakehouse_run_binds_typed_values_and_never_keeps_them(runs, lake):
    region = "West"
    status = wait(runs, runs.start("SELECT id FROM sales WHERE region = :region AND day >= {{start}} ORDER BY id", lake,
                                   params=[{"name": "region", "type": "text", "value": region},
                                           {"name": "start", "type": "date", "value": "2026-10-01"}])["id"])
    assert status["state"] == "completed", status["error"]
    assert runs.page(status["id"])["rows"] == [[3]]
    assert region not in repr(status) and region not in repr(runs.lines) and runs._get(status["id"]).params == []


def test_the_values_leave_the_run_when_its_worker_starts(runs, lake):
    run = runs.start(SLOW + " WHERE a.range > :floor", lake, params=[{"name": "floor", "type": "number", "value": "-1"}])
    wait(runs, run["id"], ("running",))
    assert runs._get(run["id"]).params == [], "the worker has them; the run doesn't keep them while it runs"
    runs.cancel(run["id"])
    wait(runs, run["id"])


def test_a_backslash_ends_a_duckdb_string_but_not_a_spark_one():
    text = "SELECT 'C:\\' AS p, {{x}} AS x"
    assert sql_params.as_colons(text, backslash=False) == "SELECT 'C:\\' AS p, :x AS x"
    assert sql_params.as_colons(text) == text, "in Spark, \\' doesn't end the string, so {{x}} is inside it"
    assert sql_params.names("SELECT l[:n] FROM t", backslash=False) == ["n"], "a DuckDB list slice reads as a parameter"
    assert sql_params.names("SELECT l[1:n] FROM t", backslash=False) == []


def test_a_duckdb_query_with_a_backslash_in_a_string_still_finds_its_parameter(runs, lake):
    status = wait(runs, runs.start("SELECT 'C:\\' AS p, {{x}} AS x", lake, dialect="duckdb",
                                   params=[{"name": "x", "type": "number", "value": "7"}])["id"])
    assert status["state"] == "completed", status["error"]
    assert runs.page(status["id"])["rows"] == [["C:\\", 7]]


def test_a_value_is_never_sql(runs, lake):
    status = wait(runs, runs.start("SELECT count(*) AS n FROM sales WHERE region = :region", lake,
                                   params=[{"name": "region", "type": "text", "value": "x' OR '1'='1"}])["id"])
    assert runs.page(status["id"])["rows"] == [[0]]


def test_each_statement_gets_the_values_it_uses(runs, lake):
    status = wait(runs, runs.start("", lake, statements=["SELECT :a AS a", "SELECT 1 AS one", "SELECT :b + 1 AS b"],
                                   params=[{"name": "a", "type": "text", "value": "x"}, {"name": "b", "type": "number", "value": "41"}])["id"])
    assert status["state"] == "completed", status["error"]
    assert runs.page(status["id"], part=1)["rows"] == [["x"]] and runs.page(status["id"], part=3)["rows"] == [[42]]


def test_a_missing_value_or_a_bad_one_fails_before_running(runs, lake):
    status = wait(runs, runs.start("SELECT :nope AS x", lake, params=[])["id"])
    assert status["state"] == "failed" and ":nope" in status["error"]
    with pytest.raises(ValueError, match="2026-10-01"):
        runs.start("SELECT :d AS x", lake, params=[{"name": "d", "type": "date", "value": "1 Oct"}])


class _Cursor:
    description, rowcount = None, -1

    def __init__(self):
        self.ran = []

    def execute(self, query, *values):
        self.ran.append((query, values))
        self.description, self._rows = [("x", int, None, None, 10, 0, True)], [(1,)]

    def fetchmany(self, size):
        out, self._rows = self._rows, []
        return out

    def nextset(self):
        return False


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


def test_a_warehouse_query_sends_the_values_as_driver_parameters(tmp_path, monkeypatch):
    cur = _Cursor()
    monkeypatch.setattr(warehouse, "connect", lambda *a, **k: _Con(cur))
    warehouse.run({"query": "SELECT x FROM dbo.t WHERE r = :region AND d >= :start", "out": str(tmp_path / "r.arrow"),
                   "params": [{"name": "region", "type": "text", "value": "West"}, {"name": "start", "type": "date", "value": "2026-10-01"}],
                   "warehouse": {"server": SERVER, "database": "d"}}, lambda: "t", lambda *a, **k: None)
    assert cur.ran == [("SELECT x FROM dbo.t WHERE r = ? AND d >= ?", ("West", dt.date(2026, 10, 1)))]


def test_a_warehouse_run_reads_braces_as_parameters_too(runs, lake, tmp_path):
    with pytest.raises(ValueError, match="lakehouse or a warehouse"):
        runs.start("SELECT 1", project="p", target="local", params=[])
    plan = runs.start("SELECT x FROM dbo.t WHERE r = {{region}}", dialect="tsql", target="warehouse", dry_run=True,
                      warehouse_ref={"workspace_id": "w", "item_id": "i", "kind": "warehouse"},
                      params=[{"name": "region", "type": "text", "value": "West"}])
    assert plan["refused"] is None and plan["permission"] == "read_only"


def test_the_audit_line_keeps_neither_the_statements_nor_the_values():
    from fabric_desktop import audit
    line = audit.target({"query": "SELECT 1", "statements": ["SELECT secret_col FROM t"],
                         "params": [{"name": "region", "type": "text", "value": "West"}]})
    assert line == {"query": "(given)", "statements": "(given)", "params": "(given)"}
