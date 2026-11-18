"""Mutation batch 90: orchestration/adapters.py, the control-flow activities run on this PC. Pins
what the earlier tests let through: ForEach's batch size, which activities are containers, what
each activity returns, the Switch value matched as text, Until's iteration limit, and how a Notebook
activity's parameters are named and typed."""

from __future__ import annotations

import pytest

from fabric_desktop.orchestration import adapters as ad
from fabric_desktop.orchestration.definition import Activity
from fabric_desktop.orchestration.evaluate import Scope


class Ctx:
    """A stand-in run context: records what the adapter asks for and runs every branch as succeeded."""

    def __init__(self, outcome="succeeded", clock=(0.0,)):
        self.scope = Scope(variables={})
        self.notes, self.batches, self.lists, self.variables, self.notebooks = {}, [], [], [], []
        self.returned, self.outcome, self._clock = {}, outcome, list(clock)

    def run_list(self, activities, scope, label, until=None):
        self.lists.append(label)
        return self.outcome

    def iterate(self, items, batch, body):
        self.batches.append(batch)
        return [body(i, item) for i, item in enumerate(items)]

    def wait(self, seconds):
        pass

    def clock(self):
        return self._clock.pop(0) if len(self._clock) > 1 else self._clock[0]

    def note(self, **fields):
        self.notes.update(fields)

    def set_variable(self, name, value, append=False):
        self.variables.append((name, value, append))

    def set_return_value(self, key, value):
        self.returned[key] = value

    def run_notebook(self, activity, parameters):
        self.notebooks.append(parameters)
        return {"ran": parameters}


def act(type_, tp=None):
    return Activity(name="A", type=type_, path="$.a", raw={"typeProperties": tp or {}})


# ---- ForEach batch size


@pytest.mark.parametrize("tp, batch", [
    ({}, 20),                              # Data Factory's default when batchCount is left out
    ({"batchCount": 7}, 7),
    ({"batchCount": True}, 20),            # not a number of iterations
    ({"batchCount": "5"}, 20),
    ({"batchCount": 7, "isSequential": True}, 1),
    ({"batchCount": 80}, 50),
    ({"batchCount": -3}, 1),
])
def test_foreach_runs_its_iterations_in_batches_of_the_size_given(tp, batch):
    ctx = Ctx()
    assert ad.ForEach().execute(act("ForEach", {"items": [1, 2, 3], **tp}), ctx) == {}
    assert ctx.batches == [batch]
    assert ctx.notes == {"iterations": 3}


# ---- containers and what each activity returns


def test_the_activities_that_hold_other_activities_are_containers():
    for cls in (ad.IfCondition, ad.Switch, ad.ForEach, ad.Until, ad.ExecutePipeline):
        assert cls.container is True, cls.__name__
    for cls in (ad.SetVariable, ad.AppendVariable, ad.Wait, ad.Fail, ad.Filter, ad.Notebook, ad.ReadData):
        assert cls.container is False, cls.__name__


def test_append_variable_and_wait_return_an_empty_output():
    ctx = Ctx()
    assert ad.AppendVariable().execute(act("AppendVariable", {"variableName": "v", "value": 3}), ctx) == {}
    assert ctx.variables == [("v", 3, True)]
    assert ad.Wait().execute(act("Wait", {"waitTimeInSeconds": 1}), ctx) == {}


def test_a_switch_on_a_number_matches_the_case_written_as_text():
    ctx = Ctx()
    tp = {"on": {"value": "@add(1, 1)", "type": "Expression"}, "cases": [{"value": "1"}, {"value": "2"}]}
    assert ad.Switch().execute(act("Switch", tp), ctx) == {}
    assert ctx.notes == {"branch": "cases[1]"}
    assert ctx.lists == ["cases[1]"]


def test_an_until_loop_runs_ten_thousand_times_at_most_by_default():
    assert ad.Until().max_iterations == 10_000
    ctx = Ctx()
    with pytest.raises(ad.ActivityFailed) as raised:
        ad.Until().execute(act("Until", {"expression": False}), ctx)
    assert raised.value.error["message"] == "The Until loop ran 10000 times without its condition becoming true."
    assert len(ctx.lists) == 10_000


# ---- Notebook parameters


@pytest.mark.parametrize("value, typ, expected", [
    ({"a": 1}, "string", '{"a": 1}'),
    ([1, 2], "string", "[1, 2]"),
    (5, "string", "5"),
    ("x", None, "x"),
    ("TRUE", "bool", True),
    ("false", "Bool", False),
    (3.0, "int", 3),
    ("7", "int", 7),
])
def test_a_notebook_parameter_is_given_as_its_type(value, typ, expected):
    out = ad.notebook_value("p", value, typ)
    assert out == expected and type(out) is type(expected)


@pytest.mark.parametrize("value, typ", [("yes", "bool"), (1, "bool"), (3.5, "int"), (True, "int")])
def test_a_notebook_parameter_that_isnt_its_type_fails(value, typ):
    with pytest.raises(ad.ActivityFailed) as raised:
        ad.notebook_value("p", value, typ)
    assert raised.value.error == {"errorCode": "LocalExpressionError", "failureType": "UserError",
                                  "message": f"Parameter p: {value!r} isn't a valid {typ}."}


def test_a_notebook_parameter_of_an_unknown_type_fails():
    with pytest.raises(ad.ActivityFailed) as raised:
        ad.notebook_value("p", "2026-01-01", "Date")
    assert raised.value.error["message"] == "Parameter p: type 'date' isn't one of string, int, float or bool."
    assert raised.value.error["errorCode"] == "LocalExpressionError"


@pytest.mark.parametrize("name", ["class", "1st", "my-name"])
def test_a_notebook_parameter_must_be_a_python_name(name):
    ctx = Ctx()
    with pytest.raises(ad.ActivityFailed) as raised:
        ad.Notebook().execute(act("TridentNotebook", {"parameters": {name: {"value": 1, "type": "int"}}}), ctx)
    assert raised.value.error["message"] == f"Parameter {name!r} isn't a Python name, so a notebook can't use it."
    assert ctx.notebooks == []


def test_a_notebook_gets_its_parameters_typed():
    ctx = Ctx()
    tp = {"parameters": {"rows": {"value": "10", "type": "int"}, "name": "orders"}}
    assert ad.Notebook().execute(act("TridentNotebook", tp), ctx) == {"ran": {"rows": 10, "name": "orders"}}


def test_foreach_takes_up_to_its_item_limit_and_no_more():
    ctx = Ctx()
    items = list(range(ad.MAX_ITEMS))
    assert ad.ForEach().execute(act("ForEach", {"items": items}), ctx) == {}
    with pytest.raises(ad.ActivityFailed) as raised:
        ad.ForEach().execute(act("ForEach", {"items": items + [0]}), Ctx())
    assert raised.value.error["errorCode"] == "LocalForEachTooManyItems"


# ---- Until


def test_an_until_loop_that_is_done_returns_an_empty_output():
    ctx = Ctx()
    assert ad.Until().execute(act("Until", {"expression": True}), ctx) == {}
    assert ctx.notes == {"iterations": 1}


def test_an_until_loop_past_its_timeout_after_the_first_iteration_counts_one():
    ctx = Ctx(clock=(0.0, 5.0))  # the deadline is 1 s away; the first iteration ends 5 s in
    with pytest.raises(ad.ActivityFailed) as raised:
        ad.Until().execute(act("Until", {"expression": False, "timeout": "00:00:01"}), ctx)
    assert raised.value.error["errorCode"] == "LocalUntilTimeout"
    assert ctx.notes == {"iterations": 1}


def test_an_until_loop_that_reaches_its_deadline_exactly_has_timed_out():
    ctx = Ctx(clock=(0.0, 0.0, 1.0))  # start, after the iteration, after the condition
    with pytest.raises(ad.ActivityFailed) as raised:
        ad.Until().execute(act("Until", {"expression": False, "timeout": "00:00:01"}), ctx)
    assert raised.value.error["message"] == "The Until loop reached its timeout."
    assert ctx.notes == {"iterations": 1}


def test_an_until_loop_whose_first_iteration_fails_names_iteration_one():
    ctx = Ctx(outcome="failed")
    with pytest.raises(ad.ActivityFailed) as raised:
        ad.Until().execute(act("Until", {"expression": False}), ctx)
    assert raised.value.error["message"] == "An activity failed in iteration 1."
    assert ctx.notes == {"iterations": 1}


def test_a_timespan_reads_minutes_and_days():
    assert ad.timespan("00:02:00") == 120
    assert ad.timespan("1.00:00:30") == 86_430
    assert ad.timespan(30) is None and ad.timespan("soon") is None


# ---- the pipeline's return value


def test_only_named_entries_set_the_pipeline_return_value():
    ctx = Ctx()
    entries = ["junk", {"key": 1, "value": "x"}, {"key": "a", "value": {"type": "String", "content": "x"}},
               {"key": "b", "value": 2}]
    out = ad.SetVariable().execute(act("SetVariable", {"setSystemVariable": True, "value": entries}), ctx)
    assert out == {"name": "pipelineReturnValue", "value": {"a": "x", "b": 2}}
    assert ctx.returned == {"a": "x", "b": 2}
