"""The local pipeline runner (orchestration/runner.py), paths that mutation testing (batch 12,
docs/quality/mutation-testing.md) showed no test checked: defaults, the record's fields and counts,
retries and timeouts, mocks that fail with an output, a notebook step's own failure, a cancel that
came from outside, and the limits on nesting and parallel work. No network and no Fabric."""

from __future__ import annotations

import socket
import threading
import uuid

import pytest

from fabric_desktop.orchestration import loads
from fabric_desktop.orchestration import runner as R
from fabric_desktop.orchestration.adapters import ActivityFailed
from fabric_desktop.orchestration.evaluate import weight
from fabric_desktop.orchestration.runner import PipelineRun

from test_pipeline_runs import act, pipeline, run, wait


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("local pipeline runs must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _records(r, name):
    return [a for a in r.to_json()["activities"] if a["name"] == name]


# ---- defaults

def test_an_undeclared_boolean_variable_starts_false():
    r = run(pipeline(wait("W"), variables={"flag": {"type": "Boolean"}, "on": {"type": "Bool"}}))
    assert r.variables == {"flag": False, "on": False}


def test_a_mock_without_seconds_doesnt_wait(monkeypatch):
    waited = []
    monkeypatch.setattr(R._Context, "wait", lambda self, seconds: waited.append(seconds))
    r = run(pipeline(act("C", "Copy", source={}, sink={})), mocks={"C": {"output": {"rows": 1}}}, wait_scale=1)
    assert r.state == "succeeded" and waited == []


def test_parallel_work_is_capped_at_32():
    imp = loads(pipeline(wait("W")))
    assert PipelineRun(imp, max_parallel=100).max_parallel == 32
    assert PipelineRun(imp, max_parallel=0).max_parallel == 1


# ---- the record

def test_activity_ids_come_from_the_run_id_and_the_activity_path():
    r = run(pipeline(wait("W")), run_id="9b2c6f0e-8a7d-4c1e-9f3a-2b4d6e8f0a1c")
    [rec] = _records(r, "W")
    assert rec["id"] == str(uuid.uuid5(uuid.UUID(r.id), f"|{rec['label']}|{rec['path']}"))


def test_a_skipped_activity_never_started_and_the_counts_add_up():
    r = run(pipeline(wait("A"), wait("B"), act("F", "Fail", message="x", errorCode="E"),
                     wait("After", depends=("F",))))
    out = r.to_json()
    [after] = _records(r, "After")
    assert after["state"] == "skipped" and after["started"] == "" and after["ended"]
    assert out["counts"]["succeeded"] == 2 and out["counts"]["failed"] == 1 and out["counts"]["skipped"] == 1


def test_seconds_are_kept_to_the_millisecond():
    r = run(pipeline(*(wait(f"W{i}") for i in range(6))))
    seconds = [a["seconds"] for a in r.to_json()["activities"]]
    assert len(seconds) == 6 and all(round(s, 3) == s for s in seconds)


class _Data:
    def check(self, a):
        return []

    def lookup(self, a, props):
        return {"firstRow": {"n": 1}}

    metadata = lookup

    def script(self, a, props, cancelled, deadline):
        return {}


def test_the_note_about_lakehouse_reads_is_there_only_when_a_read_ran():
    lookup = act("L", "Lookup", datasetSettings={"type": "LakehouseTable"}, source={})
    read = PipelineRun(loads(pipeline(lookup)), data=_Data(), wait_scale=0).run()
    assert any("Lookup, Get Metadata and Script steps ran here" in n for n in read.to_json()["notes"])
    mocked = run(pipeline(lookup), mocks={"L": {"output": {}}})
    assert not any("Lookup, Get Metadata and Script steps ran here" in n for n in mocked.to_json()["notes"])


# ---- retries, timeouts and failures

def test_a_container_isnt_retried_as_a_whole():
    inner = [act("C", "Copy", source={}, sink={})]
    text = pipeline(act("If", "IfCondition", expression={"value": "@true()", "type": "Expression"},
                        ifTrueActivities=inner) | {"policy": {"retry": 2, "retryIntervalInSeconds": 0}})
    r = run(text, mocks={"C": {"status": "Failed"}})
    [rec] = _records(r, "If")
    assert rec["state"] == "failed" and rec["attempts"] == 1


def test_a_policy_timeout_counts_from_when_the_activity_starts():
    text = pipeline(act("C", "Copy", source={}, sink={}) | {"policy": {"timeout": "0.00:10:00"}})
    r = run(text, mocks={"C": {"seconds": 0.05}}, wait_scale=1)
    assert r.state == "succeeded"


def test_a_failed_mock_keeps_its_output_and_one_without_has_none():
    r = run(pipeline(act("C", "Copy", source={}, sink={}), act("D", "Copy", source={}, sink={})),
            mocks={"C": {"status": "Failed", "output": {"rows": 3}}, "D": {"status": "Failed"}})
    [c], [d] = _records(r, "C"), _records(r, "D")
    assert c["output"] == {"rows": 3}
    assert "output" not in d


def test_a_notebook_steps_own_failure_is_its_error():
    class Notebooks:
        def check(self, a):
            return []

        def run(self, a, parameters, cancelled, deadline):
            raise ActivityFailed("cell 2 failed", "NotebookCellFailed")

        def notes(self):
            return []

        def finish(self):
            pass

    r = PipelineRun(loads(pipeline(act("N", "TridentNotebook", notebookId="x"))), wait_scale=0,
                    notebooks=Notebooks()).run()
    [rec] = _records(r, "N")
    assert rec["state"] == "failed" and rec["error"]["errorCode"] == "NotebookCellFailed"
    assert rec["error"]["message"] == "cell 2 failed"


# ---- cancelling and nesting

def test_a_run_cancelled_from_outside_says_cancelled_and_starts_nothing():
    stop = threading.Event()
    stop.set()
    r = PipelineRun(loads(pipeline(wait("W", 5))), wait_scale=1, cancel=stop).run()
    assert r.state == "cancelled" and r.stop_reason == "cancelled"
    # A mock that would end at once isn't started either.
    r = PipelineRun(loads(pipeline(act("C", "Copy", source={}, sink={}))), mocks={"C": {}}, cancel=stop).run()
    [rec] = _records(r, "C")
    assert (rec["state"], rec["executed_by"], rec["started"]) == ("cancelled", "not_started", "")


def test_a_cancelled_foreach_starts_no_more_iterations():
    inner = [act("Each", "Copy", source={}, sink={})]
    text = pipeline(act("Loop", "ForEach", items="@createArray(1, 2, 3, 4, 5, 6)", isSequential=True,
                        activities=inner))
    r = PipelineRun(loads(text), mocks={"Each": {"seconds": 0.3}}, wait_scale=1)
    threading.Timer(0.45, r.cancel).start()
    r.run()
    assert r.state == "cancelled"
    assert len(_records(r, "Each")) <= 3  # the one cut short, and at most one more already starting


def test_a_pipeline_that_runs_itself_is_refused_as_too_deep():
    text = pipeline(act("Again", "ExecutePipeline", pipeline={"referenceName": "self"}, waitOnCompletion=True))
    problems = PipelineRun(loads(text), children={"self": text}).check()
    assert any(p["code"] == "too_deep" for p in problems)


# ---- variables

def test_appending_keeps_a_running_size_of_the_array():
    acts = [act("A1", "AppendVariable", variableName="xs", value="ab"),
            act("A2", "AppendVariable", ["A1"], variableName="xs", value="cde")]
    r = run(pipeline(*acts, variables={"xs": {"type": "Array", "defaultValue": ["zzzz"]}}))
    assert r.variables["xs"] == ["zzzz", "ab", "cde"]
    start, a, b = weight(["zzzz"]), weight("ab"), weight("cde")
    assert r._weights["xs"] == (start[0] + a[0] + b[0], start[1] + a[1] + b[1])
