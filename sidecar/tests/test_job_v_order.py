"""A job whose target says v_order = true queues Fabric's table maintenance job with V-Order after its
write commits (publish.run_job, #2361). Fakes only: no Fabric call is made."""

from __future__ import annotations

import json

import pytest
from deltalake import DeltaTable
from test_spark_write_types import lake  # noqa: F401 - lake is a fixture

from fabric_desktop import fabric_api, jobs, publish
from fabric_desktop.onelake import TableRef


class Fabric:
    def __init__(self, fail=None):
        self.maintenance, self.fail = [], fail
        self.version_when_queued = None
        self.version = lambda: None

    def find_lakehouse(self, workspace_id, name):
        return {"id": "lh1"}

    def run_table_maintenance(self, workspace_id, lakehouse_id, table, schema=None, **kw):
        self.maintenance.append({"workspace_id": workspace_id, "lakehouse_id": lakehouse_id, "table": table,
                                 "schema": schema, **kw})
        self.version_when_queued = self.version()
        if self.fail:
            raise self.fail
        return "job-42"


def _job(tmp_path, v_order, table="orders", schema="dbo", mode="overwrite"):
    target = jobs.Target("ws1", "LH", table, schema, mode, v_order=v_order)
    return jobs.Job("orders", "duckdb", tmp_path, target, sql="SELECT range::BIGINT AS id FROM range(3)")


def test_v_order_is_queued_once_after_the_write_commits_and_vacuums_nothing(lake, tmp_path):  # noqa: F811
    fabric = Fabric()
    fabric.version = lambda: DeltaTable(str(lake / "dbo" / "orders")).version()
    history = tmp_path / "history.jsonl"
    record = publish.run_job(_job(tmp_path, True), fabric, lambda: "tok", tmp_path, history_file=history)
    assert fabric.maintenance == [{"workspace_id": "ws1", "lakehouse_id": "lh1", "table": "orders", "schema": "dbo",
                                   "v_order": True, "vacuum_retention": None}]
    assert fabric.version_when_queued == record.delta_version == 0  # queued after the commit, not before
    assert record.v_order_job == "job-42" and record.v_order_error is None
    assert json.loads(history.read_text(encoding="utf-8"))["v_order_job"] == "job-42"


def test_without_v_order_no_fabric_job_is_queued(lake, tmp_path):  # noqa: F811
    fabric = Fabric()
    record = publish.run_job(_job(tmp_path, False), fabric, lambda: "tok", tmp_path)
    assert fabric.maintenance == [] and record.v_order_job is None and record.v_order_error is None


def test_a_v_order_that_cant_be_queued_is_reported_and_the_written_rows_stay(lake, tmp_path):  # noqa: F811
    fabric = Fabric(fail=RuntimeError("Capacity is paused"))
    fabric.version = lambda: DeltaTable(str(lake / "dbo" / "orders")).version()
    record = publish.run_job(_job(tmp_path, True), fabric, lambda: "tok", tmp_path)
    assert record.v_order_job is None
    assert record.v_order_error == ("Wrote dbo.orders (Delta version 0), but Fabric didn't start V-Order for it "
                                    "(RuntimeError: Capacity is paused). The rows are in the table; to V-Order it, "
                                    "run table maintenance on it in Fabric.")
    table = DeltaTable(str(lake / "dbo" / "orders"))
    assert table.version() == 0 and sorted(table.to_pyarrow_table().column("id").to_pylist()) == [0, 1, 2]


def test_a_refused_write_queues_no_v_order(lake, tmp_path):  # noqa: F811
    fabric = Fabric()
    with pytest.raises(PermissionError):
        publish.run_job(_job(tmp_path, True), fabric, lambda: "tok", tmp_path, guard=lambda *a: "Prod is locked")
    assert fabric.maintenance == []


def test_a_job_file_reads_v_order(tmp_path):
    path = tmp_path / "orders.toml"
    path.write_text('name = "orders"\nengine = "duckdb"\nsql = "SELECT 1 AS id"\n[target]\nworkspace_id = "ws1"\n'
                    'lakehouse = "LH"\ntable = "orders"\nv_order = true\n', encoding="utf-8")
    assert jobs.Job.load(path).target.v_order is True


def test_v_order_must_be_true_or_false(tmp_path):
    path = tmp_path / "orders.toml"
    path.write_text('name = "orders"\nengine = "duckdb"\nsql = "SELECT 1 AS id"\n[target]\nworkspace_id = "ws1"\n'
                    'lakehouse = "LH"\ntable = "orders"\nv_order = "yes"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="target.v_order is true or false"):
        jobs.Job.load(path)


def test_a_failed_write_queues_no_v_order(lake, tmp_path, monkeypatch):  # noqa: F811
    def broken(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(publish, "write_deltalake", broken)
    fabric = Fabric()
    with pytest.raises(RuntimeError, match="disk full"):
        publish.run_job(_job(tmp_path, True), fabric, lambda: "tok", tmp_path)
    assert fabric.maintenance == []


def test_a_write_sent_again_with_a_fresh_token_queues_v_order_once(lake, tmp_path, monkeypatch):  # noqa: F811
    real, tries = publish.publish, []

    def expires_once(*a, **k):
        tries.append(1)
        if len(tries) == 1:
            assert fabric.maintenance == []  # nothing queued for the write that committed nothing
            raise publish.WriteTokenExpired("Writing dbo.orders: refused. Nothing was committed.")
        return real(*a, **k)
    monkeypatch.setattr(publish, "publish", expires_once)
    fabric = Fabric()
    fabric.version = lambda: DeltaTable(str(lake / "dbo" / "orders")).version()
    record = publish.run_job(_job(tmp_path, True), fabric, lambda: "tok", tmp_path)
    assert len(tries) == 2 and len(fabric.maintenance) == 1
    assert fabric.version_when_queued == record.delta_version == 0 and record.v_order_job == "job-42"


def test_v_order_names_the_target_table_and_schema_as_written(lake, tmp_path):  # noqa: F811
    fabric = Fabric()
    record = publish.run_job(_job(tmp_path, True, table="Sales Orders", schema="sales", mode="append"), fabric,
                             lambda: "tok", tmp_path)
    assert [(m["table"], m["schema"]) for m in fabric.maintenance] == [("Sales Orders", "sales")]
    assert record.v_order_job == "job-42"
    assert DeltaTable(str(lake / "sales" / "Sales Orders")).version() == record.delta_version


def test_a_table_in_a_lakehouse_without_schemas_is_named_without_one(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "publish", lambda *a, **k: type("T", (), {"version": lambda self: 4})())
    monkeypatch.setattr(publish, "resolve_target", lambda j, f: TableRef("ws1", "lh1", "dbo", "orders", flat=True))
    fabric = Fabric(fail=RuntimeError("Capacity is paused"))
    fabric.version = lambda: 4
    record = publish.run_job(_job(tmp_path, True), fabric, lambda: "tok", tmp_path)
    assert fabric.maintenance[0]["schema"] is None
    assert record.v_order_error.startswith("Wrote orders (Delta version 4), but Fabric didn't start V-Order")


def test_a_run_with_no_fabric_connection_says_v_order_didnt_run(lake, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(publish, "resolve_target", lambda j, f: TableRef("ws1", "lh1", "dbo", "orders"))
    record = publish.run_job(_job(tmp_path, True), None, lambda: "tok", tmp_path)
    assert record.delta_version == 0 and record.v_order_job is None
    assert record.v_order_error == ("Wrote dbo.orders (Delta version 0), but it wasn't V-Ordered: only Fabric V-Orders "
                                    "a table, and this run had no Fabric connection. To V-Order it, run table "
                                    "maintenance on it in Fabric.")


def test_a_failed_v_order_is_kept_in_the_history_and_older_runs_still_read(lake, tmp_path):  # noqa: F811
    from fabric_desktop.service import Service
    service = Service(client_id="x", root=tmp_path)
    service.history_file.parent.mkdir(parents=True, exist_ok=True)
    old = {"job": "orders", "table_uri": "x", "mode": "overwrite", "rows": 1, "delta_version": 0, "seconds": 0.1,
           "finished_at": "2026-01-01T00:00:00Z"}  # written before v_order existed
    service.history_file.write_text(json.dumps(old) + "\n", encoding="utf-8")
    publish.run_job(_job(tmp_path, True), Fabric(fail=RuntimeError("Capacity is paused")), lambda: "tok", tmp_path,
                    history_file=service.history_file)
    newest, older = service.history()
    assert newest["v_order_job"] is None and "Capacity is paused" in newest["v_order_error"]
    assert publish.RunRecord(**newest) and publish.RunRecord(**older).v_order_error is None


def test_the_fabric_client_asks_for_no_vacuum_when_v_order_runs_alone(monkeypatch):
    sent = []
    monkeypatch.setattr(fabric_api.FabricClient, "run_item_job", lambda self, *a, **k: sent.append((a, k)) or "job-1")
    client = fabric_api.FabricClient(lambda: "tok")
    assert client.run_table_maintenance("ws1", "lh1", "Sales Orders", "sales", v_order=True,
                                        vacuum_retention=None) == "job-1"
    (ws, lh, kind, body), how = sent[0]
    assert (ws, lh, kind, how) == ("ws1", "lh1", "TableMaintenance", {"item_path": "lakehouses"})
    assert body == {"tableName": "Sales Orders", "schemaName": "sales", "optimizeSettings": {"vOrder": "true"}}
    assert "vacuumSettings" not in body
