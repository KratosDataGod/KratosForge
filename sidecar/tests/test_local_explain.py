"""FD-LDE-017 (first part): how the local engine runs a query, with each operator's rows and time."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import server
from fabric_desktop.localdata import LocalData
from fabric_desktop.service import Service


@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Explain")["id"])
    e.execute("CREATE TABLE dbo.orders AS SELECT i AS id, i % 7 AS shop, i * 1.5 AS amount FROM range(50000) t(i)")
    return e


def _operators(plan):
    for node in plan:
        yield node["operator"]
        yield from _operators(node["children"])


def test_a_plan_without_running_the_query(engine):
    r = engine.explain("SELECT shop, sum(amount) FROM dbo.orders WHERE id > 10 GROUP BY shop")
    assert r["mode"] == "plan" and r["totals"] is None
    names = list(_operators(r["plan"]))
    assert any("GROUP_BY" in n for n in names) and any("SCAN" in n for n in names)
    assert "GROUP_BY" in r["text"] and r["engine"] == "local"


def test_analyze_reports_rows_time_and_memory(engine):
    r = engine.explain("SELECT shop, sum(amount) AS total FROM dbo.orders GROUP BY shop", analyze=True)
    t = r["totals"]
    assert r["mode"] == "analyze" and t["rows"] == 7 and t["rows_scanned"] >= 50000
    assert t["seconds"] >= 0 and t["peak_memory_bytes"] >= 0 and t["spilled_bytes"] == 0
    assert r["slowest"] and all(0 <= s["share"] <= 1 for s in r["slowest"])
    assert "EXPLAIN_ANALYZE" not in r["text"] and "rows=7" in r["text"]


def test_profiling_is_off_again_and_nothing_reaches_stdout(engine, capfd):
    engine.explain("SELECT count(*) FROM dbo.orders", analyze=True)
    after = engine.execute("SELECT current_setting('enable_profiling') AS p")["rows"]
    out, _err = capfd.readouterr()
    assert after in ([[None]], [[""]]) and out == ""  # stdout carries the engine's RPC


@pytest.mark.parametrize("sql", [
    "DELETE FROM dbo.orders",
    "WITH c AS (SELECT 1) DELETE FROM dbo.orders",
    "CREATE TABLE dbo.x AS SELECT 1",
    "SELECT 1; SELECT 2",
    "INSERT INTO dbo.orders SELECT * FROM dbo.orders",
])
def test_only_one_query_that_reads_is_explained_and_nothing_changes(engine, sql):
    with pytest.raises(ValueError, match="one query that reads"):
        engine.explain(sql, analyze=True)
    assert engine.execute("SELECT count(*) FROM dbo.orders")["rows"] == [[50000]]
    assert engine.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'x'")["rows"] == [[0]]


def test_spark_sql_and_delta_tables_can_be_explained(engine):
    engine.execute("CREATE TABLE dbo.sales USING DELTA AS SELECT * FROM dbo.orders")
    r = engine.explain("SELECT shop, count(*) FROM sales VERSION AS OF 0 GROUP BY shop", dialect="spark",
                       analyze=True)
    assert r["totals"]["rows"] == 7 and r["dialect"] == "spark"


def test_the_rpc_call_reads_only_and_isnt_noted_as_a_change(tmp_path):
    from fabric_desktop import audit
    svc = Service(root=tmp_path / "app")
    assert "local_explain" in server.METHODS and "local_explain" in audit.READ_ONLY

    def call(method, **params):
        reply = server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
        assert "error" not in reply, reply
        return reply["result"]
    pid = call("local_create_project", name="RPC")["id"]
    r = call("local_explain", project=pid, query="SELECT 42 AS answer", analyze=True)
    assert r["totals"]["rows"] == 1
    assert not svc.audit.recent(10, "local_explain")
