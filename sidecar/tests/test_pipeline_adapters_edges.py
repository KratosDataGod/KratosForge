"""Pipeline activities run on this PC: the failure paths the run tests don't reach, with a
stand-in run context. Nothing touches data, files or the network."""

import pytest

from fabric_desktop.orchestration import adapters as ad
from fabric_desktop.orchestration.definition import Activity
from fabric_desktop.orchestration.evaluate import Scope


class Ctx:
    def __init__(self, outcomes=("succeeded",), clock=None, **variables):
        self.scope = Scope(variables=dict(variables))
        self.outcomes, self.notes, self.waits, self.calls = list(outcomes), {}, [], []
        self._clock = clock or (lambda: 0.0)

    def run_list(self, activities, scope, label, until=None):
        self.calls.append(label)
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]

    def iterate(self, items, batch, body):
        return [body(i, item) for i, item in enumerate(items)]

    def wait(self, seconds):
        self.waits.append(seconds)

    def clock(self):
        return self._clock()

    def note(self, **fields):
        self.notes.update(fields)

    def run_child(self, activity, key, parameters, wait=True):
        return {"key": key, "parameters": parameters, "wait": wait}

    def read_data(self, activity, properties):
        return properties


def act(type_, tp=None, **raw):
    return Activity(name="A", type=type_, path="$.a", raw={"typeProperties": tp or {}, **raw})


def failed(adapter, activity, ctx):
    with pytest.raises(ad.ActivityFailed) as raised:
        adapter.execute(activity, ctx)
    return raised.value.error


def test_the_base_local_adapter_has_nothing_to_run():
    with pytest.raises(NotImplementedError):
        ad.LocalAdapter().execute(act("X"), Ctx())


def test_a_condition_that_isnt_true_or_false_fails():
    err = failed(ad.IfCondition(), act("IfCondition", {"expression": {"value": "@add(1, 1)", "type": "Expression"}}), Ctx())
    assert err == {"errorCode": "LocalExpressionError", "message": "The condition returned 2, not true or false.",
                   "failureType": "UserError"}


def test_a_switch_whose_branch_fails_fails():
    tp = {"on": "b", "cases": [{"value": "a"}, {"value": "b"}]}
    err = failed(ad.Switch(), act("Switch", tp), Ctx(outcomes=["failed"]))
    assert err["message"] == "An activity in cases[1] failed." and err["errorCode"] == "LocalInnerActivityFailed"


def test_foreach_needs_an_array():
    err = failed(ad.ForEach(), act("ForEach", {"items": "one"}), Ctx())
    assert err["message"] == "items returned str, not an array."


def test_an_until_loop_stops_when_its_run_is_cancelled():
    ctx = Ctx(outcomes=["cancelled"])
    assert ad.Until().execute(act("Until", {"expression": False}), ctx) == {}
    assert ctx.notes == {"iterations": 1}


def test_an_until_condition_that_isnt_true_or_false_fails():
    err = failed(ad.Until(), act("Until", {"expression": "yes"}), Ctx())
    assert err["message"] == "The Until condition returned 'yes', not true or false."


def test_an_until_loop_that_passes_its_timeout_after_checking_fails():
    ticks = iter([0.0, 0.0, 5.0])  # start, after the run, after the condition
    ctx = Ctx(clock=lambda: next(ticks))
    err = failed(ad.Until(), act("Until", {"expression": False, "timeout": "0:00:01"}), ctx)
    assert err["errorCode"] == "LocalUntilTimeout" and ctx.notes == {"iterations": 1}


def test_an_until_loop_that_never_ends_stops_at_its_limit():
    err = failed(ad.Until(max_iterations=3), act("Until", {"expression": False}), Ctx())
    assert err == {"errorCode": "LocalUntilTooManyIterations", "failureType": "UserError",
                   "message": "The Until loop ran 3 times without its condition becoming true."}


def test_wait_takes_digits_as_seconds_and_refuses_anything_else():
    ctx = Ctx()
    ad.Wait().execute(act("Wait", {"waitTimeInSeconds": " 5 "}), ctx)
    assert ctx.waits == [5]
    for bad in (-1, True, "soon"):
        assert "not a number of seconds" in failed(ad.Wait(), act("Wait", {"waitTimeInSeconds": bad}), ctx)["message"]


def test_filter_needs_an_array_and_a_true_or_false_condition():
    assert failed(ad.Filter(), act("Filter", {"items": "x", "condition": True}), Ctx())["message"] == \
        "items didn't return an array."
    err = failed(ad.Filter(), act("Filter", {"items": [1], "condition": "maybe"}), Ctx())
    assert err["message"] == "The condition returned 'maybe', not true or false."


def test_a_float_parameter_wont_take_a_flag():
    with pytest.raises(ad.ActivityFailed, match="True isn't a valid float"):
        ad.notebook_value("rate", True, "float")
    assert ad.notebook_value("rate", "1.5", "float") == 1.5


def test_notebook_parameters_must_be_an_object():
    err = failed(ad.Notebook(), act("TridentNotebook", {"parameters": ["x"]}), Ctx())
    assert err["message"] == "parameters must be an object."


def test_a_script_reads_its_connection_from_the_activity():
    out = ad.ReadData().execute(act("Script", {"scripts": []}, linkedService={"name": "Warehouse"}), Ctx())
    assert out == {"scripts": [], "linkedService": {"name": "Warehouse"}}


def test_child_pipeline_parameters_must_be_an_object():
    err = failed(ad.ExecutePipeline(), act("ExecutePipeline", {"parameters": "@createArray(1)"}), Ctx())
    assert err["message"] == "parameters must be an object."


def test_a_timespan_that_isnt_one_is_none():
    assert ad.timespan("1.02:00:00") == 93600
    assert ad.timespan("soon") is None and ad.timespan(30) is None
