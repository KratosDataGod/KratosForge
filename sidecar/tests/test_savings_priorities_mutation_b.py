"""Mutation batch 488: a benefit of zero is a figure, the benefit is rounded to six places, and an
impact that isn't a measurement is refused by name."""
from __future__ import annotations

import re

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.opportunities import model
from fabric_desktop.savings import priorities as pr

W = {"workspace_id": "ws", "item_id": "nb1", "type": "Notebook", "name": "Sales"}


def opp(key, impact):
    o = model.make(rule=f"rule-{key}", category="inefficient_code", action="optimize_in_place", title=f"Opportunity {key}",
                   artifacts=[model.artifact(W, "change")], current="c", problem="p",
                   evidence=[model.evidence("code", "seen", "definition")], recommendation="r", alternatives=[],
                   effort="low", risk="low", confidence="high", validation=["compare"], rollback="restore",
                   source="test", next_step={"kind": "open"}, key=[key])
    o["impact"] = impact
    o["status"] = "proposed"
    return o


def measured(v, unit=ms.CU_HOURS):
    return ms.make(ms.MEASURED, "fabric_cu_avoided", unit, v, source="Prove runs", collected_at="2026-11-01T00:00:00Z")


def test_a_measured_benefit_of_zero_is_ranked_as_measured():
    item = pr.rank([opp("a", {"cu_per_month": measured(0)})])["items"][0]
    assert item["tier"] == "measured" and item["benefit_value"] == 0


def test_the_benefit_is_rounded_to_six_places():
    item = pr.rank([opp("a", {"cu_per_month": measured(1, ms.CU_SECONDS)})])["items"][0]
    assert item["benefit_value"] == 0.000278


def test_an_impact_that_isnt_a_measurement_is_refused_by_name():
    o = opp("a", {"cu_per_month": "lots"})
    with pytest.raises(ValueError, match=re.escape(f"Opportunity {o['id']} has an impact cu_per_month that isn't a measurement")):
        pr.rank([o])
