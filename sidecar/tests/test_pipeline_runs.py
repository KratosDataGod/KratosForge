"""ORCH-003 local pipeline runs: the expression evaluator, the DAG runner, adapters, mocks, child
pipelines, cancel and timeouts, run records and the APIs. No network and no Fabric."""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fabric_desktop import audit, server
from fabric_desktop.cli import main as cli_main
from fabric_desktop.integrations.core import CallContext, Caller
from fabric_desktop.integrations.registry import default_registry as capability_registry
from fabric_desktop.orchestration import load, loads
from fabric_desktop.orchestration.activities import default_registry
from fabric_desktop.orchestration.evaluate import FUNCTIONS, ExpressionError, Scope, evaluate
from fabric_desktop.orchestration.runner import PipelineRun, RunRefused
from fabric_desktop.orchestration.runs import KEEP, RunStore
from fabric_desktop.pipeline_service import PipelineMixin, PipelineRunMixin

FIXTURES = Path(__file__).parent / "pipelines"
REFERENCE = FIXTURES / "reference_local.json"
MOCKS = {"Ingest orders": {"output": {"rowsCopied": 120}}, "Transform": {"output": {"exitValue": "ok"}}}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("local pipeline runs must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def pipeline(*activities, parameters=None, variables=None, name="p"):
    props = {"activities": list(activities)}
    if parameters is not None:
        props["parameters"] = parameters
    if variables is not None:
        props["variables"] = variables
    return json.dumps({"name": name, "properties": props})


def act(name, typ, depends=(), conditions=("Succeeded",), **tp):
    deps = [{"activity": d, "dependencyConditions": list(conditions)} for d in depends]
    return {"name": name, "type": typ, "dependsOn": deps, "typeProperties": tp}


def wait(name, seconds=0, depends=(), conditions=("Succeeded",)):
    return act(name, "Wait", depends, conditions, waitTimeInSeconds=seconds)


def setvar(name, var, value, depends=(), conditions=("Succeeded",)):
    return act(name, "SetVariable", depends, conditions, variableName=var, value=value)


def fail(name, depends=(), message="boom", code="E1"):
    return act(name, "Fail", depends, message=message, errorCode=code)


def run(text, **kw):
    kw.setdefault("wait_scale", 0)
    return PipelineRun(loads(text), **kw).run()


def states(r):
    return {a["name"]: a["state"] for a in r.to_json()["activities"] if not a["label"]}


# ---- expressions

NOW = datetime(2026, 10, 18, 6, 30, 5, 123456, tzinfo=timezone.utc)


def scope():
    return Scope(parameters={"day": "2026-10-17", "n": 3, "list": [1, 2, 3]},
                 variables={"files": ["a.csv", "b.csv"], "flag": True},
                 activities={"Look": {"output": {"firstRow": {"Watermark": 7}}, "status": "Succeeded"}},
                 system={"RunId": "r-1", "Pipeline": "pl"}, now=lambda: NOW)


@pytest.mark.parametrize("text,expected", [
    ("@pipeline().parameters.day", "2026-10-17"),
    ("@pipeline().RunId", "r-1"),
    ("@concat('a', string(add(1, 2)))", "a3"),
    ("Run @{pipeline().RunId} on @{pipeline().parameters.day}", "Run r-1 on 2026-10-17"),
    ("@@not an expression", "@not an expression"),
    ("plain text", "plain text"),
    ("@equals(length(variables('files')), 2)", True),
    ("@and(variables('flag'), greater(pipeline().parameters.n, 2))", True),
    ("@if(less(1, 2), 'yes', 'no')", "yes"),
    ("@activity('Look').output.firstRow.watermark", 7),
    ("@activity('Look')?.output?.missing?.deeper", None),
    ("@createArray(10, 20, 30)[1]", 20),
    ("@div(7, 2)", 3),
    ("@div(7.0, 2)", 3.5),
    ("@mod(7, 3)", 1),
    ("@sub(mul(2, 5), 1)", 9),
    ("@toUpper(substring('fabric', 0, 3))", "FAB"),
    ("@split('a,b', ',')", ["a", "b"]),
    ("@join(createArray('a', 'b'), '-')", "a-b"),
    ("@contains(variables('files'), 'a.csv')", True),
    ("@startsWith('Orders.csv', 'orders')", True),
    ("@coalesce(null, 'x')", "x"),
    ("@empty(createArray())", True),
    ("@first(pipeline().parameters.list)", 1),
    ("@last(pipeline().parameters.list)", 3),
    ("@union(createArray(1, 2), createArray(2, 3))", [1, 2, 3]),
    ("@int('42')", 42),
    ("@json('{\"a\": 1}').a", 1),
    ("@formatDateTime(utcNow(), 'yyyy-MM-dd HH:mm:ss')", "2026-10-18 06:30:05"),
    ("@utcNow()", "2026-10-18T06:30:05.1234560Z"),
    ("@addDays(pipeline().parameters.day, 1, 'yyyy-MM-dd')", "2026-10-18"),
    ("@startOfDay(utcNow(), 'yyyy-MM-ddTHH:mm:ss')", "2026-10-18T00:00:00"),
    ("@range(1, 3)", [1, 2, 3]),
    ({"value": "@pipeline().parameters.n", "type": "Expression"}, 3),
    ({"nested": ["@pipeline().parameters.n"]}, {"nested": [3]}),
])
def test_expressions(text, expected):
    assert evaluate(text, scope()) == expected


@pytest.mark.parametrize("text,needle", [
    ("@eval('1+1')", "eval() isn't supported"),
    ("@__import__('os')", "__import__() isn't supported"),
    ("@variables('nope')", "isn't declared"),
    ("@activity('Later').output", "hasn't run yet"),
    ("@item()", "only available inside a ForEach"),
    ("@div(1, 0)", "divide by zero"),
    ("@createArray(1)[5]", "outside an array"),
    ("@concat('a'", "bracket"),
    ("@greater('a', 1)", "compares two numbers or two strings"),
    ("@substring('abc', 1, 9)", "past the end"),
    ("@formatDateTime(utcNow(), 'yyyy gg')", "doesn't support"),
    ("@os", "isn't a value"),
    ("@range(0, 500000000)", "between 0 and 100,000"),
    ("@range(0, -1)", "between 0 and 100,000"),
])
def test_expression_errors(text, needle):
    with pytest.raises(ExpressionError, match=re.escape(needle)):
        evaluate(text, scope())


def test_function_list_is_fixed():
    assert "eval" not in FUNCTIONS and "exec" not in FUNCTIONS
    assert {"concat", "equals", "variables", "activity", "item", "pipeline", "utcnow"} <= set(FUNCTIONS)


def test_foreach_scope_keeps_iteration_results_apart():
    s = scope()
    a, b = s.child("x"), s.child("y")
    a.activities["Inner"] = {"output": 1}
    assert "Inner" not in b.activities and "Inner" not in s.activities
    assert evaluate("@item()", b) == "y" and evaluate("@activity('Look').output.firstRow.Watermark", a) == 7


# ---- dependencies and outcomes

def test_dependency_order_and_parallel_branches():
    text = pipeline(wait("A"), wait("B", depends=["A"]), wait("C", depends=["A"]), wait("D", depends=["B", "C"]))
    r = run(text)
    assert r.state == "succeeded" and set(states(r).values()) == {"succeeded"}
    started = {a["name"]: a["started"] for a in r.to_json()["activities"]}
    assert started["A"] <= started["B"] and started["A"] <= started["C"] and max(started["B"], started["C"]) <= started["D"]


def test_independent_activities_run_in_parallel_up_to_max_parallel():
    text = pipeline(act("M1", "Copy", source={}, sink={}), act("M2", "Copy", source={}, sink={}))
    mocks = {"M1": {"seconds": 0.3}, "M2": {"seconds": 0.3}}

    def took(max_parallel):
        t = time.monotonic()
        run(text, mocks=mocks, wait_scale=1, max_parallel=max_parallel)
        return time.monotonic() - t

    # Compare the two runs with each other, not with a fixed limit, so a slow CI machine can't flake
    # it: both carry the same overhead, and only one-at-a-time waits out the second 0.3 s mock.
    both, one = took(2), took(1)
    assert one >= 0.55 and one - both >= 0.15, (both, one)


@pytest.mark.parametrize("conditions,parent_fails,expected", [
    (["Succeeded"], False, "succeeded"), (["Succeeded"], True, "skipped"),
    (["Failed"], True, "succeeded"), (["Failed"], False, "skipped"),
    (["Completed"], True, "succeeded"), (["Completed"], False, "succeeded"),
    (["Skipped"], False, "skipped"),
])
def test_dependency_conditions(conditions, parent_fails, expected):
    parent = fail("P") if parent_fails else wait("P")
    r = run(pipeline(parent, wait("Next", depends=["P"], conditions=conditions)))
    assert states(r)["Next"] == expected


def test_skipped_condition_runs_after_a_skipped_activity():
    text = pipeline(wait("A"), wait("B", depends=["A"], conditions=["Failed"]),
                    wait("C", depends=["B"], conditions=["Skipped"]))
    assert states(run(text)) == {"A": "succeeded", "B": "skipped", "C": "succeeded"}


def test_try_catch_succeeds_and_if_else_fails():
    # Data Factory's documented patterns: a failure handled by a leaf that succeeds is a success...
    assert run(pipeline(fail("Try"), wait("Catch", depends=["Try"], conditions=["Failed"]))).state == "succeeded"
    # ...but a skipped leaf is judged by its parent, which failed.
    text = pipeline(fail("Try"), wait("Then", depends=["Try"]), wait("Else", depends=["Try"], conditions=["Failed"]))
    assert run(text).state == "failed"


def test_inactive_activities_are_marked_without_running():
    a = wait("Off", 60) | {"state": "Inactive", "onInactiveMarkAs": "Skipped"}
    b = fail("Off2") | {"state": "Inactive"}
    r = run(pipeline(a, b, wait("After", depends=["Off2"])), wait_scale=1)
    rows = {x["name"]: x for x in r.to_json()["activities"]}
    assert rows["Off"]["state"] == "skipped" and rows["Off2"]["state"] == "succeeded"
    assert rows["Off"]["executed_by"] == "inactive" and r.seconds < 1


# ---- adapters

def test_variables_and_return_value():
    text = pipeline(setvar("S", "v", "@concat('x', 'y')"),
                    act("A", "AppendVariable", ["S"], variableName="list", value="@variables('v')"),
                    act("R", "SetVariable", ["A"], variableName="pipelineReturnValue", setSystemVariable=True,
                        value=[{"key": "out", "value": {"type": "Expression", "content": "@variables('list')"}}]),
                    variables={"v": {"type": "String"}, "list": {"type": "Array", "defaultValue": []}})
    r = run(text).to_json()
    assert r["variables"] == {"v": "xy", "list": ["xy"]} and r["return_value"] == {"out": ["xy"]}
    s = next(a for a in r["activities"] if a["name"] == "S")
    assert s["output"] == {"name": "v", "value": "xy"}


def test_variable_errors_fail_the_activity():
    text = pipeline(act("A", "AppendVariable", variableName="s", value=1), variables={"s": {"type": "String"}})
    r = run(text).to_json()
    assert r["state"] == "failed" and r["activities"][0]["error"]["errorCode"] == "LocalVariableType"


def test_if_condition_and_switch():
    text = pipeline(
        act("If", "IfCondition", expression="@greater(pipeline().parameters.n, 1)",
            ifTrueActivities=[setvar("T", "x", "true")], ifFalseActivities=[setvar("F", "x", "false")]),
        act("Sw", "Switch", ["If"], on="@pipeline().parameters.region",
            cases=[{"value": "emea", "activities": [setvar("E", "y", "emea")]}],
            defaultActivities=[setvar("D", "y", "default")]),
        parameters={"n": {"type": "int", "defaultValue": 2}, "region": {"type": "string", "defaultValue": "amer"}},
        variables={"x": {"type": "String"}, "y": {"type": "String"}})
    r = run(text)
    assert r.variables == {"x": "true", "y": "default"}
    rows = {a["name"]: a for a in r.to_json()["activities"]}
    assert rows["If"]["branch"] == "ifTrueActivities" and rows["Sw"]["branch"] == "defaultActivities"
    assert rows["If"]["output"] == {"ifExpressionResult": True}
    assert run(text, parameters={"region": "emea"}).variables["y"] == "emea"


def test_inner_failure_fails_the_container():
    text = pipeline(act("If", "IfCondition", expression="@true", ifTrueActivities=[fail("Bad")]),
                    wait("Next", depends=["If"]))
    assert states(run(text)) == {"If": "failed", "Next": "skipped"}


def test_foreach_iterations_parallel_sequential_and_results():
    inner = [act("Each", "Copy", source={}, sink={}),
             act("Note", "AppendVariable", ["Each"], variableName="seen",
                 value="@concat(item(), ':', string(activity('Each').output.n))")]
    text = pipeline(act("Loop", "ForEach", items="@pipeline().parameters.xs", batchCount=3, activities=inner),
                    parameters={"xs": {"type": "array", "defaultValue": ["a", "b", "c"]}},
                    variables={"seen": {"type": "Array", "defaultValue": []}})
    r = run(text, mocks={"Each": {"output": {"n": 1}, "seconds": 0.2}}, wait_scale=1)
    assert sorted(r.variables["seen"]) == ["a:1", "b:1", "c:1"]
    assert r.seconds < 0.5 and next(a for a in r.to_json()["activities"] if a["name"] == "Loop")["iterations"] == 3
    seq = json.loads(text)
    seq["properties"]["activities"][0]["typeProperties"]["isSequential"] = True
    r = run(json.dumps(seq), mocks={"Each": {"output": {"n": 1}, "seconds": 0.2}}, wait_scale=1)
    assert r.variables["seen"] == ["a:1", "b:1", "c:1"] and r.seconds >= 0.55


def test_foreach_fails_when_an_iteration_fails_but_runs_them_all():
    inner = [act("Check", "IfCondition", expression="@equals(item(), 2)", ifTrueActivities=[fail("Two")],
                 ifFalseActivities=[act("Ok", "AppendVariable", variableName="ok", value="@item()")])]
    text = pipeline(act("Loop", "ForEach", items="@createArray(1, 2, 3)", isSequential=True, activities=inner),
                    variables={"ok": {"type": "Array", "defaultValue": []}})
    r = run(text)
    assert r.state == "failed" and r.variables["ok"] == [1, 3]


def test_until_loops_until_its_condition():
    inner = [act("Add", "AppendVariable", variableName="xs", value=1)]
    text = pipeline(act("U", "Until", expression="@equals(length(variables('xs')), 3)", activities=inner),
                    variables={"xs": {"type": "Array", "defaultValue": []}})
    r = run(text)
    assert r.state == "succeeded" and r.variables["xs"] == [1, 1, 1]
    assert r.to_json()["activities"][0]["iterations"] == 3


def test_until_timeout():
    text = pipeline(act("U", "Until", expression="@false", timeout="0.00:00:01", activities=[wait("W", 1)]))
    r = run(text, wait_scale=0.05)
    assert r.state == "failed" and r.to_json()["activities"][0]["error"]["errorCode"] == "LocalUntilTimeout"


def _inner(r, name):
    return next(a for a in r.to_json()["activities"] if a["name"] == name)


def test_until_timeout_cancels_a_long_iteration():
    """#1316: at the Until's timeout, the activity running in its iteration is cancelled and the next one
    doesn't start, as in Data Factory, rather than the loop waiting for the iteration to end."""
    inner = [wait("Long", 30), setvar("After", "v", "x", depends=["Long"])]
    text = pipeline(act("U", "Until", expression="@false", timeout="0.00:00:01", activities=inner),
                    variables={"v": {"type": "String"}})
    started = time.monotonic()
    r = run(text, wait_scale=1)
    assert time.monotonic() - started < 10
    assert r.state == "failed" and _inner(r, "U")["error"]["errorCode"] == "LocalUntilTimeout"
    assert _inner(r, "Long")["state"] == "cancelled" and "error" not in _inner(r, "Long")
    assert _inner(r, "After")["state"] == "cancelled" and _inner(r, "After")["executed_by"] == "not_started"
    assert r.variables.get("v") in (None, "")


def test_an_outer_until_timeout_stops_an_inner_until_and_its_retry_wait():
    retried = act("Flaky", "Fail", message="no", errorCode="E") | {"policy": {"retry": 5, "retryIntervalInSeconds": 30}}
    inner_until = act("Inner", "Until", expression="@false", timeout="0.01:00:00", activities=[retried])
    text = pipeline(act("Outer", "Until", expression="@false", timeout="0.00:00:01", activities=[inner_until]))
    started = time.monotonic()
    r = run(text, wait_scale=1)
    assert time.monotonic() - started < 10
    assert r.state == "failed" and _inner(r, "Outer")["error"]["errorCode"] == "LocalUntilTimeout"
    assert _inner(r, "Inner")["state"] == "cancelled" and _inner(r, "Flaky")["state"] == "cancelled"


def test_an_activitys_own_policy_timeout_inside_an_until_still_fails_it():
    slow = wait("Slow", 30) | {"policy": {"timeout": "0.00:00:01"}}
    text = pipeline(act("U", "Until", expression="@false", timeout="0.01:00:00", activities=[slow]))
    r = run(text, wait_scale=1)
    assert _inner(r, "Slow")["error"]["errorCode"] == "LocalActivityTimeout"
    assert _inner(r, "U")["error"]["errorCode"] == "LocalInnerActivityFailed"


def test_fail_and_filter():
    r = run(pipeline(fail("F", message="@concat('bad ', 'thing')", code="E42"))).to_json()
    assert r["activities"][0]["error"] == {"errorCode": "E42", "message": "bad thing", "failureType": "UserError",
                                           "target": "F", "details": ""}
    r = run(pipeline(act("Fi", "Filter", items="@range(1, 5)", condition="@greater(item(), 3)"))).to_json()
    assert r["activities"][0]["output"] == {"ItemsCount": 5, "FilteredItemsCount": 2, "Value": [4, 5]}


# ORCH-H1 (#1224): a Failed activity's error has the fields Fabric's activity('X').error has.
ERROR_KEYS = {"errorCode", "message", "failureType", "target", "details"}


def errors(r):
    return {a["name"]: a.get("error") for a in r.to_json()["activities"]}


def test_every_kind_of_failure_has_fabrics_error_fields():
    text = pipeline(fail("F"), setvar("Expr", "v", "@int('x')"), act("C", "Copy", source={}, sink={}),
                    act("Loop", "ForEach", items="@range(1, 2)", activities=[fail("Inner")]),
                    act("Slow", "Wait", waitTimeInSeconds=5) | {"policy": {"timeout": "0.00:00:01"}},
                    variables={"v": {"type": "String"}})
    found = errors(run(text, wait_scale=1, mocks={"C": {"status": "Failed"}}))
    for name in ("F", "Expr", "C", "Loop", "Slow"):
        assert set(found[name]) == ERROR_KEYS and found[name]["target"] == name, (name, found[name])
        assert found[name]["failureType"] == "UserError" and found[name]["details"] == ""


def test_a_catch_branch_reads_failure_type_and_target():
    text = pipeline(fail("Load", code="E7"),
                    setvar("Catch", "why", "@concat(activity('Load').error.failureType, ' in ', "
                           "activity('Load').error.target)", depends=["Load"], conditions=["Failed"]),
                    variables={"why": {"type": "String"}})
    r = run(text)
    assert states(r)["Catch"] == "succeeded" and r.variables["why"] == "UserError in Load"


def test_a_mock_sets_its_own_failure_type_and_an_engine_crash_is_a_system_error(monkeypatch):
    mocks = {"C": {"status": "Failed", "error": {"errorCode": "2011", "message": "no", "failureType": "SystemError"}}}
    assert errors(run(pipeline(act("C", "Copy", source={}, sink={})), mocks=mocks))["C"]["failureType"] == "SystemError"
    adapter = default_registry().get("Wait").adapter

    def crash(activity, ctx):
        raise RuntimeError("bug")
    monkeypatch.setattr(adapter, "execute", crash)
    err = errors(run(pipeline(wait("W"))))["W"]
    assert err == {"errorCode": "LocalAdapterError", "message": "RuntimeError: bug", "failureType": "SystemError",
                   "target": "W", "details": ""}


def test_a_secure_output_failure_still_has_its_error_fields():
    a = fail("F") | {"policy": {"secureOutput": True}}
    assert set(errors(run(pipeline(a)))["F"]) == ERROR_KEYS


def test_foreach_refuses_more_items_than_fabric_allows():
    text = pipeline(act("Each", "ForEach", items="@pipeline().parameters.xs", activities=[]),
                    parameters={"xs": {"type": "Array"}})
    r = run(text, parameters={"xs": list(range(100_001))}).to_json()
    assert r["activities"][0]["error"]["errorCode"] == "LocalForEachTooManyItems"
    assert run(text, parameters={"xs": [1, 2]}).state == "succeeded"


def test_expression_error_fails_only_that_activity():
    r = run(pipeline(setvar("S", "v", "@nosuch()"), wait("Catch", depends=["S"], conditions=["Failed"]),
                     variables={"v": {"type": "String"}})).to_json()
    s = r["activities"][0]
    assert s["state"] == "failed" and s["error"]["errorCode"] == "LocalExpressionError" and r["state"] == "succeeded"


# ---- mocks, policy, cancel, timeouts

def test_mock_failure_and_retries():
    a = act("C", "Copy", source={}, sink={}) | {"policy": {"retry": 2, "retryIntervalInSeconds": 1}}
    r = run(pipeline(a), mocks={"C": {"status": "Failed", "error": {"errorCode": "2200", "message": "no"}}})
    row = r.to_json()["activities"][0]
    assert row["state"] == "failed" and row["attempts"] == 3
    assert row["error"] == {"errorCode": "2200", "message": "no", "failureType": "UserError", "target": "C", "details": ""}
    assert row["executed_by"] == "mock"


def test_policy_timeout():
    a = act("C", "Copy", source={}, sink={}) | {"policy": {"timeout": "0.00:00:01"}}
    r = run(pipeline(a), mocks={"C": {"seconds": 100}}, wait_scale=0.05)
    assert r.to_json()["activities"][0]["error"]["errorCode"] == "LocalActivityTimeout" and r.seconds < 1.5


def test_secure_output_is_hidden():
    a = act("C", "Copy", source={}, sink={}) | {"policy": {"secureOutput": True}}
    assert run(pipeline(a), mocks={"C": {"output": {"token": "x"}}}).to_json()["activities"][0]["output"] == "(hidden)"


def test_cancel_stops_waits_and_pending_activities():
    r = PipelineRun(loads(pipeline(wait("Long", 30), wait("After", depends=["Long"]))), wait_scale=1)
    t = threading.Thread(target=r.run)
    t.start()
    time.sleep(0.3)
    r.cancel()
    t.join(5)
    assert not t.is_alive() and r.state == "cancelled"
    assert states(r) == {"Long": "cancelled", "After": "cancelled"} and r.seconds < 2


def test_run_timeout():
    r = run(pipeline(wait("Long", 30)), wait_scale=1, timeout_seconds=0.3)
    assert r.state == "failed" and r.stop_reason == "timed_out"


def test_activity_run_ids_are_stable_for_a_run():
    text = pipeline(wait("A"), act("L", "ForEach", ["A"], items="@createArray(1, 2)", activities=[wait("W")]))
    ids = lambda r: sorted((a["name"], a["label"], a["id"]) for a in r.to_json()["activities"])  # noqa: E731
    rid = "11111111-2222-3333-4444-555555555555"
    assert ids(run(text, run_id=rid)) == ids(run(text, run_id=rid)) != ids(run(text))


def test_secure_parameters_are_hidden():
    text = pipeline(wait("A"), parameters={"key": {"type": "securestring", "defaultValue": "s3cret"}})
    assert run(text).to_json()["parameters"] == {"key": "(hidden)"}


# ---- refusals: nothing that needs Fabric runs

@pytest.mark.parametrize("activity,code", [
    (act("C", "Copy", source={}, sink={}), "not_local"),
    (act("N", "TridentNotebook", notebookId="x"), "notebook_needs_engine"),
    (act("D", "RefreshDataflow", dataflowId="d"), "not_local"),
    (act("X", "ContosoPagerNotify"), "not_local"),
    (act("E", "ExecutePipeline", pipeline={"referenceName": "child"}), "child_definition_missing"),
    (act("W", "Wait"), "missing_property"),
    (act("Bad", "Wait", ["Nope"], waitTimeInSeconds=1), "missing_dependency"),
])
def test_refused_before_anything_runs(activity, code):
    r = PipelineRun(loads(pipeline(wait("First"), activity)))
    with pytest.raises(RunRefused) as exc:
        r.run()
    assert code in [p["code"] for p in exc.value.problems]
    assert r.records == [] and r.state == "refused"


def test_nested_activity_needing_fabric_refuses_the_run():
    text = pipeline(act("L", "ForEach", items="@createArray(1)", activities=[act("N", "TridentNotebook", notebookId="x")]))
    with pytest.raises(RunRefused, match="N runs notebook x"):
        run(text)
    assert run(text, mocks={"N": {}}).state == "succeeded"


def test_parameter_and_mock_problems():
    text = pipeline(wait("A"), parameters={"need": {"type": "int"}})
    codes = lambda **kw: [p["code"] for p in PipelineRun(loads(text), **kw).check()]  # noqa: E731
    assert codes() == ["missing_parameter"]
    assert codes(parameters={"need": "x"}) == ["bad_parameter"]
    assert codes(parameters={"need": "4", "other": 1}) == ["unknown_parameter"]
    assert codes(parameters={"need": 4}, mocks={"Z": {}}) == ["unknown_mock"]
    assert codes(parameters={"need": 4}, mocks={"A": {"status": "Maybe"}}) == ["bad_mock"]
    assert codes(parameters={"need": "4"}) == []


# ---- child pipelines

CHILD = pipeline(setvar("Set", "v", "@concat('hi ', pipeline().parameters.who)"),
                 act("Ret", "SetVariable", ["Set"], variableName="pipelineReturnValue", setSystemVariable=True,
                     value=[{"key": "greeting", "value": {"type": "Expression", "content": "@variables('v')"}}]),
                 parameters={"who": {"type": "string"}}, variables={"v": {"type": "String"}}, name="child")


def test_child_pipeline_runs_locally_with_parameters():
    text = pipeline(act("Call", "ExecutePipeline", pipeline={"referenceName": "child", "type": "PipelineReference"},
                        parameters={"who": "@pipeline().parameters.name"}, waitOnCompletion=True),
                    setvar("Use", "g", "@activity('Call').output.pipelineReturnValue.greeting", ["Call"]),
                    parameters={"name": {"type": "string", "defaultValue": "Chris"}},
                    variables={"g": {"type": "String"}})
    r = run(text, children={"child": loads(CHILD)})
    assert r.state == "succeeded" and r.variables["g"] == "hi Chris"
    call = next(a for a in r.to_json()["activities"] if a["name"] == "Call")
    assert call["child_run"]["state"] == "succeeded" and call["output"]["pipelineRunId"] == call["child_run"]["run_id"]


def test_invoke_pipeline_by_id_and_child_failure():
    failing = pipeline(fail("Nope"), name="bad")
    text = pipeline(act("Inv", "InvokePipeline", pipelineId="abc", workspaceId="w"))
    r = run(text, children={"abc": loads(failing)})
    row = r.to_json()["activities"][0]
    assert r.state == "failed" and row["error"]["errorCode"] == "LocalChildFailed"


def test_wait_on_completion_false_moves_on_without_the_child():
    # Plan P0 (B8): Fabric starts the child and the activity succeeds; the next step doesn't wait for it.
    slow = pipeline(wait("Slow", 2), name="slow")
    text = pipeline(act("Call", "ExecutePipeline", pipeline={"referenceName": "slow"}, waitOnCompletion=False),
                    setvar("Next", "v", "go", ["Call"]), variables={"v": {"type": "String"}})
    r = run(text, children={"slow": loads(slow)}, wait_scale=1)
    rows = {a["name"]: a for a in r.to_json()["activities"] if not a["label"]}
    call, nxt = rows["Call"], rows["Next"]
    assert r.state == "succeeded" and call["output"] == {"pipelineRunId": call["child_run"]["run_id"]}
    assert call["wait_on_completion"] is False and call["seconds"] < 1.5
    # The run's record waits for the child, so the child's whole run is in it.
    assert call["child_run"]["state"] == "succeeded" and nxt["ended"] < call["child_run"]["ended"]


def test_a_child_nothing_waits_on_doesnt_fail_the_run():
    failing = pipeline(fail("Nope"), name="bad")
    r = run(pipeline(act("Inv", "InvokePipeline", pipelineId="abc", workspaceId="w", waitOnCompletion=False)),
            children={"abc": loads(failing)})
    row = r.to_json()["activities"][0]
    assert r.state == "succeeded" and row["state"] == "succeeded" and row["child_run"]["state"] == "failed"


def test_cancel_stops_a_child_nothing_waits_on():
    slow = pipeline(wait("Slow", 60), name="slow")
    r = PipelineRun(loads(pipeline(act("Call", "ExecutePipeline", pipeline={"referenceName": "slow"},
                                       waitOnCompletion=False))), children={"slow": loads(slow)})
    t = threading.Thread(target=r.run)
    t.start()
    end = time.monotonic() + 10
    while not (r.records and r.records[0]["state"] == "succeeded") and time.monotonic() < end:
        time.sleep(0.02)
    r.cancel()
    t.join(10)
    assert not t.is_alive() and r.state == "cancelled"
    assert r.to_json()["activities"][0]["child_run"]["state"] == "cancelled"


def test_child_needing_fabric_refuses_the_parent():
    child = pipeline(act("C", "Copy", source={}, sink={}), name="c")
    with pytest.raises(RunRefused, match="In child pipeline c"):
        run(pipeline(act("E", "ExecutePipeline", pipeline={"referenceName": "c"})), children={"c": loads(child)})


# ---- the reference pipeline

def test_reference_pipeline_runs_with_mocks():
    r = PipelineRun(load(str(REFERENCE)), mocks=MOCKS, wait_scale=0).run().to_json()
    assert r["state"] == "succeeded"
    # The ForEach runs two iterations at once, so appends land in the order iterations finish.
    assert sorted(r["variables"]["loaded"]) == ["AMER", "APAC", "EMEA"] and r["variables"]["status"] == "valid"
    assert r["return_value"]["rows"] == 120 and sorted(r["return_value"]["regions"]) == ["AMER", "APAC", "EMEA"]
    assert r["options"]["mocked"] == ["Ingest orders", "Transform"] and r["verified_against_fabric"] is False
    by = {a["name"]: a["executed_by"] for a in r["activities"] if not a["label"]}
    assert by["Ingest orders"] == "mock" and by["Checkpoint"] == "adapter" and by["On failure"] == "not_started"


def test_reference_pipeline_validation_failure():
    mocks = MOCKS | {"Ingest orders": {"output": {"rowsCopied": 0}}}
    r = PipelineRun(load(str(REFERENCE)), mocks=mocks, wait_scale=0).run()
    assert r.state == "failed" and r.variables["status"] == "failed"
    err = next(a for a in r.to_json()["activities"] if a["name"] == "Too few rows")["error"]
    assert err == {"errorCode": "ROWS_LOW", "message": "Only 0 rows", "failureType": "UserError", "target": "Too few rows",
                   "details": ""}


def test_reference_pipeline_refused_without_mocks():
    with pytest.raises(RunRefused) as exc:
        PipelineRun(load(str(REFERENCE))).run()
    assert sorted(p["activity"] for p in exc.value.problems) == ["Ingest orders", "Transform"]


# ---- run records

def test_store_saves_lists_and_trims(tmp_path):
    store = RunStore(tmp_path)
    for i in range(KEEP + 3):
        store.save({"run_id": f"{i:08x}-0000-0000-0000-000000000000", "state": "succeeded"})
    store.trim()
    assert len(list(tmp_path.glob("*.json"))) == KEEP and len(store.list(500)) == KEEP
    with pytest.raises(ValueError):
        store.save({"run_id": "../escape"})


def test_store_marks_runs_left_going_as_interrupted(tmp_path):
    RunStore(tmp_path).save({"run_id": "aaaa", "state": "running",
                             "activities": [{"name": "A", "state": "running"}, {"name": "B", "state": "succeeded"}]})
    record = RunStore(tmp_path).get("aaaa")
    assert record["state"] == "interrupted" and record["stop_reason"] == "app_stopped"
    assert [a["state"] for a in record["activities"]] == ["interrupted", "succeeded"]


def test_a_save_or_read_refused_for_a_moment_is_tried_again(tmp_path, monkeypatch):
    """Windows: while a reader has the record open, swapping the new copy in fails with WinError 5,
    and opening it just as a save swaps it fails the same way. Neither may lose the record."""
    import os
    from pathlib import Path as P
    store = RunStore(tmp_path)
    store.save({"run_id": "aaaa", "state": "running"})
    real_replace, real_read = os.replace, P.read_text
    refused = {"replace": 2, "read": 2}

    def replace(src, dst):
        if refused["replace"]:
            refused["replace"] -= 1
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    def read_text(self, *a, **kw):
        if self.suffix == ".json" and refused["read"]:
            refused["read"] -= 1
            raise PermissionError(5, "Access is denied")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(os, "replace", replace)
    store.save({"run_id": "aaaa", "state": "succeeded"})
    monkeypatch.setattr(P, "read_text", read_text)
    assert store.get("aaaa")["state"] == "succeeded"
    assert refused == {"replace": 0, "read": 0}
    assert [p.name for p in tmp_path.iterdir()] == ["aaaa.json"]  # no temporary file left


class Svc(PipelineMixin, PipelineRunMixin):
    def __init__(self, root):
        self.root = root


def finish(svc, run_id, limit=30.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if svc.pipeline_run_status(run_id)["state"] not in ("pending", "running"):
            return svc.pipeline_run_status(run_id)
        time.sleep(0.02)
    raise AssertionError("run didn't finish")


def saved(folder, run_id, limit=30.0):
    """The run's record on disk once it's final. Status reads the live run, which turns final
    just before the background thread's last save, so the file can still say running."""
    path = folder / "pipeline_runs" / f"{run_id}.json"
    end = time.monotonic() + limit
    while time.monotonic() < end:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
        if record.get("state") not in (None, "pending", "running"):
            return record
        time.sleep(0.02)
    raise AssertionError("run record wasn't saved as finished")


def test_service_start_status_runs_and_record_on_disk(tmp_path):
    svc = Svc(tmp_path)
    assert svc.pipeline_run_check(path=str(REFERENCE))["ok"] is False
    assert svc.pipeline_run_check(path=str(REFERENCE), mocks=MOCKS)["ok"] is True
    started = svc.pipeline_run_start(path=str(REFERENCE), mocks=MOCKS, wait_scale=0)
    assert started["ok"] is True
    record = finish(svc, started["run_id"])
    assert record["state"] == "succeeded"
    on_disk = saved(tmp_path, started["run_id"])
    assert on_disk["state"] == "succeeded" and on_disk["return_value"]["rows"] == 120
    lean = svc.pipeline_run_status(started["run_id"], values=False)
    assert "variables" not in lean and all("output" not in a for a in lean["activities"])
    assert svc.pipeline_runs()["runs"][0]["run_id"] == started["run_id"]
    # A new service (the app restarted) still reads it.
    assert Svc(tmp_path).pipeline_run_status(started["run_id"])["state"] == "succeeded"


def test_service_refuses_and_limits_concurrent_runs(tmp_path):
    svc = Svc(tmp_path)
    refused = svc.pipeline_run_start(path=str(REFERENCE))
    assert refused["ok"] is False and {p["code"] for p in refused["problems"]} == {"not_local", "notebook_needs_engine"}
    slow = pipeline(wait("W", 30))
    ids = [svc.pipeline_run_start(text=slow)["run_id"] for _ in range(2)]
    busy = svc.pipeline_run_start(text=slow)
    assert busy["ok"] is False and busy["problems"][0]["code"] == "busy"
    for rid in ids:
        assert svc.pipeline_run_cancel(rid)["cancelled"] is True
    assert all(finish(svc, rid)["state"] == "cancelled" for rid in ids)
    assert svc.pipeline_run_cancel(ids[0])["cancelled"] is False
    with pytest.raises(KeyError):
        svc.pipeline_run_status("00000000-0000-0000-0000-000000000000")


# ---- wiring

def test_methods_and_audit():
    for name in ("pipeline_run_check", "pipeline_run_start", "pipeline_run_status", "pipeline_run_cancel",
                 "pipeline_runs"):
        assert name in server.METHODS
    assert {"pipeline_run_check", "pipeline_run_status", "pipeline_runs"} <= audit.READ_ONLY
    assert not {"pipeline_run_start", "pipeline_run_cancel"} & audit.READ_ONLY


def test_capabilities(tmp_path):
    reg = capability_registry()
    start = reg.get("fabric_desktop_start_pipeline_run")
    assert start.permission == "EXECUTE_LOCAL_CODE" and start.approval == "always" and not start.read_only
    assert start.locations == ("this_pc",)
    cancel = reg.get("fabric_desktop_cancel_pipeline_run")
    assert cancel.approval == "always" and not cancel.read_only
    ctx = CallContext(Caller("cl_test", "test"), timeout=5)
    svc = Svc(tmp_path)
    text = REFERENCE.read_text(encoding="utf-8")
    checked = reg.invoke(svc, "fabric_desktop_check_pipeline_run", {"definition": text, "mocks": MOCKS}, ctx)
    assert checked["ok"] is True
    rid = svc.pipeline_run_start(text=text, mocks=MOCKS, wait_scale=0)["run_id"]
    finish(svc, rid)
    got = reg.invoke(svc, "fabric_desktop_get_pipeline_run", {"id": rid}, ctx)
    assert got["state"] == "succeeded" and "variables" not in got
    listed = reg.invoke(svc, "fabric_desktop_list_pipeline_runs", {}, ctx)
    assert listed["runs"][0]["run_id"] == rid


def test_external_tools_mock_notebook_lookup_and_get_metadata(tmp_path):
    reg = capability_registry()
    ctx = CallContext(Caller("cl_test", "test"), timeout=5)
    svc = Svc(tmp_path)
    ls = {"name": "LH", "properties": {"type": "Lakehouse", "typeProperties": {"workspaceId": "w", "artifactId": "a"}}}
    data = {"type": "LakehouseTable", "linkedService": ls, "typeProperties": {"table": "t"}}
    text = pipeline(act("NB", "TridentNotebook", notebookId="n1", workspaceId="w"),
                    act("LK", "Lookup", datasetSettings=data, source={}),
                    act("GM", "GetMetadata", datasetSettings=data, fieldList=["columnCount"]))
    checked = reg.invoke(svc, "fabric_desktop_check_pipeline_run", {"definition": text}, ctx)
    assert [p["activity"] for p in checked["problems"]] == ["NB", "LK", "GM"]
    # The external tool can't pass the CLI's --notebook or --read-lakehouse, so it is told to mock.
    assert all("Give a mock for it" in p["message"] and "--" not in p["message"] for p in checked["problems"])
    mocks = {"NB": {"output": {"exitValue": "ok"}}, "LK": {"output": {"firstRow": {"n": 1}}},
             "GM": {"output": {"columnCount": 3}}}
    checked = reg.invoke(svc, "fabric_desktop_check_pipeline_run", {"definition": text, "mocks": mocks}, ctx)
    assert checked["ok"] is True and sorted(checked["mocked"]) == ["GM", "LK", "NB"]


def test_registry_marks_control_flow_local():
    reg = default_registry()
    for type_ in ("SetVariable", "AppendVariable", "IfCondition", "Switch", "ForEach", "Until", "Wait", "Fail",
                  "Filter", "ExecutePipeline", "InvokePipeline"):
        entry = reg.get(type_)
        assert entry.classification == "local" and entry.adapter.can_execute and entry.evidence == "unit_tests"
    assert reg.get("Copy").classification == "static" and reg.get("TridentNotebook").classification == "local"


def test_cli_run(capsys, tmp_path):
    mocks = tmp_path / "mocks.json"
    mocks.write_text(json.dumps(MOCKS), encoding="utf-8")
    with pytest.raises(SystemExit) as done:
        cli_main(["pipeline", "run", str(REFERENCE), "--mocks", str(mocks), "--wait-scale", "0"])
    assert done.value.code == 0 and "succeeded  Record" in capsys.readouterr().out
    with pytest.raises(SystemExit) as done:
        cli_main(["pipeline", "run", str(REFERENCE), "--mocks", str(mocks), "--wait-scale", "0", "--param", "min_rows=500"])
    assert done.value.code == 1
    with pytest.raises(SystemExit) as done:
        cli_main(["pipeline", "run", str(REFERENCE)])
    assert done.value.code == 2 and "refused: not_local [Ingest orders]" in capsys.readouterr().out


def test_notebook_cleanup_runs_before_the_run_reports_its_end():
    # The flaky cancel test after #319: the run said "cancelled" while its folder was still being deleted.
    seen = []

    class Notebooks:
        def check(self, a):
            return []

        def run(self, a, parameters, cancelled, deadline):
            return {"result": {"runStatus": "Succeeded", "exitValue": None}}

        def notes(self):
            return []

        def finish(self):
            seen.append(r.state)

    r = PipelineRun(loads(pipeline(act("N", "TridentNotebook", notebookId="x"))), wait_scale=0, notebooks=Notebooks())
    r.run()
    assert seen == ["running"] and r.state == "succeeded"


def test_pipelines_in_open_repositories_are_listed_with_their_last_run(tmp_path):
    import shutil
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "pl_orders_daily.DataPipeline", repo / "pipelines" / "pl_orders_daily.DataPipeline")
    broken = repo / "deep" / "a" / "b" / "pl_broken.DataPipeline"
    broken.mkdir(parents=True)
    (repo / ".git" / "x.DataPipeline").mkdir(parents=True)        # never looked in
    (repo / "a" / "b" / "c" / "d" / "e" / "far.DataPipeline").mkdir(parents=True)   # too deep

    class Repos(Svc):
        def git_repos(self):
            return {"repos": [{"path": str(repo), "name": "platform", "exists": True},
                              {"path": str(tmp_path / "gone"), "name": "gone", "exists": False}]}

    svc = Repos(tmp_path / "data")
    out = svc.pipeline_list()
    assert out["truncated"] is False
    rows = {r["relative"]: r for r in out["pipelines"]}
    assert set(rows) == {"pipelines/pl_orders_daily.DataPipeline", "deep/a/b/pl_broken.DataPipeline"}
    ok = rows["pipelines/pl_orders_daily.DataPipeline"]
    assert ok["name"] == "pl_orders_daily" and ok["repo"] == "platform" and ok["activity_count"] > 0
    assert ok["last_run"] is None
    assert rows["deep/a/b/pl_broken.DataPipeline"]["problem"].startswith("pl_broken.DataPipeline has no pipeline-content.json")
    record = svc.pipeline_run_start(text=json.dumps({"name": "pl_orders_daily", "properties": {"activities": [
        {"name": "W", "type": "Wait", "typeProperties": {"waitTimeInSeconds": 0}}]}}))
    finish(svc, record["run_id"])
    assert svc.pipeline_list()["pipelines"][1]["last_run"]["state"] == "succeeded"
    assert "pipeline_list" in server.METHODS and "pipeline_list" in audit.READ_ONLY
    # Without the Git service (an external tool's service), nothing is listed.
    assert Svc(tmp_path / "other").pipeline_list() == {"pipelines": [], "truncated": False}
