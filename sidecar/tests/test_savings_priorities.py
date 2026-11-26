"""ESI-603: open opportunities ranked by evidence-backed benefit; measured, estimated, run time,
scenario and unknown never share a sort key, and nothing is hidden or inflated."""

from __future__ import annotations

import copy
import math

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.opportunities import model
from fabric_desktop.savings import priorities as pr

W = {"workspace_id": "ws", "item_id": "nb1", "type": "Notebook", "name": "Sales"}


def measured(v: float, unit: str = ms.CU_HOURS, metric: str = "fabric_cu_avoided") -> dict:
    return ms.make(ms.MEASURED, metric, unit, v, source="before and after Prove runs",
                   collected_at="2026-11-01T00:00:00Z")


def estimated(v: float, unit: str = ms.CU_HOURS, metric: str = "fabric_cu_avoided") -> dict:
    return ms.make(ms.ESTIMATED, metric, unit, v, method="scaled to 30 days", assumptions=["same as last month"])


def scenario(v: float) -> dict:
    return ms.make(ms.SCENARIO, "fabric_cu_avoided", ms.CU_HOURS, v, method="as if run in Fabric",
                   assumptions=["F64 price"])


def unknown(reason: str = "How much it reduces isn't measured yet.", unit: str = ms.CU_SECONDS) -> dict:
    return ms.unknown("fabric_cu_avoided", unit, reason)


def opp(key: str, *, cu: dict | None = None, secs: dict | None = None, action: str = "optimize_in_place",
        status: str = "proposed", impact: dict | None = None) -> dict:
    o = model.make(rule=f"rule-{key}", category="inefficient_code", action=action, title=f"Opportunity {key}",
                   artifacts=[model.artifact(W, "change")], current="c", problem="p",
                   evidence=[model.evidence("code", "seen", "definition")], recommendation="r", alternatives=[],
                   effort="low", risk="low", confidence="high", validation=["compare"], rollback="restore",
                   source="test", next_step={"kind": "open"}, key=[key])
    imp = impact if impact is not None else {}
    if impact is None and cu is not None:
        imp["cu_per_month"] = cu
    if impact is None and secs is not None:
        imp["run_seconds_per_month"] = secs
    o["impact"] = imp
    o["status"] = status
    return o


def keys(result: dict) -> list[str]:
    return [i["title"].split()[-1] for i in result["items"]]


# ---------- order

def test_measured_then_estimated_then_run_time_then_scenario_then_unknown():
    got = pr.rank([opp("u", cu=unknown()), opp("s", cu=scenario(500)), opp("e", cu=estimated(50)),
                   opp("t", cu=unknown(), secs=estimated(99999, ms.SECONDS)), opp("m", cu=measured(1))])
    assert keys(got) == ["m", "e", "t", "s", "u"]
    assert [i["tier"] for i in got["items"]] == ["measured", "estimated", "run_time", "scenario", "unknown"]
    assert [i["rank"] for i in got["items"]] == [1, 2, 3, 4, 5]
    assert got["counts"] == {"measured": 1, "estimated": 1, "run_time": 1, "scenario": 1, "unknown": 1}


def test_a_big_estimate_never_outranks_a_small_measured_figure():
    got = pr.rank([opp("big", cu=estimated(1000)), opp("small", cu=measured(0.5))])
    assert keys(got) == ["small", "big"]


def test_within_a_tier_bigger_first_then_action_order_then_id():
    got = pr.rank([opp("a", cu=estimated(2), action="reschedule"), opp("b", cu=estimated(5)),
                   opp("c", cu=estimated(2), action="eliminate"), opp("d", cu=estimated(2), action="reschedule")])
    assert keys(got)[:2] == ["b", "c"]  # eliminate comes before reschedule at the same benefit
    tied = [i["opportunity_id"] for i in got["items"] if i["action"] == "reschedule"]
    assert tied == sorted(tied)


def test_order_is_the_same_whatever_the_input_order():
    opps = [opp(k, cu=estimated(1)) for k in "abcdef"] + [opp("g", cu=unknown()), opp("h", cu=unknown())]
    first = [i["opportunity_id"] for i in pr.rank(opps)["items"]]
    assert [i["opportunity_id"] for i in pr.rank(list(reversed(opps)))["items"]] == first


def test_cu_seconds_and_cu_hours_compare_in_cu_hours():
    got = pr.rank([opp("hours", cu=estimated(1.5)), opp("secs", cu=estimated(7200, ms.CU_SECONDS))])
    assert keys(got) == ["secs", "hours"]
    assert got["items"][0]["benefit_value"] == 2 and got["items"][0]["unit"] == ms.CU_HOURS


def test_run_time_is_used_only_when_cu_is_not_known():
    got = pr.rank([opp("both", cu=estimated(0.1), secs=estimated(1e6, ms.SECONDS))])
    assert got["items"][0]["tier"] == "estimated" and got["items"][0]["unit"] == ms.CU_HOURS


def test_money_is_never_a_benefit():
    value = ms.make(ms.ESTIMATED, "cu_value", ms.CURRENCY, 1e6, currency="USD", method="price",
                    assumptions=["price"])
    got = pr.rank([opp("money", impact={"cu_value_per_month": value}), opp("cu", cu=estimated(1))])
    assert keys(got) == ["cu", "money"]
    assert got["items"][1]["tier"] == "unknown" and got["items"][1]["benefit_value"] is None


# ---------- unknown stays unknown

def test_unknown_is_listed_with_none_and_its_reason():
    item = pr.rank([opp("u", cu=unknown("Its runs have no end time yet."))])["items"][0]
    assert item["benefit_value"] is None and item["benefit"]["value"] is None
    assert item["classification"] == ms.UNKNOWN
    assert item["reason"] == "Its benefit isn't known, so it is listed last: Its runs have no end time yet."


def test_no_impact_at_all_is_listed_as_unknown():
    item = pr.rank([opp("none")])["items"][0]
    assert item["tier"] == "unknown" and item["benefit"]["value"] is None
    assert "No impact figure was recorded" in item["reason"]


def test_unknown_is_not_filled_in_with_an_average():
    got = pr.rank([opp("a", cu=estimated(4)), opp("b", cu=estimated(8)), opp("u", cu=unknown())])
    assert got["items"][-1]["benefit_value"] is None


def test_the_benefit_figure_is_passed_through_unchanged():
    cu = measured(3)
    item = pr.rank([opp("m", cu=cu)])["items"][0]
    assert item["benefit"] == cu and item["classification"] == ms.MEASURED


# ---------- reasons

def test_every_item_has_a_one_sentence_reason():
    got = pr.rank([opp("m", cu=measured(1)), opp("e", cu=estimated(2)), opp("t", secs=measured(60, ms.SECONDS)),
                   opp("s", cu=scenario(3)), opp("u", cu=unknown())])
    for item in got["items"]:
        r = item["reason"]
        assert r and r[0].isupper() and r.endswith(".") and ". " not in r, r
    assert "measured at 1 CU hours" in got["items"][0]["reason"]
    assert "60 seconds of run time" in got["items"][2]["reason"]
    assert "scenario" in got["items"][3]["reason"]


# ---------- open only

@pytest.mark.parametrize("status", pr.OPEN_STATUSES)
def test_open_statuses_are_ranked(status):
    assert len(pr.rank([opp("a", status=status)])["items"]) == 1


@pytest.mark.parametrize("status", [s for s in model.STATUSES if s not in pr.OPEN_STATUSES])
def test_done_or_closed_ones_are_listed_as_not_open(status):
    got = pr.rank([opp("a", status=status, cu=measured(100)), opp("b", cu=unknown())])
    assert keys(got) == ["b"]
    assert got["not_open"] == [{"opportunity_id": model.opportunity_id("rule-a", "a"), "status": status}]


def test_open_statuses_come_from_the_model():
    assert set(pr.OPEN_STATUSES) < set(model.STATUSES)
    assert pr.rank([])["open_statuses"] == list(pr.OPEN_STATUSES)


def test_overlaps_are_carried():
    o = opp("a", cu=estimated(1))
    o["overlaps"] = ["opp-x"]
    assert pr.rank([o])["items"][0]["overlaps"] == ["opp-x"]


# ---------- pure

def test_the_input_is_not_changed():
    opps = [opp("a", cu=estimated(1)), opp("b", status="verified")]
    before = copy.deepcopy(opps)
    pr.rank(opps)
    assert opps == before


# ---------- refusals

@pytest.mark.parametrize("bad", [math.nan, math.inf, -1.0, True, "5"])
def test_bad_figures_are_refused_naming_the_opportunity(bad):
    o = opp("a", cu=estimated(1))
    o["impact"]["cu_per_month"]["value"] = bad
    with pytest.raises(ValueError, match=o["id"]):
        pr.rank([o])


def test_bad_run_time_is_refused_even_when_cu_is_known():
    o = opp("a", cu=estimated(1), secs=estimated(1, ms.SECONDS))
    o["impact"]["run_seconds_per_month"]["value"] = -5
    with pytest.raises(ValueError, match="finite number of zero or more"):
        pr.rank([o])


def test_unknown_with_a_value_is_refused():
    o = opp("a", cu=unknown())
    o["impact"]["cu_per_month"]["value"] = 0
    with pytest.raises(ValueError, match="unknown figure has none"):
        pr.rank([o])


@pytest.mark.parametrize("field,value,match", [
    ("status", "open", "unknown status 'open'"),
    ("action", "delete", "unknown action 'delete'"),
    ("id", "", "has no id"),
])
def test_bad_records_are_refused(field, value, match):
    o = opp("a")
    o[field] = value
    with pytest.raises(ValueError, match=match):
        pr.rank([o])


def test_wrong_unit_and_classification_are_refused():
    with pytest.raises(ValueError, match="must be in cu_seconds or cu_hours"):
        pr.rank([opp("a", cu=estimated(1, ms.SECONDS))])
    o = opp("b", cu=estimated(1))
    o["impact"]["cu_per_month"]["classification"] = "guess"
    with pytest.raises(ValueError, match="unknown classification 'guess'"):
        pr.rank([o])


def test_a_repeated_id_and_a_non_record_are_refused():
    o = opp("a")
    with pytest.raises(ValueError, match="appears twice"):
        pr.rank([o, dict(o)])
    with pytest.raises(ValueError, match="must be a record"):
        pr.rank(["opp-1"])


def test_real_assessment_opportunities_rank():
    """Records from the opportunity engine's demonstration fixture rank without change."""
    from fabric_desktop.opportunities import assess, fixture
    f = fixture.load()
    result = assess.assess([f["source"]], usage_imports=f["usage_imports"], price=f["price"], now=f["now"],
                           fixture=True)
    opps = result["opportunities"]
    before = copy.deepcopy(opps)
    got = pr.rank(opps)
    assert opps == before and opps
    assert len(got["items"]) == len(opps) and got["not_open"] == []
    tiers = [pr.TIERS.index(i["tier"]) for i in got["items"]]
    assert tiers == sorted(tiers)
    for item in got["items"]:
        assert (item["benefit_value"] is None) == (item["tier"] == "unknown")
