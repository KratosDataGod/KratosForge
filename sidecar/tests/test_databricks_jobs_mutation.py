"""Mutation-killing tests for engines/databricks_jobs.py (batch 81): the read limits, how states with
no words read, run and task times, which compute a run used, pipeline update rows, the days bound
and a failed sign-in."""

from __future__ import annotations

import pytest
from test_databricks_discovery import HOST, Resp
from test_databricks_jobs import MS, NOW, FakeWorkspace, runs

from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_jobs as dj
from fabric_desktop.engines import databricks_sql as dbsql


class Ws(FakeWorkspace):
    """FakeWorkspace with the runs, pipelines and updates it answers set per test."""

    def __init__(self, runs_=None, pipelines=None, updates=None):
        super().__init__()
        self.runs_, self.pipelines, self.updates = runs_ or [], pipelines or [], updates or {}

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        path = url[len(HOST):]
        self.calls.append((method, path, dict(params or {})))
        if path == "/api/2.1/jobs/runs/list":
            return Resp(body={"runs": self.runs_})
        if path == "/api/2.0/pipelines":
            return Resp(body={"statuses": self.pipelines})
        if path.endswith("/updates"):
            return Resp(body={"updates": self.updates.get(path.split("/")[-2], [])})
        return Resp(404, {"message": "no such path"})


def _run(i, **kw):
    return {"run_id": i, "job_id": 1, "run_name": f"r{i}", "start_time": MS - 1000, "state": {"result_state": "SUCCESS"},
            "tasks": [{"task_key": "t", "job_cluster_key": "c"}]} | kw


# ---------------------------------------------------------------- limits


def test_at_most_100_job_runs_are_read():
    assert dbsql.CATALOG_LIMIT > 101
    rows = runs(Ws(runs_=[_run(i) for i in range(101)])).list()["rows"]
    assert len(rows) == 100


def test_at_most_50_pipelines_have_their_updates_read():
    pls = [{"pipeline_id": f"p{i}", "name": f"p{i}"} for i in range(51)]
    ws = Ws(pipelines=pls)
    runs(ws).list()
    reads = [c for c in ws.calls if c[1].endswith("/updates")]
    assert len(reads) == 50
    assert {c[2]["max_results"] for c in reads} == {"5"}


def test_a_task_error_is_cut_to_1000_characters():
    row = dj.task_row({"task_key": "t", "state": {"result_state": "FAILED", "state_message": "e" * 1200}})
    assert row["error"] == "e" * 999 + "…"


def test_a_run_page_url_and_a_notebook_path_are_cut_to_500_characters():
    url = f"{HOST}/" + "u" * 600
    assert dj.run_row(_run(1, run_page_url=url))["url"] == url[:499] + "…"
    row = dj.task_row({"task_key": "t", "notebook_task": {"notebook_path": "/" + "n" * 600}})
    assert row["notebook"] == ("/" + "n" * 600)[:499] + "…"


# ---------------------------------------------------------------- states and times


def test_a_life_cycle_state_with_no_words_is_title_cased_or_unknown():
    assert dj._state({"life_cycle_state": "TERMINATED"}) == ("other", "Terminated")
    assert dj._state({}) == ("other", "Unknown")


@pytest.mark.parametrize("value,ms", [(None, 0), (0, 0), (-5, 0), (7, 7)])
def test_a_missing_or_negative_time_is_zero(value, ms):
    assert dj._ms(value) == ms


@pytest.mark.parametrize("start,end,took", [(0, 5000, 0), (5000, 0, 0), (9000, 5000, 0), (5000, 9000, 4)])
def test_a_run_without_a_start_or_end_took_nothing(start, end, took):
    assert dj._took({"start_time": start, "end_time": end}) == took


# ---------------------------------------------------------------- compute


def test_compute_is_serverless_with_an_environment_or_no_task_list():
    assert dj._compute({}) == "Serverless"
    assert dj._compute({"environment_key": "e", "tasks": []}) == "Serverless"
    assert dj._compute({"tasks": []}) == ""


def test_a_task_with_no_compute_says_none():
    assert dj.task_row({"task_key": "t", "tasks": []})["compute"] == ""


def test_a_run_whose_tasks_name_no_compute_uses_the_runs_own():
    assert dj.run_row(_run(1, tasks=[]))["compute"] == ""
    assert dj.run_row(_run(1, tasks=[], environment_key="e"))["compute"] == "Serverless"
    row = dj.run_row(_run(1, tasks=[{"task_key": "a", "tasks": []}, {"task_key": "b", "job_cluster_key": "c"}]))
    assert row["compute"] == "Job cluster"


def test_a_failed_run_without_a_failed_task_says_only_failed():
    row = dj.run_row(_run(1, state={"result_state": "FAILED"}, tasks=[{"task_key": "t", "state": {"result_state": "SUCCESS"}}]))
    assert row["result"] == "Failed"
    ok = dj.run_row(_run(1, state={"result_state": "SKIPPED"}, tasks=[{"task_key": "t", "state": {"result_state": "FAILED"}}]))
    assert ok["result"] == "Skipped"


# ---------------------------------------------------------------- pipeline updates


def test_an_update_state_with_no_words_is_title_cased_or_unknown():
    p = {"pipeline_id": "p"}
    assert dj.update_row(p, {"state": "WEIRD_STATE"})["result"] == "Weird_State"
    assert dj.update_row(p, {})["result"] == "Unknown"


def test_an_update_row_took_nothing_and_says_its_trigger():
    p = {"pipeline_id": "p"}
    row = dj.update_row(p, {"state": "COMPLETED", "cause": "SCHEDULED"})
    assert row["took_s"] == 0 and row["trigger"] == "Scheduled"
    assert dj.update_row(p, {"full_refresh": True, "cause": "SCHEDULED"})["trigger"] == "Full refresh"
    assert dj.update_row(p, {})["trigger"] == ""


def test_an_update_made_exactly_at_the_window_start_is_kept():
    since = MS - 86_400_000
    ws = Ws(pipelines=[{"pipeline_id": "p", "name": "p"}],
            updates={"p": [{"update_id": "edge", "state": "COMPLETED", "creation_time": since},
                           {"update_id": "old", "state": "COMPLETED", "creation_time": since - 1}]})
    rows = runs(ws).list(days=1)["rows"]
    assert [r["id"] for r in rows] == ["edge"]


# ---------------------------------------------------------------- days and sign-in


def test_days_below_one_reads_one_day():
    ws = Ws()
    out = runs(ws).list(days=0)
    assert out["days"] == 1 and int(ws.calls[0][2]["start_time_from"]) == int((NOW - 86400) * 1000)


def test_a_failed_sign_in_marks_both_lists_not_ok():
    def no_token():
        raise dba.DatabricksAuthError("Sign in again.")
    out = dj.Runs(dbsql.Client(dba.TokenProvider(HOST, no_token, "test"), Ws())).list()
    assert out["jobs"]["ok"] is False and out["pipelines"]["ok"] is False
