"""Mutation-testing gaps in orchestration/validate.py (docs/quality/mutation-testing.md, batch 32).

Each test pins a behaviour that a surviving mutant changed while every existing test still passed.
Pure functions over pipeline JSON: no network, no files, no Fabric.
"""

from __future__ import annotations

import json

from fabric_desktop.orchestration import loads, validate
from fabric_desktop.orchestration.definition import build
from fabric_desktop.orchestration.validate import Diagnostic, own_json, summary


def act(name, typ="Wait", depends=(), **extra):
    a = {"name": name, "type": typ,
         "dependsOn": [{"activity": d, "dependencyConditions": ["Succeeded"]} for d in depends],
         "typeProperties": {"waitTimeInSeconds": 1}}
    a.update(extra)
    return a


def content(activities, parameters=None, variables=None):
    props = {"activities": activities}
    if parameters is not None:
        props["parameters"] = parameters
    if variables is not None:
        props["variables"] = variables
    return {"name": "p", "properties": props}


def run(activities, **kw):
    return validate(loads(json.dumps(content(activities, **kw))))


def by_code(diags, code):
    return [d for d in diags if d.code == code]


# ---- duplicate keys, and a Pipeline passed in directly

def test_duplicate_keys_are_listed_up_to_ten():
    keys = ", ".join(f'"k{i}": 1, "k{i}": 2' for i in range(11))
    text = '{"name": "p", "properties": {"activities": [], ' + keys + "}}"
    (d,) = by_code(validate(loads(text)), "duplicate_json_key")
    assert "'k9'" in d.message and "'k10'" not in d.message
    assert d.severity == "warning" and d.path == "$"


def test_no_duplicate_warning_without_duplicates():
    assert by_code(run([act("A")]), "duplicate_json_key") == []


def test_a_built_pipeline_can_be_validated_directly():
    p = build(content([act("A"), act("A")]))
    assert [d.code for d in validate(p)] == ["duplicate_activity_name"]


# ---- severity order

def test_errors_come_before_warnings_and_warnings_before_info():
    # The duplicate-key warning is found before the undeclared-variable error, and the inactive
    # activity's info is found last; the result is still ordered by severity.
    text = ('{"name": "p", "properties": {"description": "a", "description": "b", "activities": ['
            '{"name": "S", "type": "SetVariable", "state": "Inactive", '
            '"typeProperties": {"variableName": "nope", "value": "x"}}]}}')
    diags = validate(loads(text))
    assert [d.severity for d in diags] == ["error", "warning", "info"]
    assert [d.code for d in diags] == ["undeclared_variable", "duplicate_json_key", "inactive_activity"]


# ---- inactive activities

def test_an_inactive_activity_says_how_it_is_marked():
    (d,) = by_code(run([act("A", state="Inactive")]), "inactive_activity")
    assert d.message.endswith("marks it Succeeded.")
    (d,) = by_code(run([act("A", state="Inactive", onInactiveMarkAs="Failed")]), "inactive_activity")
    assert d.message.endswith("marks it Failed.")
    assert d.path == "$.properties.activities[0].state" and d.activity == "A"


# ---- missing type

def test_missing_type_names_the_activity():
    (d,) = by_code(run([{"name": "A"}]), "missing_type")
    assert d.message == "A has no type." and d.activity == "A"
    (d,) = by_code(run([{}]), "missing_type")
    assert d.message == "An activity has no type." and d.activity is None


# ---- cycles

def test_a_cycle_is_reported_on_its_first_activity():
    diags = run([act("X"), act("A", depends=["B"]), act("B", depends=["A"])])
    (d,) = by_code(diags, "dependency_cycle")
    assert d.message.endswith("B -> A -> B.")
    assert d.activity == "B"
    assert d.path == "$.properties.activities[2].dependsOn"


def test_a_cycle_reached_through_another_activity_is_found():
    (d,) = by_code(run([act("A", depends=["B"]), act("B", depends=["C"]), act("C", depends=["B"])]),
                   "dependency_cycle")
    assert d.message.endswith("C -> B -> C.") and d.activity == "C"


# ---- upstream with a dependency on an activity that doesn't exist

def test_reading_output_past_a_missing_dependency_does_not_fail():
    reads_a = {"type": "Expression", "value": "@activity('A').output"}
    diags = run([act("A"), act("B", depends=["Ghost"], typeProperties={"waitTimeInSeconds": reads_a})])
    assert [d.code for d in by_code(diags, "missing_dependency")] == ["missing_dependency"]
    (d,) = by_code(diags, "activity_not_upstream")
    assert d.activity == "B"


# ---- Set variable

def _set(var, system=None):
    tp = {"variableName": var, "value": "x"}
    if system is not None:
        tp["setSystemVariable"] = system
    return {"name": "S", "type": "SetVariable", "typeProperties": tp}


def test_a_variable_name_that_is_not_a_string_is_missing():
    assert [d.code for d in run([_set(123)], variables={"v": {"type": "String"}})] == ["missing_variable_name"]
    assert [d.code for d in run([_set("")], variables={"v": {"type": "String"}})] == ["missing_variable_name"]


def test_the_system_return_value_needs_set_system_variable():
    assert run([_set("pipelineReturnValue", True)]) == []
    assert [d.code for d in run([_set("pipelineReturnValue", False)])] == ["undeclared_variable"]
    assert [d.code for d in run([_set("pipelineReturnValue")])] == ["undeclared_variable"]
    assert [d.code for d in run([_set("other", True)])] == ["undeclared_variable"]
    assert run([_set("v")], variables={"v": {"type": "String"}}) == []


# ---- the activity's own JSON

def test_own_json_keeps_everything_but_child_activities():
    raw = {"name": "Sw", "type": "Switch", "policy": {"timeout": "@pipeline().parameters.t"},
           "typeProperties": {"on": "@item()", "cases": [{"value": "a", "activities": [act("Kid")]}, "junk"],
                              "defaultActivities": [act("Def")]}}
    p = build(content([raw]))
    own = own_json(p.activities[0])
    assert own["policy"] == raw["policy"] and own["name"] == "Sw"
    assert own["typeProperties"] == {"on": "@item()", "cases": [{"value": "a"}, "junk"]}


def test_expressions_outside_type_properties_are_checked():
    a = act("A", policy={"timeout": {"type": "Expression", "value": "@pipeline().parameters.missing"}})
    (d,) = by_code(run([a]), "undeclared_parameter")
    assert d.path.startswith("$.properties.activities[0].policy")


# ---- item()

def _item_user(name, depends=()):
    return act(name, depends=depends, typeProperties={"waitTimeInSeconds": {"type": "Expression", "value": "@item()"}})


def test_item_on_a_foreach_itself_is_outside_its_loop():
    fe = {"name": "FE", "type": "ForEach",
          "typeProperties": {"items": {"type": "Expression", "value": "@item()"}, "activities": []}}
    (d,) = by_code(run([fe]), "item_outside_foreach")
    assert d.activity == "FE"


def test_item_inside_an_if_needs_a_foreach_around_it():
    in_if = {"name": "If", "type": "IfCondition",
             "typeProperties": {"expression": {"type": "Expression", "value": "@true"},
                                "ifTrueActivities": [_item_user("K")]}}
    (d,) = by_code(run([in_if]), "item_outside_foreach")
    assert d.activity == "K"
    fe = {"name": "FE", "type": "ForEach",
          "typeProperties": {"items": {"type": "Expression", "value": "@createArray(1)"}, "activities": [in_if]}}
    assert by_code(run([fe]), "item_outside_foreach") == []


# ---- summary

def test_summary_counts_each_severity():
    d = [Diagnostic("a", "error", "", "$"), Diagnostic("b", "warning", "", "$"), Diagnostic("c", "warning", "", "$"),
         Diagnostic("d", "debug", "", "$")]
    assert summary(d) == {"error": 1, "warning": 2, "info": 0, "debug": 1}
    assert summary([]) == {"error": 0, "warning": 0, "info": 0}
