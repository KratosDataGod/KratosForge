"""Script activities: the edges the Script tests don't reach. A fake SQL runner stands in for the SQL
page, so no query runs and nothing reaches a lakehouse."""

from types import SimpleNamespace

import pytest

from fabric_desktop import pipeline_script as ps
from fabric_desktop.orchestration.adapters import ActivityFailed
from fabric_desktop.orchestration.definition import Activity


def activity(*scripts, project=True):
    return Activity(name="Load", type="Script", path="$.a[0]",
                    raw={"typeProperties": {"scripts": list(scripts)},
                         "linkedService": {"name": "Local" if project else "LH"}})


class Runs:
    def __init__(self, states, page_rows=()):
        self.states, self.page_rows, self.cancelled = list(states), list(page_rows), []

    def start(self, text, *args, **kwargs):
        if kwargs.get("dry_run"):
            return {}
        return {"id": "r1", **self.states.pop(0)}

    def status(self, run_id):
        return self.states.pop(0)

    def cancel(self, run_id):
        self.cancelled.append(run_id)

    def page(self, run_id, offset, limit):
        return {"rows": self.page_rows.pop(0) if self.page_rows else []}


def scripts(runs):
    data = SimpleNamespace(svc=SimpleNamespace(sql_runs=runs), lakehouse_reads=True, workspace_id="ws")
    return ps.LocalScripts(data, {"Load": "proj"})


def test_an_activity_with_no_scripts_or_an_empty_one_is_reported_before_the_run():
    s = scripts(Runs([]))
    assert [p["code"] for p in s.check(activity())] == ["script_empty"]
    problems = s.check(activity({"type": "Query", "text": "  "}))
    assert problems[0]["code"] == "script_empty" and "script 1 is empty" in problems[0]["message"]


def test_an_empty_script_fails_the_run():
    with pytest.raises(ActivityFailed, match="script 1 is empty"):
        scripts(Runs([])).run(activity(), {"scripts": [{"text": ""}]}, lambda: False, None)


@pytest.mark.parametrize("state, code", [("timed_out", "LocalScriptTimeout"), ("cancelled", "LocalCancelled")])
def test_a_script_stopped_by_its_limit_or_by_a_person_fails_with_its_code(state, code):
    with pytest.raises(ActivityFailed) as raised:
        scripts(Runs([{"state": state}])).run(activity(), {"scripts": [{"text": "SELECT 1"}]}, lambda: False, None)
    assert raised.value.error["errorCode"] == code


def test_the_activity_timeout_stops_a_running_script():
    runs = Runs([{"state": "running"}])
    with pytest.raises(ActivityFailed, match="policy timeout") as raised:
        scripts(runs).run(activity(), {"scripts": [{"text": "SELECT 1"}]}, lambda: False, deadline=0.0)
    assert raised.value.error["errorCode"] == "LocalActivityTimeout" and runs.cancelled == ["r1"]


def test_rows_stop_at_the_first_empty_page():
    runs = Runs([{"state": "completed", "columns": [{"name": "n"}], "rows": 3}], page_rows=[[[1]], []])
    out = scripts(runs).run(activity(), {"scripts": [{"text": "SELECT n"}]}, lambda: False, None)
    assert out["resultSets"] == [{"rowCount": 1, "rows": [{"n": 1}]}] and out["outputTruncated"] is True


def test_a_statement_with_no_columns_adds_no_result_set():
    runs = Runs([{"state": "completed", "columns": [], "rows": 0}])
    out = scripts(runs).run(activity(), {"scripts": [{"type": "Query", "text": "CREATE TABLE t (a INT)"}]},
                            lambda: False, None)
    assert out["resultSetCount"] == 0 and out["resultSets"] == []
