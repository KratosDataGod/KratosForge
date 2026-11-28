"""Mutation-killing tests for parity.py (batch 541): a frozen plan and the plan table's exact text."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop import parity
from fabric_desktop.parity import Area, Plan


def test_a_plan_cant_be_changed_after_it_is_made():
    plan = Plan("A", "P0", "cloud", ("SF-001",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.owner = "someone"


def test_the_plan_table_has_a_row_per_area_and_a_dot_for_no_items(monkeypatch):
    areas = (Area("fabric", "a", "Area A", {}), Area("snowflake", "b", "Area B", {}))
    plans = {
        ("fabric", "a"): Plan("A", "P0", "cloud", ("FAB-001", "PAR-002"), owner="Team"),
        ("snowflake", "b"): Plan("G", "P2", "none"),
    }
    monkeypatch.setattr(parity, "AREAS", areas)
    monkeypatch.setattr(parity, "PLANS", plans)
    assert parity.plan_table() == (
        "| Platform | Area | Family | Priority | Runs | Next items | Owner |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| Microsoft Fabric | Area A | A Authentication and connectivity | P0 | Cloud only | FAB-001, PAR-002 | Team |\n"
        "| Snowflake | Area B | G Monitoring, cost and optimization | P2 | Not built yet | · | unassigned |\n"
    )


def test_the_real_plan_table_ends_with_one_newline():
    text = parity.plan_table()
    assert text.endswith(" |\n") and not text.endswith("\n\n")
    assert text.count("\n") == len(parity.AREAS) + 2
