"""Mutation batch 41: engines/databricks_billing.py. Pins the row limit, the day choices, which rows
count as DBUs (zero kept, NaN and negative dropped), the rounding, the per-workspace file name, and
how long a quoted error can be."""

from __future__ import annotations

import json

import pyarrow as pa
import pytest

from fabric_desktop.engines import databricks_billing as bill
from fabric_desktop.engines.contract import CellResult

HOST = "https://adb-123.4.azuredatabricks.net"


class Session:
    def __init__(self, log, result):
        self.log, self.result = log, result

    def execute(self, request):
        self.log.append(request)
        return self.result

    def close(self):
        self.log.append("close")


class Adapter:
    def __init__(self, result):
        self.log, self.result = [], result

    def open_session(self, config, authorization=None):
        return Session(self.log, self.result)


def _table(**cols):
    n = len(next(iter(cols.values())))
    base = {k: [None] * n for k in ("job_id", "pipeline_id", "warehouse_id", "cluster_id", "sku_name", "dbu")}
    return pa.table(base | cols)


def test_the_query_reads_at_most_500_rows():
    assert bill.ROW_LIMIT == 500
    assert bill.query(7).rstrip().endswith("LIMIT 500")
    adapter = Adapter(CellResult("ok", [], 1.0, 0, "databricks-sql", _table(dbu=[1.0])))
    bill.read(adapter, {}, "wh", 7, object())
    request = adapter.log[0]
    assert request.result_rows == 500 and request.preview_rows == 0


@pytest.mark.parametrize("days", [1, 7, 30])
def test_each_offered_day_count_is_taken(days):
    assert f"date_sub(current_date(), {days})" in bill.query(days)


@pytest.mark.parametrize("days", [0, 2, 31, 90])
def test_other_day_counts_are_refused(days):
    with pytest.raises(ValueError, match="1, 7, 30 days"):
        bill.query(days)


def test_a_row_with_no_job_pipeline_warehouse_or_cluster_is_other():
    rows = bill.rows_from(_table(sku_name=["X"], dbu=[2.0]))
    assert rows == [{"kind": "other", "id": "", "sku": "X", "dbu": 2.0}]


def test_zero_and_fractional_dbus_are_kept_nan_and_negative_dropped():
    rows = bill.rows_from(_table(job_id=["a", "b", "c", "d"], dbu=[0.0, 0.5, float("nan"), -0.001]))
    assert [(r["id"], r["dbu"]) for r in rows] == [("a", 0.0), ("b", 0.5)]


def test_a_negative_dbu_alone_is_dropped_and_a_nan_alone_is_dropped():
    assert bill.rows_from(_table(job_id=["a"], dbu=[-1.0])) == []
    assert bill.rows_from(_table(job_id=["a"], dbu=[float("nan")])) == []


def test_dbus_are_rounded_to_three_places_and_seconds_to_one():
    adapter = Adapter(CellResult("ok", [], 1.26, 1, "databricks-sql", _table(job_id=["a"], dbu=[1.23456])))
    out = bill.read(adapter, {}, "wh", 1, object())
    assert out["rows"][0]["dbu"] == 1.235 and out["seconds"] == 1.3


def test_a_long_error_is_cut_to_600_characters():
    long = "x" * 700
    adapter = Adapter(CellResult("error", [{"type": "error", "ename": "E", "evalue": long}], 0.0, None, "d"))
    out = bill.read(adapter, {}, "wh", 1, object())
    assert out["ok"] is False and len(out["error"]) == 600 and out["error"].endswith("…")
    exact = Adapter(CellResult("error", [{"type": "error", "evalue": "y" * 600}], 0.0, None, "d"))
    assert bill.read(exact, {}, "wh", 1, object())["error"] == "y" * 600


def test_the_file_is_named_from_the_host_with_other_characters_as_underscores(tmp_path):
    assert bill._file(tmp_path, HOST) == tmp_path / "adb_123_4_azuredatabricks_net.json"
    assert bill._file(tmp_path, "") == tmp_path / "workspace.json"
    assert bill._file(tmp_path, "https://") == tmp_path / "workspace.json"


def test_a_long_host_name_is_cut_to_120_characters(tmp_path):
    host = "https://" + "a" * 130
    assert bill._file(tmp_path, host).name == "a" * 120 + ".json"


def test_save_makes_the_folder_and_can_save_again(tmp_path):
    root = tmp_path / "deep" / "billing"
    result = {"ok": True, "schema": bill.SCHEMA, "rows": []}
    bill.save(root, HOST, result)
    bill.save(root, HOST, result | {"rows": [1]})
    saved = json.loads(bill._file(root, HOST).read_text(encoding="utf-8"))
    assert saved["rows"] == [1] and saved["host"] == HOST
    assert bill.last(root, HOST)["rows"] == [1]
