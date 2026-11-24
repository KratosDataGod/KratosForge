"""Mutation batch 153, a re-score of orchestration/runner.py from batch 12 (docs/quality/mutation-testing.md):
a skipped activity made no attempt, a refused run with a parameter declared without an object still has
its record, and waits and child runs at their deadline exactly."""

from __future__ import annotations

import socket

import pytest

from fabric_desktop.orchestration import loads
from fabric_desktop.orchestration.runner import PipelineRun, RunRefused

from test_pipeline_runs import pipeline, run, wait


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("local pipeline runs must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_a_skipped_activity_made_no_attempt():
    r = run(pipeline(wait("A"), wait("B", depends=["A"], conditions=["Failed"])))
    [rec] = [a for a in r.to_json()["activities"] if a["name"] == "B"]
    assert rec["state"] == "skipped" and rec["attempts"] == 0


def test_a_refused_run_with_a_parameter_that_isnt_an_object_still_has_a_record():
    imp = loads(pipeline(wait("W"), parameters={"p": {"type": "String", "defaultValue": "x"}}))
    imp.pipeline.parameters["q"] = "String"  # a hand-made definition that isn't an object
    r = PipelineRun(imp, wait_scale=0)
    with pytest.raises(RunRefused, match="The parameter q has no type"):
        r.run()
    assert r.to_json()["parameters"]["p"] == "x"


# ---- deadlines at their exact edge, with this module's clock replaced


class _Clock:
    """time.monotonic for the runner: each call returns the next value, the last one for ever after."""

    def __init__(self, *values):
        self.values = list(values)

    def monotonic(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


class _Event:
    def __init__(self):
        self.waits = []

    def wait(self, seconds):
        self.waits.append(seconds)
        return False

    def is_set(self):
        return False


def _context(monkeypatch, clock, deadline):
    from types import SimpleNamespace

    from fabric_desktop.orchestration import runner as R

    monkeypatch.setattr(R, "time", clock)
    monkeypatch.setattr(R, "awake_monotonic", clock.monotonic)  # the deadline's clock
    run_ = SimpleNamespace(wait_scale=1.0, cancelled=False, _cancel=_Event())
    return R._Context(run_, SimpleNamespace(name="W"), None, {}, deadline), run_


def test_a_wait_that_ends_exactly_at_its_deadline_isnt_a_timeout(monkeypatch):
    ctx, run_ = _context(monkeypatch, _Clock(100.0, 100.6, 101.2), deadline=101.0)
    ctx.wait(1.0)  # ends at 101.0, the deadline itself: not past it
    assert run_._cancel.waits == [0.2]


def test_a_wait_stops_without_waiting_again_once_no_time_is_left(monkeypatch):
    ctx, run_ = _context(monkeypatch, _Clock(100.0, 101.0, 102.0), deadline=None)
    ctx.wait(1.0)
    assert run_._cancel.waits == []


def test_a_child_is_stopped_at_its_deadline_exactly(monkeypatch):
    ctx, _ = _context(monkeypatch, _Clock(50.0, 51.0), deadline=50.0)
    stopped, done = [], _Event()
    child = type("Child", (), {"cancel": lambda self, state="cancelled": stopped.append(state)})()
    ctx._watch_child(child, done)
    assert stopped == ["timed_out"] and done.waits == []
