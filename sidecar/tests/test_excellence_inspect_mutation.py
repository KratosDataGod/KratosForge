"""What a model, notebook or pipeline definition shows (excellence/inspect_models.py and
excellence/inspect_items.py), mutation batch 354: the limits on names and places as numbers and at
their edges, the answers for a definition that wasn't read or isn't shaped right, and a fixed
workspace found inside a list."""

from __future__ import annotations

from fabric_desktop.excellence import inspect_items, inspect_models

GUID = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"


# ---- models


def rel(i: int, **more) -> dict:
    return {"fromTable": f"F{i:03d}", "fromColumn": "k", "toTable": "D", "toColumn": "k",
            "crossFilteringBehavior": "bothDirections", **more}


def test_a_definition_without_a_model_is_read_and_says_so():
    assert inspect_models.inspect({}) == {"read": True, "error": "The definition has no model."}


def test_names_are_kept_to_50_and_each_to_256_characters():
    assert (inspect_models.MAX_NAMES, inspect_models.MAX_NAME) == (50, 256)
    out = inspect_models.inspect({"model": {"relationships": [rel(i) for i in range(60)]}})
    assert out["bidirectional_count"] == 60 and len(out["bidirectional"]) == 50
    assert inspect_models._cut("a" * 256) == "a" * 256
    assert inspect_models._cut("a" * 257) == "a" * 255 + "…"


def test_a_rule_keeps_50_of_its_objects_and_counts_them_all():
    tables = [{"name": f"F{i:03d}", "columns": [{"name": "k", "dataType": "string"}]} for i in range(55)]
    tables.append({"name": "D", "columns": [{"name": "k", "dataType": "string", "isHidden": True}]})
    out = inspect_models.inspect({"model": {"tables": tables, "relationships": [rel(i) for i in range(55)]}})
    sm002 = out["rules"]["SM002"]
    assert sm002["count"] == 55 and len(sm002["objects"]) == 50


def test_a_relationship_missing_either_table_is_left_out():
    out = inspect_models.inspect({"model": {"relationships": [{"fromTable": "F", "fromColumn": "k"},
                                                              {"toTable": "D", "toColumn": "k"}]}})
    assert "error" not in out
    assert out["relationships"] == 0 and out["types_unknown"] == [] and out["columns_unknown"] == []


# ---- notebooks and pipelines


def test_a_workload_whose_definition_wasnt_read_says_so():
    assert inspect_items.inspect({"type": "Notebook", "definition": "unread"}) == {"read": False}


def test_a_notebook_or_pipeline_without_its_parts_says_which():
    assert inspect_items.inspect({"type": "Notebook", "definition": "read"}) == {
        "read": True, "error": "The notebook's cells weren't available."}
    assert inspect_items.inspect({"type": "DataPipeline", "definition": "read"}) == {
        "read": True, "error": "The pipeline's activities weren't available."}


def test_a_definition_that_cant_be_inspected_is_an_error_not_a_crash():
    got = inspect_items.inspect({"type": "DataPipeline", "definition": "read", "activities": ["not an activity"]})
    assert got == {"read": True, "error": "The definition couldn't be inspected (AttributeError)."}


def test_a_rule_keeps_20_places_and_counts_them_all():
    assert inspect_items.MAX_PLACES == 20
    got = inspect_items.inspect({"type": "Notebook", "definition": "read", "cells": ["%pip install a b c\n" * 30]})
    nb009 = got["rules"]["NB009"]
    assert nb009["count"] == 30 and len(nb009["where"]) == 20


def test_activity_names_are_kept_to_50_and_counted_in_full():
    assert inspect_items.MAX_NAMES == 50
    acts = [{"name": f"a{i:03d}", "inactive": True, "type_properties": {}} for i in range(60)]
    got = inspect_items.inspect({"type": "DataPipeline", "definition": "read", "activities": acts})
    assert got["inactive_count"] == 60 and len(got["inactive"]) == 50


def test_a_fixed_workspace_inside_a_list_is_found():
    acts = [{"name": "copy", "inactive": False, "type_properties": {"sinks": [{"workspaceId": GUID}]}}]
    got = inspect_items.inspect({"type": "DataPipeline", "definition": "read", "activities": acts})
    assert got["fixed_workspace"] == ["copy"]
