"""The local pipeline runner (orchestration/runner.py), the paths the main tests don't reach: parameter
values given as text, run ids that aren't UUIDs, child pipelines that can't be read or nest too deep or
refuse at run time, a progress save that fails, the run record without values, and what one activity
can do once the run is cancelled. No network and no Fabric."""

from __future__ import annotations

import socket

import pytest

from fabric_desktop.orchestration import loads
from fabric_desktop.orchestration import runner as R
from fabric_desktop.orchestration.adapters import ActivityFailed
from fabric_desktop.orchestration.runner import PipelineRun, RunRefused

from test_pipeline_runs import act, fail, pipeline, run, setvar, states


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("local pipeline runs must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def call(name="Call", key="child", wait=True, **tp):
    return act(name, "ExecutePipeline", pipeline={"referenceName": key}, waitOnCompletion=wait, **tp)


# ---- parameters given as text


@pytest.mark.parametrize("declared, text, expected", [
    ("float", "2.5", 2.5),
    ("bool", "TRUE", True),
    ("boolean", "false", False),
    ("array", "[1, 2]", [1, 2]),
    ("object", '{"a": 1}', {"a": 1}),
    ("string", "as is", "as is"),
])
def test_a_parameter_given_as_text_becomes_its_declared_type(declared, text, expected):
    assert R.coerce(text, {"type": declared}) == expected


def test_a_parameter_value_that_isnt_its_type_refuses_the_run():
    for declared, text in (("bool", "yes"), ("array", "[1,"), ("int", "two")):
        with pytest.raises(RunRefused, match=f"isn't a valid {declared}"):
            R.coerce(text, {"type": declared})
    text = pipeline(act("W", "Wait", waitTimeInSeconds=0), parameters={"on": {"type": "bool"}})
    with pytest.raises(RunRefused, match="Parameter on: 'maybe' isn't a valid bool"):
        run(text, parameters={"on": "maybe"})


def test_a_run_id_that_isnt_a_uuid_still_gives_stable_activity_ids():
    text = pipeline(act("W", "Wait", waitTimeInSeconds=0))
    first, again = run(text, run_id="nightly-1"), run(text, run_id="nightly-1")
    assert first.id == "nightly-1"
    assert first.to_json()["activities"][0]["id"] == again.to_json()["activities"][0]["id"]


# ---- child pipelines


def test_a_child_definition_given_as_text_runs():
    child = pipeline(act("W", "Wait", waitTimeInSeconds=0), name="child")
    r = run(pipeline(call()), children={"child": child})
    assert r.state == "succeeded"


def test_a_child_definition_that_cant_be_read_refuses_the_run():
    with pytest.raises(RunRefused) as exc:
        run(pipeline(call()), children={"child": "{not json"})
    assert [p["code"] for p in exc.value.problems] == ["child_definition"]
    assert "the definition given for 'child' can't be read" in exc.value.problems[0]["message"]


def test_child_pipelines_nested_too_deep_refuse_the_run():
    child = pipeline(act("W", "Wait", waitTimeInSeconds=0), name="child")
    r = PipelineRun(loads(pipeline(call())), children={"child": child}, depth=R.MAX_DEPTH - 1, wait_scale=0)
    problems = r.check()
    assert [p["code"] for p in problems] == ["too_deep"]
    assert f"nested more than {R.MAX_DEPTH} deep" in problems[0]["message"]


def _needs_a_value():
    return pipeline(act("W", "Wait", waitTimeInSeconds=0), parameters={"who": {"type": "string"}}, name="child")


def test_a_child_that_refuses_at_run_time_fails_its_activity():
    r = run(pipeline(call()), children={"child": _needs_a_value()})
    row = r.to_json()["activities"][0]
    assert r.state == "failed" and row["error"]["errorCode"] == "LocalChildRefused"
    assert "Child pipeline child can't run" in row["error"]["message"]


def test_a_child_nothing_waits_on_that_refuses_fails_its_activity():
    r = run(pipeline(call(wait=False)), children={"child": _needs_a_value()})
    row = r.to_json()["activities"][0]
    assert row["state"] == "failed" and row["error"]["errorCode"] == "LocalChildRefused"


def test_a_child_nothing_waits_on_that_stops_with_an_error_is_logged(monkeypatch):
    original = PipelineRun.run

    def crash(self):
        if self.depth:
            raise RuntimeError("engine stopped")
        return original(self)

    said = []
    monkeypatch.setattr(PipelineRun, "run", crash)
    monkeypatch.setattr(R._log, "warning", lambda msg, *args: said.append(msg % args))
    child = pipeline(act("W", "Wait", waitTimeInSeconds=0), name="child")
    r = run(pipeline(call(wait=False)), children={"child": child})
    assert r.state == "succeeded"
    assert said == ["Child pipeline child stopped: RuntimeError: engine stopped"]


# ---- the run and its record


def test_a_progress_save_that_fails_doesnt_stop_the_run(monkeypatch):
    said = []
    monkeypatch.setattr(R._log, "warning", lambda msg, *args: said.append(msg % args))

    def broken(r):
        raise OSError("disk full")

    r = run(pipeline(act("W", "Wait", waitTimeInSeconds=0)), on_change=broken)
    assert r.state == "succeeded" and said
    assert all(s.startswith("Couldn't save the run's progress; the run goes on") for s in said)


def test_the_record_without_values_keeps_only_the_error_code():
    r = run(pipeline(fail("F", message="secret detail", code="E7")))
    row = r.to_json(values=False)["activities"][0]
    assert row["error"] == {"errorCode": "E7"} and "output" not in row


def test_a_cancelled_activity_matches_no_condition():
    assert not R._matches("cancelled", ["Succeeded", "Failed", "Completed", "Skipped"])
    assert R._matches("skipped", ["Skipped"]) and not R._matches("skipped", [])


def test_a_variable_that_would_grow_too_big_fails_the_activity(monkeypatch):
    monkeypatch.setattr(R, "over", lambda nodes, chars: "too much" if chars > 3 else "")
    text = pipeline(setvar("S", "v", "long text"), variables={"v": {"type": "String"}})
    row = run(text).to_json()["activities"][0]
    assert row["error"]["errorCode"] == "LocalValueTooBig"
    assert row["error"]["message"] == "Variable 'v' would hold too much, the limit for one pipeline value."


# ---- what one activity can do


class Reader:
    """A stand-in for the engine's data reader and notebook runner."""

    def __init__(self, run=None, cancel_after=False, fail=False):
        self.pipeline_run, self.cancel_after, self.fail = run, cancel_after, fail

    def _go(self):
        if self.cancel_after:
            self.pipeline_run.cancel()
        if self.fail:
            raise ActivityFailed("read failed", "LocalRead")
        return {"ok": True}

    def script(self, activity, properties, cancelled, deadline):
        return self._go()

    def run(self, activity, parameters, cancelled, deadline):
        return self._go()


def context(typ="Wait", variables=None):
    r = PipelineRun(loads(pipeline(act("A", typ), variables=variables)), wait_scale=0)
    a = r.pipeline.activities[0]
    return r, a, R._Context(r, a, r.scope, {"id": "rec"}, None)


def test_an_activity_without_the_engine_cant_read_data_or_run_a_notebook():
    r, a, ctx = context("Lookup")
    with pytest.raises(ActivityFailed, match="can't read a lakehouse without the engine"):
        ctx.read_data(a, {})
    with pytest.raises(ActivityFailed, match="can't run without the engine"):
        ctx.run_notebook(a, {})
    with pytest.raises(ActivityFailed, match="No definition was given for child pipeline 'nope'"):
        ctx.run_child(a, "nope", {})
    with pytest.raises(ActivityFailed, match="Variable 'nope' isn't declared"):
        ctx.set_variable("nope", 1)
    assert ctx.wait_scale == 0


def test_a_script_returns_its_output_or_its_failure():
    r, a, ctx = context("Script")
    r.data = Reader(r)
    assert ctx.read_data(a, {}) == {"ok": True}
    r.data = Reader(r, fail=True)
    with pytest.raises(ActivityFailed, match="read failed"):
        ctx.read_data(a, {})


@pytest.mark.parametrize("fails", [False, True])
def test_a_script_or_notebook_that_ends_after_the_run_was_cancelled_is_cancelled(fails):
    r, a, ctx = context("Script")
    r.data = r.notebooks = Reader(r, cancel_after=True, fail=fails)
    with pytest.raises(R._Cancelled):
        ctx.read_data(a, {})
    with pytest.raises(R._Cancelled):
        ctx.run_notebook(a, {})


def test_a_cancelled_run_runs_no_list_and_no_more_iterations():
    r, a, ctx = context()
    r.cancel()
    with pytest.raises(R._Cancelled):
        ctx.run_list([a], r.scope, "inner")
    with pytest.raises(R._Cancelled):
        ctx.iterate([], 1, lambda i, item: "succeeded")


def test_an_inner_list_that_ends_cancelled_cancels_its_container(monkeypatch):
    r, a, ctx = context()
    monkeypatch.setattr(r, "_run_list", lambda *args: "cancelled")
    with pytest.raises(R._Cancelled):
        ctx.run_list([a], r.scope, "inner")


def test_an_iteration_that_is_cancelled_is_recorded_as_cancelled():
    r, a, ctx = context()

    def body(i, item):
        if item == "stop":
            raise R._Cancelled()
        return "succeeded"

    assert ctx.iterate(["go", "stop"], 2, body) == ["succeeded", "cancelled"]


def test_an_inactive_failure_and_skip_marks_are_kept():
    def inactive(name, mark):
        return act(name, "Wait", waitTimeInSeconds=0) | {"state": "Inactive", "onInactiveMarkAs": mark}

    r = run(pipeline(inactive("A", "Failed"), inactive("B", "Skipped"), inactive("C", "Odd")))
    assert states(r) == {"A": "failed", "B": "skipped", "C": "succeeded"}
