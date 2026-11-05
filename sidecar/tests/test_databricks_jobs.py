"""Databricks job runs and Lakeflow pipeline updates for Monitor, against a fake of the documented
REST APIs: every request is a GET, a list that can't be read never hides the other, and nothing
from the workspace carries a token or a newline into the screen."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_jobs as dj
from fabric_desktop.engines import databricks_sql as dbsql

from test_databricks_discovery import HOST, TOKEN, Resp

NOW = 1_800_000_000.0
MS = int(NOW * 1000)
RUNS = [
    {"run_id": 11, "job_id": 1, "run_name": "nightly_sales", "start_time": MS - 3_600_000, "run_duration": 840_000,
     "state": {"life_cycle_state": "TERMINATED", "result_state": "FAILED"}, "trigger": "PERIODIC",
     "creator_user_name": "dev@contoso.com", "run_page_url": f"{HOST}/#job/1/run/11",
     "tasks": [{"task_key": "bronze", "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"}, "job_cluster_key": "c"},
               {"task_key": "gold_kpis", "state": {"life_cycle_state": "TERMINATED", "result_state": "FAILED"}, "job_cluster_key": "c"}]},
    {"run_id": 12, "job_id": 2, "run_name": "other\nperson", "start_time": MS - 600_000,
     "state": {"life_cycle_state": "RUNNING"}, "creator_user_name": "someone@contoso.com", "tasks": [{"task_key": "t", "existing_cluster_id": "c1"}]},
]
RUN = RUNS[0] | {"tasks": [
    {"task_key": "bronze", "run_id": 101, "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
     "start_time": MS - 3_600_000, "end_time": MS - 3_420_000, "notebook_task": {"notebook_path": "/Repos/sales/bronze"}},
    {"task_key": "gold_kpis", "run_id": 102, "state": {"life_cycle_state": "TERMINATED", "result_state": "FAILED",
     "state_message": f"AnalysisException: column revenue_usd cannot be resolved. token {TOKEN}"},
     "notebook_task": {"notebook_path": "/Repos/sales/gold_kpis"}}],
    "job_parameters": [{"name": "run_date", "value": "2026-11-02"}]}
PIPELINES = [{"pipeline_id": "pl-1", "name": "orders_cdc", "creator_user_name": "dev@contoso.com", "serverless": True}]
UPDATES = [{"update_id": "u2", "state": "COMPLETED", "creation_time": MS - 1_000_000, "cause": "SCHEDULED"},
           {"update_id": "u1", "state": "FAILED", "creation_time": MS - 5 * 86_400_000}]


class FakeWorkspace:
    def __init__(self, refuse=None):
        self.calls = []
        self.refuse = refuse or {}

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        path = url[len(HOST):]
        self.calls.append((method, path, dict(params or {})))
        if path in self.refuse:
            return Resp(self.refuse[path], {"message": f"denied for {TOKEN}"})
        if path == "/api/2.1/jobs/runs/list":
            return Resp(body={"runs": RUNS})
        if path == "/api/2.1/jobs/runs/get":
            return Resp(body=RUN)
        if path == "/api/2.0/pipelines":
            return Resp(body={"statuses": PIPELINES})
        if path == "/api/2.0/pipelines/pl-1/updates":
            return Resp(body={"updates": UPDATES})
        return Resp(404, {"message": "no such path"})


def runs(ws):
    return dj.Runs(dbsql.Client(dba.TokenProvider(HOST, lambda: (TOKEN, None), "test"), ws), clock=lambda: NOW)


def test_runs_reads_with_gets_only_newest_first():
    ws = FakeWorkspace()
    out = runs(ws).list(days=1)
    assert {m for m, _, _ in ws.calls} == {"GET"}
    assert out["signed_in"] and out["jobs"] == {"ok": True} and out["pipelines"] == {"ok": True}
    assert [(r["kind"], r["name"]) for r in out["rows"]] == [("job", "other person"), ("pipeline", "orders_cdc"), ("job", "nightly_sales")]
    failed = out["rows"][2]
    assert failed["result"] == "Failed at task gold_kpis" and failed["outcome"] == "failed"
    assert failed["took_s"] == 840 and failed["compute"] == "Job cluster" and failed["trigger"] == "Periodic"
    assert out["rows"][0]["outcome"] == "running" and out["rows"][0]["compute"] == "All-purpose cluster"
    assert out["rows"][1]["result"] == "Succeeded" and out["rows"][1]["compute"] == "Serverless"
    since = ws.calls[0][2]["start_time_from"]
    assert int(since) == MS - 86_400_000


def test_older_pipeline_updates_are_left_out_and_days_is_bounded():
    out = runs(FakeWorkspace()).list(days=7)
    assert [r["id"] for r in out["rows"] if r["kind"] == "pipeline"] == ["u2", "u1"]
    assert runs(FakeWorkspace()).list(days=999)["days"] == 30


def test_mine_keeps_only_that_persons_runs():
    out = runs(FakeWorkspace()).list(mine="DEV@contoso.com")
    assert {r["name"] for r in out["rows"]} == {"nightly_sales", "orders_cdc"}


def test_a_refused_list_says_why_and_the_other_still_comes_back():
    out = runs(FakeWorkspace(refuse={"/api/2.0/pipelines": 403})).list()
    assert not out["pipelines"]["ok"] and out["jobs"]["ok"]
    assert {r["kind"] for r in out["rows"]} == {"job"}
    assert TOKEN not in json.dumps(out)


def test_a_failed_sign_in_stops_both_lists_once():
    def no_token():
        raise dba.DatabricksAuthError("Sign in again with databricks auth login.")
    out = dj.Runs(dbsql.Client(dba.TokenProvider(HOST, no_token, "test"), FakeWorkspace())).list()
    assert not out["signed_in"] and "databricks auth login" in out["error"] and out["rows"] == []


def test_run_detail_has_tasks_errors_notebooks_and_parameters_without_the_token():
    out = runs(FakeWorkspace()).run("11")
    assert [t["key"] for t in out["tasks"]] == ["bronze", "gold_kpis"]
    gold = out["tasks"][1]
    assert gold["outcome"] == "failed" and gold["notebook"] == "/Repos/sales/gold_kpis"
    assert gold["error"].startswith("AnalysisException: column revenue_usd cannot be resolved.")
    assert TOKEN not in json.dumps(out)
    assert out["tasks"][0]["took_s"] == 180 and out["parameters"] == {"run_date": "2026-11-02"}


@pytest.mark.parametrize("bad", ["", "11; drop", "-1", "1" * 21])
def test_run_refuses_an_id_that_isnt_a_number(bad):
    with pytest.raises(ValueError):
        runs(FakeWorkspace()).run(bad)


def test_an_odd_reply_names_the_list_and_keeps_the_other():
    class Odd(FakeWorkspace):
        def request(self, method, url, **kw):
            if url.endswith("/jobs/runs/list"):
                self.calls.append((method, url, {}))
                return Resp(body={"runs": ["not a run"]})
            return super().request(method, url, **kw)
    out = runs(Odd()).list()
    assert not out["jobs"]["ok"] and "shape" in out["jobs"]["error"] and out["pipelines"]["ok"]


def test_a_run_page_on_another_host_isnt_offered():
    evil = RUNS[0] | {"run_page_url": "https://evil.example/phish"}

    class Elsewhere(FakeWorkspace):
        def request(self, method, url, **kw):
            if url.endswith("/jobs/runs/list"):
                return Resp(body={"runs": [evil]})
            return super().request(method, url, **kw)
    out = runs(Elsewhere()).list(days=1)
    assert [r["url"] for r in out["rows"] if r["kind"] == "job"] == [""]
    assert runs(FakeWorkspace()).run("11")["url"] == f"{HOST}/#job/1/run/11"
