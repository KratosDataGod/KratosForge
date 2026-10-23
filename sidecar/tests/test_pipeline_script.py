"""Script activities in local pipeline runs (pipelines plan P2): each script runs through the SQL
page's runner and policy, read-only on a lakehouse or in a local data project named for it."""

from __future__ import annotations

import json

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import sql_exec
from tests.test_pipeline_data import LS, start
from tests.test_pipeline_notebooks import act, by_name, finish, pipeline, svc  # noqa: F401

WAREHOUSE = {"name": "WH", "properties": {"type": "DataWarehouse", "typeProperties": {
    "artifactId": "wh1", "workspaceId": "ws1", "endpoint": "x.datawarehouse.fabric.microsoft.com"}}}


def script(name, *blocks, ls=LS, depends=(), **tp):
    a = act(name, "Script", depends, scripts=[{"type": t, "text": text} for t, text in blocks], **tp)
    a["linkedService"] = ls
    return a


@pytest.fixture
def sql(svc):
    limits = {"sql_page_rows": 1000, "sql_time_limit_minutes": 10, "sql_memory_limit_percent": 30,
              "sql_max_concurrent": 2, "sql_result_retention_hours": 24, "sql_temp_dir": ""}
    svc._sql_runs = sql_exec.SqlExecutions(svc.root, token=lambda: "tok", limits=lambda: limits,
                                           test_lake=svc.lh / "Tables" / "dbo",
                                           local_engine=lambda project: svc.local_data.engine(project))
    yield svc
    svc._sql_runs.shutdown()


def codes(svc, text, **kw):
    return [p["code"] for p in svc.pipeline_run_check(text=text, **kw)["problems"]]


def test_a_query_on_a_lakehouse_returns_its_rows(sql):
    text = pipeline(script("Totals", ("Query", "SELECT region, SUM(amount) AS total FROM orders GROUP BY region "
                                               "ORDER BY region"),
                           ("Query", "SELECT COUNT(*) AS n FROM orders")),
                    act("Use", "SetVariable", ["Totals"], variableName="v",
                        value="@string(activity('Totals').output.resultSets[1].rows[0].n)"),
                    variables={"v": {"type": "String"}})
    r = start(sql, text)
    assert r["state"] == "succeeded", r
    out = by_name(r)["Totals"]["output"]
    assert out["resultSetCount"] == 2 and out["outputTruncated"] is False
    assert out["resultSets"][0] == {"rowCount": 2, "rows": [{"region": "APAC", "total": 5.0},
                                                            {"region": "EMEA", "total": 30.0}]}
    assert r["variables"]["v"] == "3"


def test_a_lakehouse_is_only_read(sql):
    before = DeltaTable(str(sql.lh / "Tables" / "dbo" / "orders")).version()
    assert codes(sql, pipeline(script("Del", ("NonQuery", "DELETE FROM orders"))), read_lakehouses=True) == \
        ["script_refused"]
    # A script known only at run time is checked by the same policy before it runs.
    text = pipeline(script("Del", ("NonQuery", {"value": "@pipeline().parameters.sql", "type": "Expression"})),
                    parameters={"sql": {"type": "string", "defaultValue": "DELETE FROM orders"}})
    r = start(sql, text)
    assert r["state"] == "failed" and by_name(r)["Del"]["error"]["errorCode"] == "LocalScriptRefused"
    assert DeltaTable(str(sql.lh / "Tables" / "dbo" / "orders")).version() == before


def test_a_local_data_project_runs_its_scripts_and_keeps_its_tables(sql):
    project = sql.local_data.create("Pipeline SQL")["id"]
    text = pipeline(script("Build", ("NonQuery", "CREATE TABLE picked AS SELECT 7 AS n"),
                           ("Query", "SELECT n FROM picked")))
    r = start(sql, text, sql_projects={"Build": project})
    assert r["state"] == "succeeded", r
    assert by_name(r)["Build"]["output"]["resultSets"] == [{"rowCount": 1, "rows": [{"n": 7}]}]
    drop = pipeline(script("Drop", ("NonQuery", "DROP TABLE picked")))
    assert codes(sql, drop, sql_projects={"Drop": project}) == ["script_needs_confirm"]
    assert codes(sql, drop, sql_projects={"Drop": "nope"}) == ["script_project_missing"]


def test_a_project_can_be_named_for_the_connection(sql):
    project = sql.local_data.create("By connection")["id"]
    r = start(sql, pipeline(script("Q", ("Query", "SELECT 1 AS one"), ls=WAREHOUSE)), sql_projects={"WH": project})
    assert by_name(r)["Q"]["output"]["resultSets"][0]["rows"] == [{"one": 1}]


def test_results_keep_at_most_5000_rows(sql):
    write_deltalake(str(sql.lh / "Tables" / "dbo" / "big"), pa.table({"n": list(range(6000))}))
    r = start(sql, pipeline(script("Big", ("Query", "SELECT n FROM big"))))
    out = by_name(r)["Big"]["output"]
    assert out["resultSets"][0]["rowCount"] == 5000 and out["outputTruncated"] is True


def test_what_cant_run_here_is_refused_before_the_run(sql):
    assert codes(sql, pipeline(script("Q", ("Query", "SELECT 1")))) == ["data_needs_engine"]
    assert codes(sql, pipeline(script("W", ("Query", "SELECT 1"), ls=WAREHOUSE)), read_lakehouses=True) == \
        ["script_warehouse"]
    sqldb = {"name": "SQL", "properties": {"type": "AzureSqlDatabase", "typeProperties": {}}}
    assert codes(sql, pipeline(script("S", ("Query", "SELECT 1"), ls=sqldb)), read_lakehouses=True) == \
        ["script_connection"]
    params = script("P", ("Query", "SELECT 1"))
    params["typeProperties"]["scripts"][0]["parameters"] = [{"name": "x", "type": "Int32", "value": 1}]
    assert codes(sql, pipeline(params), read_lakehouses=True) == ["script_parameters"]
    own = json.loads(json.dumps(LS))
    del own["properties"]["typeProperties"]["workspaceId"]
    assert codes(sql, pipeline(script("O", ("Query", "SELECT 1"), ls=own)), read_lakehouses=True) == \
        ["data_workspace_unknown"]
    # Naming a project doesn't turn lakehouse reads on for the rest of the run.
    project = sql.local_data.create("Only this")["id"]
    text = pipeline(script("Q", ("Query", "SELECT 1")), script("L", ("Query", "SELECT 1")))
    assert codes(sql, text, sql_projects={"Q": project}) == ["data_needs_engine"]


def test_a_failing_script_fails_the_activity(sql):
    r = start(sql, pipeline(script("Bad", ("Query", "SELECT nope FROM orders"))))
    assert r["state"] == "failed" and by_name(r)["Bad"]["error"]["errorCode"] == "LocalScriptFailed"


def test_cancel_stops_a_running_script(sql):
    import time
    slow = "SELECT sum(a.range * b.range) AS s FROM range(200000000) a, range(1000) b"
    project = sql.local_data.create("Slow")["id"]
    rid = sql.pipeline_run_start(text=pipeline(script("Slow", ("Query", slow))), sql_projects={"Slow": project})["run_id"]
    end = time.monotonic() + 30
    while not any(r["state"] == "running" for r in sql.sql_runs.recent()) and time.monotonic() < end:
        time.sleep(0.05)
    sql.pipeline_run_cancel(rid)
    r = finish(sql, rid, limit=30)
    assert r["state"] == "cancelled"
    assert all(r["state"] != "running" for r in sql.sql_runs.recent())


def test_lakehouse_t_sql_is_translated_as_local_projects_translate_it(sql):
    r = start(sql, pipeline(script("Top", ("Query", "SELECT TOP 1 [id] FROM orders ORDER BY amount DESC"),
                                   ("Query", "SELECT 7 / 2 AS half"))))
    out = by_name(r)["Top"]["output"]
    assert r["state"] == "succeeded", r
    assert out["resultSets"][0]["rows"] == [{"id": 2}] and out["resultSets"][1]["rows"] == [{"half": 3}]
    assert codes(sql, pipeline(script("V", ("Query", "DECLARE @x INT = 1; SELECT @x"))), read_lakehouses=True) == \
        ["script_unreadable"]


def test_cli_runs_a_script_in_a_named_project(sql, tmp_path, capsys, monkeypatch):
    from fabric_desktop import cli
    monkeypatch.setattr(cli, "Service", lambda: sql)
    project = sql.local_data.create("CLI")["id"]
    path = tmp_path / "p.json"
    path.write_text(pipeline(script("Q", ("Query", "SELECT 2 AS two"))), encoding="utf-8")
    with pytest.raises(SystemExit) as done:
        cli.main(["pipeline", "run", str(path), "--sql-project", f"Q={project}", "--json"])
    record = json.loads(capsys.readouterr().out)
    assert done.value.code == 0 and by_name(record)["Q"]["output"]["resultSets"][0]["rows"] == [{"two": 2}]
