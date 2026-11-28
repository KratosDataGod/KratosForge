"""Mutation batch 261: excellence/inspect_items.py (docs/quality/mutation-testing.md). The survivors were
the exact answers for a definition that wasn't read, a notebook without cells, a pipeline without
activities and an inspection that breaks; a workspace id inside a list in an activity's settings; the
caps (20 places a rule, 50 names a list); and the arithmetic of a timeout: seconds, minutes and days
each count, a timeout of one second is set, and a day or more is never under the 12-hour default."""

from __future__ import annotations

import pytest

from fabric_desktop.excellence import inspect_items

WSID = "11111111-2222-3333-4444-555555555555"
GOOD_POLICY = {"retry": 2, "timeout": "0.01:00:00"}


def activity(name: str, *, kind: str = "TridentNotebook", tp: dict | None = None, policy: dict | None = None,
             inactive: bool = False) -> dict:
    return {"name": name, "type": kind, "inactive": inactive, "type_properties": tp or {"notebookId": "x"},
            "depends_on": [], "policy": dict(GOOD_POLICY if policy is None else policy)}


def pipeline(acts: object) -> dict:
    return {"type": "DataPipeline", "definition": "read", "activities": acts}


def test_a_definition_not_read_is_exactly_read_false():
    assert inspect_items.inspect({"type": "Notebook", "definition": "not_read"}) == {"read": False}
    assert inspect_items.inspect({"type": "DataPipeline"}) == {"read": False}


def test_a_notebook_without_cells_and_a_pipeline_without_activities_were_read_but_not_inspectable():
    assert inspect_items.inspect({"type": "Notebook", "definition": "read"}) == {
        "read": True, "error": "The notebook's cells weren't available."}
    assert inspect_items.inspect(pipeline(None)) == {"read": True, "error": "The pipeline's activities weren't available."}


def test_an_inspection_that_breaks_is_read_with_the_error_type_only(monkeypatch):
    def boom(w):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(inspect_items, "pipeline", boom)
    assert inspect_items.inspect(pipeline([])) == {
        "read": True, "error": "The definition couldn't be inspected (RuntimeError)."}


def test_a_workspace_id_inside_a_list_of_settings_is_found():
    nested = {"steps": [{"name": "a"}, {"settings": {"workspaceId": WSID}}]}
    got = inspect_items.inspect(pipeline([activity("Load", tp=nested), activity("Clean")]))
    assert got["fixed_workspace"] == ["Load"] and got["fixed_workspace_count"] == 1
    assert inspect_items._names_fixed_workspace([[{"WorkspaceId": WSID}]]) is True
    assert not inspect_items._names_fixed_workspace([{"workspaceId": "@pipeline().parameters.ws"}])


def test_a_rule_keeps_twenty_places_and_counts_every_match():
    w = {"type": "Notebook", "definition": "read", "cells": ["%pip install requests\n"] * 25,
         "runs": {"read": True, "scheduled": 0}}
    nb009 = inspect_items.inspect(w)["rules"]["NB009"]
    assert nb009["count"] == 25 and len(nb009["where"]) == 20
    assert nb009["where"][-1] == {"cell": 20, "line": 1}


def test_a_list_keeps_fifty_names_and_its_count_has_them_all():
    acts = [activity(f"a{i:02d}", inactive=True) for i in range(51)]
    acts += [activity(f"r{i:02d}", policy={"retry": 0, "timeout": "1.00:00:00"}) for i in range(51)]
    got = inspect_items.inspect(pipeline(acts))
    assert got["inactive_count"] == 51 and len(got["inactive"]) == 50 and got["inactive"][-1] == "a49"
    assert got["no_retry_count"] == 51 and len(got["no_retry"]) == 50
    assert got["no_timeout_count"] == 51 and len(got["no_timeout"]) == 50


@pytest.mark.parametrize("value, expect", [
    ("0.00:00:01", True),  # one second is a timeout someone set
    ("0.00:00:30", True),
    ("0.00:30:00", True),
    ("0.11:59:59", True),
    ("0.12:00:00", False),  # the default itself
    ("1.00:00:01", False),  # a day and a second: well over the default
    ("2.00:00:00", False),
    ("0.00:00:00", False),
    ("00:45:00", True),  # no days part
    ("0.00:60:00", None),
    ("0.00:00:60", None),
    ("soon", None),
])
def test_a_timeout_is_set_when_it_is_under_twelve_hours(value, expect):
    assert inspect_items._timeout_set({"timeout": value}) is expect


@pytest.mark.parametrize("policy, expect", [
    ({}, False), ({"retry": 0}, False), ({"retry": "0"}, False), ({"retry": 1}, True), ({"retry": " 3 "}, True),
    ({"retry": "@pipeline().parameters.r"}, True), ({"retry": {"type": "Expression", "value": "@x"}}, True),
    ({"retry": True}, None), ({"retry": "two"}, None),
])
def test_a_retry_is_set_when_it_is_above_zero_or_an_expression(policy, expect):
    assert inspect_items._retry_set(policy) is expect
