"""Mutation testing batch 179 (docs/quality/mutation-testing.md): the local pipeline runner
(orchestration/runner.py), every mutant. Each test pins a behavior a surviving mutant changed without
any other test noticing. No network and no Fabric."""

from __future__ import annotations

import socket

import pytest

from fabric_desktop.orchestration import loads
from fabric_desktop.orchestration import runner as R
from fabric_desktop.orchestration.runner import PipelineRun

from test_pipeline_runs import act, fail, pipeline, run, wait


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("local pipeline runs must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_child_pipelines_nest_at_most_8_deep():
    text = pipeline(act("Again", "ExecutePipeline", pipeline={"referenceName": "self"}, waitOnCompletion=True))
    problems = PipelineRun(loads(text), children={"self": text}).check()
    # The top-level run and 7 child levels below it are checked; the 8th is refused.
    assert [p["message"] for p in problems if p["code"] == "too_deep"] == [
        "In child pipeline self: " * 7 + "Again: child pipelines are nested more than 8 deep."]


def test_an_integer_variable_without_a_default_starts_at_0():
    r = run(pipeline(wait("W"), variables={"n": {"type": "Int"}, "m": {"type": "Integer"}}))
    assert r.variables == {"n": 0, "m": 0}


def test_a_cancelled_state_matches_no_condition():
    for conditions in (["Succeeded"], ["Failed"], ["Skipped"], ["Completed"], []):
        assert R._matches("cancelled", conditions) is False


def test_a_finished_record_is_saved_at_once_and_a_running_one_with_its_end():
    seen = []

    def save(r):
        seen.append((r.state, {a["name"]: a["state"] for a in r.to_json()["activities"]}))

    run(pipeline(fail("F"), wait("After", depends=("F",))), on_change=save)
    # The skipped step is saved as soon as it is skipped, while the run is still going.
    assert ("running", {"F": "failed", "After": "skipped"}) in seen
    # A step is saved when it ends, not when it starts.
    assert not any(acts.get("F") == "running" for _, acts in seen)


def test_a_notebook_steps_output_is_what_its_notebook_gave():
    class Notebooks:
        def check(self, a):
            return []

        def run(self, a, parameters, cancelled, deadline):
            return {"status": "Succeeded", "result": {"exitValue": "42"}}

        def notes(self):
            return []

        def finish(self):
            pass

    r = PipelineRun(loads(pipeline(act("N", "TridentNotebook", notebookId="x"))), wait_scale=0,
                    notebooks=Notebooks()).run()
    [rec] = [a for a in r.to_json()["activities"] if a["name"] == "N"]
    assert rec["state"] == "succeeded"
    assert rec["output"] == {"status": "Succeeded", "result": {"exitValue": "42"}}


def test_four_activities_run_at_once_by_default():
    r = PipelineRun(loads(pipeline(wait("W"))))
    assert r.max_parallel == 4 and r.to_json()["options"]["max_parallel"] == 4


def test_a_retry_waits_the_interval_the_policy_gives(monkeypatch):
    waited = []
    monkeypatch.setattr(R._Context, "wait", lambda self, seconds: waited.append(seconds))
    flaky = act("Flaky", "Fail", message="no", errorCode="E") | {"policy": {"retry": 2, "retryIntervalInSeconds": 7}}
    run(pipeline(flaky))
    assert waited == [7, 7]


def test_a_container_cancelled_while_its_steps_run_is_cancelled():
    import threading

    text = pipeline(act("If", "IfCondition", expression="@true", ifTrueActivities=[act("Slow", "Copy", source={}, sink={})]))
    r = PipelineRun(loads(text), mocks={"Slow": {"seconds": 5}}, wait_scale=1)
    threading.Timer(0.5, r.cancel).start()
    r.run()
    assert r.state == "cancelled"
    assert {a["name"]: a["state"] for a in r.to_json()["activities"]} == {"If": "cancelled", "Slow": "cancelled"}


def _context(text, **kw):
    r = PipelineRun(loads(text), wait_scale=0, **kw)
    assert r.check() == []
    a = r.pipeline.activities[0]
    return r, a, R._Context(r, a, r.scope, {"id": "rec"}, None)


def test_a_list_asked_for_after_a_cancel_records_nothing():
    r, a, ctx = _context(pipeline(wait("W")))
    r.cancel()
    with pytest.raises(R._Cancelled):
        ctx.run_list(r.pipeline.activities, r.scope, "inner")
    assert r.records == []


def test_a_child_pipeline_is_waited_on_unless_told_not_to():
    child = pipeline(wait("CW"), name="c")
    r, a, ctx = _context(pipeline(act("Run", "ExecutePipeline", pipeline={"referenceName": "c"}, waitOnCompletion=True)),
                         children={"c": child})
    out = ctx.run_child(a, "c", {})
    # Waited on: the output has the child's return value, which a child started without waiting lacks.
    assert set(out) == {"pipelineRunId", "pipelineReturnValue"}


def test_a_retry_waits_30_seconds_when_the_policy_gives_no_interval(monkeypatch):
    waited = []
    monkeypatch.setattr(R._Context, "wait", lambda self, seconds: waited.append(seconds))
    run(pipeline(act("Flaky", "Fail", message="no", errorCode="E") | {"policy": {"retry": 1}}))
    assert waited == [30]


def test_a_step_that_never_started_made_no_attempt():
    r = run(pipeline(fail("F"), wait("After", depends=("F",))))
    attempts = {a["name"]: a["attempts"] for a in r.to_json()["activities"]}
    assert attempts == {"F": 1, "After": 0}


def test_an_activity_that_isnt_local_is_refused_even_with_an_adapter_that_runs():
    import dataclasses

    from fabric_desktop.orchestration.activities import default_registry

    real = default_registry()

    class Registry:
        version = real.version

        def get(self, typ):
            entry = real.get(typ)
            return dataclasses.replace(entry, classification="cloud") if typ == "Wait" else entry

    problems = PipelineRun(loads(pipeline(wait("W"))), registry=Registry()).check()
    assert [(p["code"], p["message"]) for p in problems] == [
        ("not_local", "W (Wait) only runs in Fabric. Give a mock for it, or run the pipeline in Fabric.")]


def test_only_secure_string_parameters_are_hidden_in_the_record():
    r = run(pipeline(wait("W"), parameters={"a": {"type": "String", "defaultValue": "x"},
                                            "s": {"type": "SecureString", "defaultValue": "y"}}))
    assert r.to_json()["parameters"] == {"a": "x", "s": R.HIDDEN}


def test_an_inactive_step_marked_skipped_with_nothing_before_it_doesnt_fail_the_run():
    off = wait("Off", 60) | {"state": "Inactive", "onInactiveMarkAs": "Skipped"}
    r = run(pipeline(off, wait("On")))
    assert r.state == "succeeded"
    assert {a["name"]: a["state"] for a in r.to_json()["activities"]} == {"Off": "skipped", "On": "succeeded"}


def test_a_run_without_notebooks_says_it_ran_on_this_pc():
    notes = run(pipeline(wait("W"))).to_json()["notes"]
    assert notes[0] == "Ran on this PC; no Fabric job was started."


def test_a_runs_seconds_are_kept_to_the_millisecond(monkeypatch):
    import time
    import types

    calls = []

    def monotonic():
        calls.append(1)
        return 100.0 if len(calls) == 1 else 100.123456

    monkeypatch.setattr(R, "time", types.SimpleNamespace(monotonic=monotonic, sleep=time.sleep))
    r = run(pipeline(wait("W")))
    assert r.state == "succeeded" and r.seconds == 0.123


def _clock(monkeypatch, *readings):
    """R.time.monotonic gives these readings, then the last one again."""
    import time
    import types

    left = list(readings)
    monkeypatch.setattr(R, "time", types.SimpleNamespace(
        monotonic=lambda: left.pop(0) if len(left) > 1 else left[0], sleep=time.sleep))


def test_an_untils_deadline_has_passed_at_that_very_moment(monkeypatch):
    _clock(monkeypatch, 50.0)
    assert R._past(50.0) is True and R._past(50.5) is False and R._past(None) is False


def test_a_wait_that_ends_exactly_at_the_deadline_isnt_a_timeout(monkeypatch):
    r, a, _ = _context(pipeline(wait("W")))
    r.wait_scale = 1.0
    _clock(monkeypatch, 100.0, 106.0)
    R._Context(r, a, r.scope, {"id": "rec"}, 105.0).wait(5)  # ends at 105, the deadline: no timeout
    _clock(monkeypatch, 100.0, 106.0)
    with pytest.raises(R.ActivityFailed, match="policy timeout"):
        R._Context(r, a, r.scope, {"id": "rec"}, 104.0).wait(5)


def test_steps_still_waiting_for_a_slot_at_a_cancel_end_cancelled_without_starting():
    import threading

    text = pipeline(act("A", "Copy", source={}, sink={}), act("B", "Copy", source={}, sink={}))
    r = PipelineRun(loads(text), mocks={"A": {"seconds": 0}, "B": {"seconds": 0}}, wait_scale=1, max_parallel=1)
    assert r._slots.acquire(timeout=1)  # the run's only slot is taken, so both steps wait for it
    threading.Timer(0.5, r.cancel).start()
    t = threading.Thread(target=r.run, daemon=True)
    t.start()
    t.join(30)
    assert not t.is_alive()
    assert r.state == "cancelled"
    assert {a["name"]: (a["state"], a["attempts"]) for a in r.to_json()["activities"]} == {
        "A": ("cancelled", 0), "B": ("cancelled", 0)}
