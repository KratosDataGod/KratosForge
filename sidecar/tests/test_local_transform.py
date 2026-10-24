"""SQL transformations in a local project (FD-LDE-006): files run in dependency order, kept as tables."""

import json

import pytest
from deltalake import DeltaTable

from fabric_desktop import server
from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.transform import Transforms
from fabric_desktop.service import Service


@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.fixture
def engine(local):
    e = local.engine(local.create("T")["id"])
    e.execute("CREATE TABLE orders AS SELECT * FROM (VALUES (1, 'EU', 10.0, DATE '2026-01-05'), "
              "(2, 'US', 20.0, DATE '2026-02-01'), (3, 'EU', 30.0, DATE '2026-03-01')) t(id, region, amount, day)")
    return e


def step(engine, name, text, schema="dbo"):
    path = engine.project.root / "transforms" / schema / f"{name}.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def rows(engine, sql):
    return engine.execute(sql)["rows"]


def test_steps_run_in_dependency_order_and_keep_their_tables(engine):
    # Named so that alphabetical order would be wrong.
    step(engine, "a_report", "SELECT region, total FROM dbo.z_by_region WHERE total > 15 ORDER BY region")
    step(engine, "z_by_region", "SELECT region, sum(amount)::DOUBLE AS total FROM orders GROUP BY region")
    t = Transforms(engine)
    plan = t.plan()
    assert plan["order"] == ["dbo.z_by_region", "dbo.a_report"] and not plan["problem"]
    assert plan["steps"][1]["after"] == ["dbo.z_by_region"]
    run = t.run()
    assert run["status"] == "ok" and [(s["name"], s["status"], s["rows"]) for s in run["steps"]] == [
        ("dbo.z_by_region", "ok", 2), ("dbo.a_report", "ok", 2)]
    assert rows(engine, "SELECT * FROM a_report") == [["EU", 40.0], ["US", 20.0]]
    # Run again: the tables a run made are replaced, and every run is kept.
    engine.execute("INSERT INTO orders VALUES (4, 'APAC', 50.0, DATE '2026-04-01')")
    assert t.run()["status"] == "ok"
    assert rows(engine, "SELECT count(*) FROM a_report") == [[3]]
    assert len(t.runs()) == 2


def test_parameters_are_bound_with_defaults(engine):
    step(engine, "since", "-- default: start = \"2026-02-01\"\n"
                          "SELECT id FROM orders WHERE day >= $start::DATE ORDER BY id")
    step(engine, "spark_since", "-- dialect: spark\nSELECT id FROM orders WHERE region = :region ORDER BY id")
    t = Transforms(engine)
    run = t.run(params={"region": "EU"})
    assert run["status"] == "ok", run
    assert rows(engine, "SELECT * FROM since") == [[2], [3]]
    assert rows(engine, "SELECT * FROM spark_since") == [[1], [3]]
    # A value is a value: it can't change what the query does.
    t.run(params={"region": "EU' OR '1'='1", "start": "2026-01-01"})
    assert rows(engine, "SELECT count(*) FROM spark_since") == [[0]]
    assert rows(engine, "SELECT count(*) FROM since") == [[3]]


def test_a_missing_parameter_fails_its_step_and_skips_the_rest(engine):
    step(engine, "first", "SELECT * FROM orders WHERE region = $region")
    step(engine, "second", "SELECT count(*) AS n FROM first")
    run = Transforms(engine).run()
    assert run["status"] == "failed"
    assert [(s["name"], s["status"]) for s in run["steps"]] == [("dbo.first", "failed"), ("dbo.second", "skipped")]
    assert "$region" in run["steps"][0]["error"]


def test_a_delta_step_writes_a_fabric_safe_delta_table(engine):
    step(engine, "eu_orders", "-- materialize: delta\nSELECT id, amount FROM orders WHERE region = 'EU'", schema="silver")
    run = Transforms(engine).run()
    assert run["steps"][0]["status"] == "ok" and run["steps"][0]["version"] == 0
    assert DeltaTable(str(engine.tables_dir / "silver" / "eu_orders")).to_pyarrow_table().num_rows == 2
    assert Transforms(engine).run()["steps"][0]["version"] == 1  # replaced as a new version


def test_only_runs_the_chosen_steps_and_what_reads_them(engine):
    step(engine, "base", "SELECT * FROM orders")
    step(engine, "mid", "SELECT region FROM base")
    step(engine, "other", "SELECT 1 AS one")
    t = Transforms(engine)
    t.run()
    run = t.run(only=["mid"])
    assert [s["name"] for s in run["steps"]] == ["dbo.mid"]
    run = t.run(only=["base"])
    assert [s["name"] for s in run["steps"]] == ["dbo.base", "dbo.mid"]
    with pytest.raises(ValueError, match="No step named"):
        t.run(only=["nope"])


def test_a_step_never_replaces_a_table_it_didnt_make(engine):
    engine.execute("CREATE TABLE keep AS SELECT 42 AS answer")
    step(engine, "keep", "SELECT 1 AS answer")
    run = Transforms(engine).run()
    assert run["status"] == "failed" and "wasn't made by a transformation" in run["steps"][0]["error"]
    assert rows(engine, "SELECT * FROM keep") == [[42]]
    engine.execute("CREATE TABLE mine USING DELTA AS SELECT 1 AS a")
    (engine.project.root / "transforms" / "dbo" / "keep.sql").unlink()
    step(engine, "mine", "-- materialize: delta\nSELECT 2 AS a")
    assert Transforms(engine).run()["status"] == "failed"
    assert DeltaTable(str(engine.tables_dir / "dbo" / "mine")).version() == 0


def test_cycles_and_bad_files_are_refused_before_anything_runs(engine):
    step(engine, "a", "SELECT * FROM b")
    step(engine, "b", "SELECT * FROM a")
    t = Transforms(engine)
    assert "cycle" in t.plan()["problem"]
    with pytest.raises(ValueError, match="cycle"):
        t.run()
    for name in ("a", "b"):
        (engine.project.root / "transforms" / "dbo" / f"{name}.sql").unlink()
    step(engine, "two", "SELECT 1; SELECT 2")
    step(engine, "dml", "WITH c AS (SELECT 1) DELETE FROM orders")
    plan = t.plan()
    assert all("one query" in s["error"] for s in plan["steps"])
    with pytest.raises(ValueError, match="Fix these steps first"):
        t.run()
    assert rows(engine, "SELECT count(*) FROM orders") == [[3]]


def test_a_dry_run_checks_queries_and_makes_nothing(engine):
    step(engine, "base", "SELECT * FROM orders")
    step(engine, "top", "SELECT region FROM base")
    step(engine, "broken", "SELECT no_such_column FROM orders")
    t = Transforms(engine)
    run = t.run(dry_run=True)
    status = {s["name"]: s["status"] for s in run["steps"]}
    assert status["dbo.base"] == "checked" and status["dbo.broken"] == "failed"
    assert not engine._exists("dbo", "base") and not t.runs()
    (engine.project.root / "transforms" / "dbo" / "broken.sql").unlink()
    t.run()
    assert {s["status"] for s in t.run(dry_run=True)["steps"]} == {"checked"}


def test_depends_on_orders_steps_the_query_doesnt_name(engine):
    step(engine, "load", "SELECT 1 AS x")
    step(engine, "after", "-- depends_on: dbo.load\nSELECT 2 AS y")
    assert Transforms(engine).plan()["order"] == ["dbo.load", "dbo.after"]
    step(engine, "lost", "-- depends_on: dbo.nothing\nSELECT 3 AS z")
    assert "isn't a step" in Transforms(engine).plan()["problem"]


def test_app_calls_for_transformations(tmp_path):
    svc = Service(root=tmp_path / "app")
    assert {"local_transform_plan", "local_transform_run", "local_transform_runs"} <= server.METHODS

    def call(method, **params):
        reply = server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
        assert "error" not in reply, reply
        return reply["result"]
    pid = call("local_create_project", name="RPC")["id"]
    engine = svc.local_data.engine(pid)
    step(engine, "one", "SELECT $n::INT AS n")
    assert call("local_transform_plan", project=pid)["order"] == ["dbo.one"]
    assert call("local_transform_run", project=pid, params={"n": 7})["status"] == "ok"
    assert call("local_query", project=pid, query="SELECT n FROM one")["rows"] == [[7]]
    assert len(call("local_transform_runs", project=pid)["runs"]) == 1
    assert svc.audit.recent(10, "local_transform_run") and not svc.audit.recent(10, "local_transform_plan")
    svc.local_data.close_all()


def test_app_calls_check_each_step_like_a_managed_local_run(tmp_path):
    """local_transform_run and local_write_delta run their SQL in this process, so they make
    local_query's statement checks (sql_exec.check_local) before a step or write runs."""
    svc = Service(root=tmp_path / "app")
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\n42\n")

    def call(method, **params):
        return server.handle(svc, json.dumps({"id": 1, "method": method, "params": params}))
    pid = call("local_create_project", name="Checks")["result"]["id"]
    engine = svc.local_data.engine(pid)
    step(engine, "fine", "SELECT 1 AS a")
    step(engine, "grab", f"SELECT * FROM read_csv('{outside}')")
    for dry_run in (True, False):
        run = call("local_transform_run", project=pid, dry_run=dry_run)["result"]
        by_name = {s["name"]: s for s in run["steps"]}
        assert run["status"] == "failed" and "nothing ran" in by_name["dbo.grab"]["error"]
    assert call("local_query", project=pid, query="SELECT a FROM fine")["result"]["rows"] == [[1]]
    assert "error" in call("local_query", project=pid, query="SELECT * FROM grab")
    for mode in ("create", "append"):
        reply = call("local_write_delta", project=pid, table="dbo.stolen", query=f"SELECT * FROM '{outside}'",
                     mode=mode)
        assert "nothing ran" in reply.get("error", ""), reply
    assert not (engine.project.root / "Tables" / "dbo" / "stolen").exists()
    assert "result" in call("local_write_delta", project=pid, table="dbo.t", query="SELECT 1 AS a")
    reply = call("local_change_delta", project=pid,
                 query=f"MERGE INTO t USING read_csv('{outside}') s ON t.a = s.secret WHEN NOT MATCHED THEN "
                       "INSERT (a) VALUES (s.secret)")
    assert "nothing ran" in reply.get("error", ""), reply
    assert call("local_change_delta", project=pid, query="DELETE FROM t WHERE a = 1")["result"]["rows_deleted"] == 1
    svc.local_data.close_all()


def test_a_table_replaced_since_the_last_run_is_left_alone(engine):
    step(engine, "eu", "SELECT * FROM orders WHERE region = 'EU'")
    step(engine, "lake", "-- materialize: delta\nSELECT * FROM orders")
    t = Transforms(engine)
    assert t.run()["status"] == "ok" and t.run()["status"] == "ok"  # a rerun replaces its own tables
    engine.execute("CREATE OR REPLACE TABLE dbo.eu AS SELECT 'mine' AS who")
    engine.execute("INSERT INTO dbo.lake SELECT * FROM orders")
    r = t.run()
    assert r["status"] == "failed" and "has changed since" in r["steps"][0]["error"]
    assert rows(engine, "SELECT who FROM dbo.eu") == [["mine"]]
