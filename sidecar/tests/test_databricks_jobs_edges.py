"""Databricks job runs: a run Databricks itself failed, times that aren't milliseconds, a SQL
warehouse task, and the pipeline list kept to one person's or refused for an odd id."""

import pytest
from test_databricks_discovery import Resp
from test_databricks_jobs import PIPELINES, FakeWorkspace, runs

from fabric_desktop.engines import databricks_jobs as dj


def test_a_run_databricks_failed_itself_reads_as_failed():
    assert dj._state({"life_cycle_state": "INTERNAL_ERROR"}) == ("failed", "Failed (Databricks error)")


@pytest.mark.parametrize("value", ["soon", [1], float("inf")])
def test_a_time_that_isnt_milliseconds_is_an_odd_reply(value):
    with pytest.raises(dj.OddReply, match="isn't milliseconds"):
        dj._ms(value)


def test_a_sql_task_runs_on_a_sql_warehouse():
    assert dj._compute({"sql_task": {"warehouse_id": "wh1"}}) == "SQL warehouse"


def test_mine_leaves_out_other_peoples_pipelines_without_reading_their_updates():
    ws = FakeWorkspace()
    out = runs(ws).list(mine="someone@contoso.com")
    assert out["pipelines"]["ok"] and not [r for r in out["rows"] if r.get("kind") == "pipeline"]
    assert not [c for c in ws.calls if c[1].endswith("/updates")]


def test_a_pipeline_id_databricks_wouldnt_give_is_refused_before_it_goes_in_a_path():
    class Odd(FakeWorkspace):
        def request(self, method, url, **kw):
            if url.endswith("/api/2.0/pipelines"):
                self.calls.append((method, url, {}))
                return Resp(body={"statuses": [PIPELINES[0] | {"pipeline_id": "../jobs/runs/delete"}]})
            return super().request(method, url, **kw)
    ws = Odd()
    out = runs(ws).list()
    assert not out["pipelines"]["ok"] and "a pipeline id isn't one Databricks gives" in out["pipelines"]["error"]
    assert out["jobs"]["ok"] and not [c for c in ws.calls if "delete" in c[1]]
