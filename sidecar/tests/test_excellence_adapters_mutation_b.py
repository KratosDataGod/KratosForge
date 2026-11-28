"""Mutation batch 544: excellence/adapters.py, re-scored. The survivors were how a value is kept as it
is or cut to text, the refusals of items and capacities that aren't lists, a workload needing both its
item id and its type, and an unread inspection saying it wasn't read."""

from __future__ import annotations

import math

import pytest

from fabric_desktop.excellence import adapters

AT = "2026-11-28T02:00:00+00:00"


@pytest.mark.parametrize("value", [None, True, False, 0, 3, 1.5, -2.25])
def test_a_number_flag_or_none_is_kept_as_it_is(value):
    got = adapters._plain(value)
    assert got == value and type(got) is type(value)


@pytest.mark.parametrize("value, want", [(math.nan, "nan"), (math.inf, "inf"), (-math.inf, "-inf"),
                                         ("abc", "abc"), (["x"], "['x']")])
def test_anything_else_is_kept_as_capped_text(value, want):
    assert adapters._plain(value) == want
    assert adapters._plain("x" * 50, 10) == "x" * 9 + "…"


def test_items_that_arent_a_list_are_refused():
    with pytest.raises(ValueError, match="items aren't a list"):
        adapters.workspace({"id": "w1"}, {"id": "a"}, collected_at=AT)  # type: ignore[arg-type]


def test_capacities_that_arent_a_list_are_refused():
    with pytest.raises(ValueError, match="capacities aren't a list"):
        adapters.capacity("c1", {"id": "c1"}, collected_at=AT)  # type: ignore[arg-type]


def test_a_workload_needs_both_its_item_id_and_its_type():
    result = {"workloads": [
        {"item_id": "n1", "type": "Notebook", "definition": "read"},
        {"item_id": "n2", "definition": "read"},
        {"type": "Notebook", "definition": "read"},
        {"item_id": True, "type": "Notebook", "definition": "read"},
    ]}
    recs = adapters.optimization(result, collected_at=AT)
    assert [r["subject"]["id"] for r in recs] == ["n1"]


def test_an_unread_definition_is_inspected_as_not_read():
    result = {"workloads": [{"item_id": "n1", "type": "Notebook", "definition": "not_read",
                             "problem": "No access.", "inspected": {"read": True}}]}
    [rec] = adapters.inspection(result, collected_at=AT)
    assert rec["confidence"] == "insufficient"
    assert rec["payload"] == {"read": False, "problem": "No access."}


def test_runs_that_arent_an_object_mean_no_history_read():
    result = {"workloads": [{"item_id": "n1", "type": "Notebook", "definition": "read", "runs": ["odd"]}]}
    [rec] = adapters.optimization(result, collected_at=AT)
    assert rec["payload"]["history_read"] is False
    assert rec["payload"]["scheduled_runs"] is None


@pytest.mark.parametrize("inspected, problem", [
    ({"read": False, "problem": "Too big."}, "Too big."),
    ({"read": True, "error": "Bad TMSL."}, "Bad TMSL."),
    (None, "The definition wasn't read."),
    ({"read": False}, "The definition wasn't read."),
])
def test_a_model_not_read_says_why(inspected, problem):
    [rec] = adapters.models([{"item_id": "m1", "name": "Sales", "workspace_id": "w1", "inspected": inspected}],
                            collected_at=AT)
    assert rec["confidence"] == "insufficient"
    assert rec["payload"] == {"read": False, "problem": problem}
