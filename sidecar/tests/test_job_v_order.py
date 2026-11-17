"""A job whose target says v_order = true queues Fabric's table maintenance job with V-Order after its
write commits (publish.run_job, #2361). Fakes only: no Fabric call is made."""

from __future__ import annotations

import json

import pytest
from deltalake import DeltaTable
from test_spark_write_types import lake  # noqa: F401 - lake is a fixture

from fabric_desktop import jobs, publish


class Fabric:
    def __init__(self, fail=None):
        self.maintenance, self.fail = [], fail
        self.version_when_queued = None

    def find_lakehouse(self, workspace_id, name):
        return {"id": "lh1"}

    def run_table_maintenance(self, workspace_id, lakehouse_id, table, schema=None, **kw):
        self.maintenance.append({"workspace_id": workspace_id, "lakehouse_id": lakehouse_id, "table": table,
                                 "schema": schema, **kw})
        self.version_when_queued = self.version()
        if self.fail:
            raise self.fail
        return "job-42"


def _job(tmp_path, v_order):
    target = jobs.Target("ws1", "LH", "orders", "dbo", "overwrite", v_order=v_order)
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
    assert record.v_order_error.startswith("The table was written, but V-Order wasn't queued:")
    assert "Capacity is paused" in record.v_order_error
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
