"""Databricks DBUs per run of linked jobs (Compare platforms, Preview): the per-run query runs only in
the confirmed billing session, takes whole-number job ids only, and is kept apart from the totals.
Tested against fakes only; no live workspace."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest

from fabric_desktop.engines import databricks_billing as bill
from fabric_desktop.engines.contract import CellResult

from test_databricks_billing import TABLE, Svc, WarehouseWs, cli  # noqa: F401  (cli is an autouse fixture)

RUNS = pa.table({"job_id": ["8841", "8841", "8841", "8841"], "job_run_id": ["r1", "r1", "r2", None],
                 "sku_name": ["JOBS_COMPUTE", "PREMIUM_SQL_PRO", "JOBS_COMPUTE", "JOBS_COMPUTE"],
                 "started": [dt.datetime(2026, 11, 23, 9, 0), dt.datetime(2026, 11, 23, 9, 0), dt.datetime(2026, 11, 22, 9, 0), None],
                 "dbu": [1.2, 0.22, 1.4, 3.0]})


class TwoResults:
    """A session that answers the totals query, then the per-run query."""

    def __init__(self, log, results):
        self.log, self.results = log, list(results)

    def execute(self, request):
        self.log.append(("execute", request.code, request.result_rows))
        return self.results.pop(0)

    def close(self):
        self.log.append(("close",))


class Adapter:
    def __init__(self, *results):
        self.log, self.results = [], results

    def open_session(self, config, authorization=None):
        self.log.append(("open", config.options["warehouse_id"], authorization))
        return TwoResults(self.log, self.results)


OK = CellResult("ok", [], 1.0, 4, "databricks-sql", TABLE)


def test_the_runs_query_is_fixed_text_with_job_ids_only():
    sql = bill.runs_query(30, ["8841", 12, "8841"])
    assert "usage_metadata.job_run_id" in sql and "IN ('8841', '12')" in sql and "date_sub(current_date(), 30)" in sql
    for bad in (["1; DROP"], ["abc"], [True], ["1" * 31], [str(i) for i in range(21)]):
        with pytest.raises(ValueError):
            bill.runs_query(30, bad)
    with pytest.raises(ValueError, match="Link a Databricks job"):
        bill.runs_query(30, [])
    with pytest.raises(ValueError, match="days"):
        bill.runs_query(90, ["1"])


def test_run_rows_drop_a_row_with_no_run_id_and_keep_times_in_utc():
    rows = bill.run_rows_from(RUNS)
    assert [(r["job_run_id"], r["sku"], r["dbu"]) for r in rows] == [
        ("r1", "JOBS_COMPUTE", 1.2), ("r1", "PREMIUM_SQL_PRO", 0.22), ("r2", "JOBS_COMPUTE", 1.4)]
    assert rows[0]["started"] == "2026-11-23T09:00:00Z" and rows[2]["started"] == "2026-11-22T09:00:00Z"


def test_without_confirm_nothing_runs_even_with_job_ids(tmp_path):
    svc = Svc(tmp_path, Adapter())
    svc.ws = WarehouseWs()
    assert svc.databricks_billing("cli:dev", days=30, job_ids=["8841"])["needs_confirm"]
    assert svc.adapter.log == []


def test_bad_job_ids_are_refused_before_anything(tmp_path):
    svc = Svc(tmp_path, Adapter())
    with pytest.raises(ValueError, match="job id"):
        svc.databricks_billing("cli:dev", confirmed=True, job_ids=["1 OR 1=1"])
    assert svc.adapter.log == []


def test_confirmed_with_jobs_runs_both_queries_in_one_session_and_keeps_runs_apart(tmp_path):
    svc = Svc(tmp_path, Adapter(OK, CellResult("ok", [], 1.0, 4, "databricks-sql", RUNS)))
    out = svc.databricks_billing("cli:dev", days=30, confirmed=True, job_ids=["8841"])
    assert out["ok"] and len(out["runs"]) == 3 and out["run_jobs"] == ["8841"]
    assert [e[0] for e in svc.adapter.log] == ["open", "execute", "execute", "close"]
    assert svc.adapter.log[0][2].reason == "confirmed" and svc.adapter.log[2][1] == bill.runs_query(30, ["8841"])
    host = "https://" + svc.databricks_billing_last("cli:dev")["host"].removeprefix("https://")
    assert "runs" not in svc.databricks_billing_last("cli:dev")
    assert len(bill.last_runs(tmp_path / "billing", host)["runs"]) == 3
    # A later read of the totals alone keeps the per-run read.
    svc.adapter = Adapter(OK)
    assert svc.databricks_billing("cli:dev", days=7, confirmed=True)["ok"]
    assert len(bill.last_runs(tmp_path / "billing", host)["runs"]) == 3


def test_a_failed_runs_query_keeps_the_totals_and_says_the_runs_are_not_known(tmp_path):
    failed = CellResult("error", [{"type": "error", "evalue": "TABLE_OR_VIEW_NOT_FOUND"}], 1.0, None, "databricks-sql")
    svc = Svc(tmp_path, Adapter(OK, failed))
    out = svc.databricks_billing("cli:dev", days=30, confirmed=True, job_ids=["8841"])
    assert out["ok"] and out["runs"] is None and "TABLE_OR_VIEW_NOT_FOUND" in out["runs_error"]
    assert out["total_dbu"] == 84.6


def test_a_failed_totals_query_doesnt_run_the_runs_query(tmp_path):
    failed = CellResult("error", [{"type": "error", "evalue": "PERMISSION_DENIED"}], 1.0, None, "databricks-sql")
    svc = Svc(tmp_path, Adapter(failed))
    assert not svc.databricks_billing("cli:dev", confirmed=True, job_ids=["8841"])["ok"]
    assert [e[0] for e in svc.adapter.log] == ["open", "execute", "close"]
