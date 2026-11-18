"""Corrections, reversals and reconciliation (ESI-404): the ledger is only appended to, and adds up."""

from __future__ import annotations

import random

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import corrections as co


def measured(cu_hours: float) -> dict:
    return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, cu_hours, source="Capacity Metrics export",
                   collected_at="2026-11-01T00:00:00Z")


def estimated(cu_hours: float) -> dict:
    return ms.make(ms.ESTIMATED, "fabric_job_cu_avoided", ms.CU_HOURS, cu_hours, method="calibration",
                   assumptions=["median of 5 Fabric runs"])


def unknown() -> dict:
    return ms.unknown("fabric_job_cu", ms.CU_HOURS, "not measured yet")


def run(ran: str = at.PC, eid: str = "e1", when: str = "2026-11-10T00:00:00Z", **kw) -> dict:
    return {"execution_id": eid, "workload_key": "nb:sales", "baseline_version": "b1", "at": when, "ran": ran, **kw}


def opt(oid: str, after: dict | None, when: str = "2026-11-05T00:00:00Z", **kw) -> dict:
    return {"optimization_id": oid, "implemented_at": when, "action": "optimize_in_place", "state": "verified",
            "after": after, **kw}


def layers_by_name(events: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in events:
        out.setdefault(e["layer"], []).append(e)
    return out


def ledger_sum(events: list[dict], layer: str | None = None) -> float:
    return round(sum(e["cu_hours"] for e in events if e["cu_hours"] is not None
                     and (layer is None or e["layer"] == layer)), 6)


# ---------- scenario 19: historical correction

def test_scenario_19_a_past_run_is_corrected_without_editing_its_events():
    # 10 November: a run on this PC while o1's after was still being validated. The split waits.
    first = at.attribute(run(), measured(10), [opt("o1", None)], local_avoided=estimated(9),
                         local_basis=at.ORIGINAL)
    ledger = list(first["layers"])
    written = [dict(e) for e in ledger]
    assert {e["layer"]: e["cu_hours"] for e in ledger} == {"o1": None, at.UNATTRIBUTED: 9}

    # 20 November: o1's after is measured at 6, so the same run is attributed again.
    second = at.attribute(run(), measured(10), [opt("o1", measured(6))], local_avoided=estimated(9),
                          local_basis=at.ORIGINAL)
    fix = co.correct(ledger, second, reason="o1's after was measured on 20 November")
    by = {e["layer"]: e for e in fix}
    assert by["o1"]["event_type"] == co.CORRECTION and by["o1"]["cu_hours"] == 4
    assert by["o1"]["corrects"] == first["layers"][0]["ledger_event_id"]
    assert by["o1"]["result_cu_hours"] == 4 and by["o1"]["result_classification"] == ms.MEASURED
    assert by[at.LOCAL]["event_type"] == at.ACCRUAL and by[at.LOCAL]["cu_hours"] == 6  # a new layer
    assert by[at.UNATTRIBUTED]["event_type"] == co.REVERSAL and by[at.UNATTRIBUTED]["cu_hours"] == -9
    assert by[at.UNATTRIBUTED]["layer_removed"]
    assert "20 November" in by["o1"]["reason"]
    ledger += fix
    assert ledger[:len(written)] == written  # nothing earlier was changed
    assert ledger_sum(ledger) == second["gross_cu_hours"] == 10  # the total is the same; only the split moved

    # 2 December: the Capacity Metrics export for that week is revised and the baseline was 9, not 10.
    third = at.attribute(run(), measured(9), [opt("o1", measured(6))], local_avoided=estimated(9),
                         local_basis=at.ORIGINAL)
    fix2 = co.correct(ledger, third, reason="Capacity Metrics export revised")
    assert [(e["layer"], e["cu_hours"]) for e in fix2] == [("o1", -1)]
    assert fix2[0]["corrects"] == by["o1"]["ledger_event_id"]  # a correction of the correction
    ledger += fix2
    report = co.reconcile(ledger, [third])
    assert report["reconciled"] and report["runs"][0]["status"] == "matched"
    assert report["ledger"]["value"] == 9 and report["expected"]["value"] == 9


def test_correcting_twice_with_the_same_input_gives_the_same_events():
    first = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    second = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(5))])
    a = co.correct(first["layers"], second)
    b = co.correct([dict(e) for e in first["layers"]], second)
    assert a == b and [e["ledger_event_id"] for e in a] == [e["ledger_event_id"] for e in b]
    # Once written, correcting again with the same attribution adds nothing.
    assert co.correct(first["layers"] + a, second) == []


def test_unchanged_layers_get_no_event():
    first = at.attribute(run(), measured(10), [opt("o1", measured(6))], local_avoided=estimated(6))
    second = at.attribute(run(), measured(10), [opt("o1", measured(6))], local_avoided=estimated(5))
    fix = co.correct(first["layers"], second)
    assert [(e["layer"], e["cu_hours"]) for e in fix] == [(at.LOCAL, -1)]


def test_a_correction_of_a_correction_points_at_the_correction():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(7))])
    c = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(2))])
    c1 = co.correct(a["layers"], b)
    c2 = co.correct(a["layers"] + c1, c)
    assert c1[0]["corrects"] == a["layers"][0]["ledger_event_id"] and c1[0]["cu_hours"] == -1
    assert c2[0]["corrects"] == c1[0]["ledger_event_id"] and c2[0]["cu_hours"] == 5
    assert c1[0]["ledger_event_id"] != c2[0]["ledger_event_id"]
    assert ledger_sum(a["layers"] + c1 + c2) == 8
    assert co.reconcile(a["layers"] + c1 + c2, [c])["reconciled"]


def test_a_correction_can_turn_a_saving_into_a_regression_and_back():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(12))])
    fix = co.correct(a["layers"], b)
    assert fix[0]["cu_hours"] == -6 and fix[0]["result_cu_hours"] == -2
    # The ledger holds layers: this run sums below zero though its gross is 0.
    report = co.reconcile(a["layers"] + fix, [b])
    assert report["reconciled"] and report["ledger"]["value"] == -2 and b["gross_cu_hours"] == 0


def test_a_better_classification_alone_is_a_correction():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", estimated(6))])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    fix = co.correct(a["layers"], b)
    assert fix[0]["cu_hours"] == 0 and fix[0]["classification"] == ms.ESTIMATED
    assert fix[0]["result_classification"] == ms.MEASURED
    assert co.reconcile(a["layers"] + fix, [b])["ledger"]["classification"] == ms.MEASURED


def test_a_new_baseline_version_and_pricing_version_go_on_the_correction():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    b = at.attribute(run(at.FABRIC, baseline_version="b2"), measured(9), [opt("o1", measured(6))])
    one = co.correct(a["layers"], b, pricing_version="p1")
    two = co.correct(a["layers"], b, pricing_version="p2")
    assert one[0]["baseline_version"] == "b2" and one[0]["pricing_version"] == "p1"
    assert one[0]["ledger_event_id"] != two[0]["ledger_event_id"]


# ---------- unknown stays unknown

def test_a_layer_that_becomes_unknown_withdraws_its_figure_and_shows_unknown():
    a = at.attribute(run(), measured(10), [opt("o1", measured(6))], local_avoided=estimated(6))
    b = at.attribute(run(), measured(10), [opt("o1", measured(6))], local_avoided=unknown())
    fix = co.correct(a["layers"], b)
    assert [(e["layer"], e["cu_hours"], e["classification"]) for e in fix] == [(at.LOCAL, -6, ms.ESTIMATED)]
    assert fix[0]["result_cu_hours"] is None and fix[0]["result_classification"] == ms.UNKNOWN
    report = co.reconcile(a["layers"] + fix, [b])
    assert report["reconciled"]
    assert report["ledger"]["value"] == 4 and report["ledger"]["unknown_parts"] == 1
    assert not report["ledger"]["complete"]


def test_an_unknown_layer_that_is_still_unknown_gets_nothing():
    a = at.attribute(run(), measured(10), [opt("o1", None)], local_avoided=unknown())
    b = at.attribute(run(), measured(10), [opt("o1", None)], local_avoided=unknown())
    assert co.correct(a["layers"], b) == []


def test_an_unknown_layer_that_becomes_known_adds_its_whole_figure():
    a = at.attribute(run(), measured(10), local_avoided=unknown())
    b = at.attribute(run(), measured(10), local_avoided=measured(7))
    fix = co.correct(a["layers"], b)
    assert fix[0]["cu_hours"] == 7 and fix[0]["classification"] == ms.MEASURED
    assert fix[0]["corrects"] == a["layers"][0]["ledger_event_id"]


def test_an_unknown_layer_that_goes_away_is_closed_with_no_figure():
    a = at.attribute(run(at.FABRIC), unknown(), [opt("o1", measured(6))])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6), reverted_at="2026-11-06T00:00:00Z")])
    fix = co.correct(a["layers"], b)
    assert [(e["event_type"], e["cu_hours"], e["classification"]) for e in fix] == [(co.REVERSAL, None, ms.UNKNOWN)]
    report = co.reconcile(a["layers"] + fix, [b])
    assert report["reconciled"] and report["ledger"]["unknown_parts"] == 0


def test_reconciliation_never_counts_unknown_as_zero():
    a = at.attribute(run(), unknown(), [opt("o1", measured(6))], local_avoided=estimated(6))
    report = co.reconcile(a["layers"], [a])
    assert report["reconciled"]
    assert report["ledger"]["value"] is None and report["ledger"]["classification"] == ms.UNKNOWN
    assert report["difference"] is None


# ---------- reversal

def test_a_reversal_nets_every_accrual_of_the_run_to_zero():
    a = at.attribute(run(), measured(10), [opt("o1", measured(6))], local_avoided=estimated(6))
    rev = co.reverse(a["layers"], reason="o1 was never actually deployed")
    assert {e["event_type"] for e in rev} == {co.REVERSAL}
    assert {e["corrects"] for e in rev} == {x["ledger_event_id"] for x in a["layers"]}
    ledger = a["layers"] + rev
    for name, events in layers_by_name(ledger).items():
        assert ledger_sum(events) == 0, name
    report = co.reconcile(ledger, [])
    assert report["reconciled"] and report["runs"][0]["status"] == "reversed"
    assert report["ledger"]["value"] == 0
    # Reversing again writes nothing; reversing twice from scratch gives the same ids.
    assert co.reverse(ledger, reason="o1 was never actually deployed") == []
    assert co.reverse(a["layers"], reason="again")[0]["ledger_event_id"] == rev[0]["ledger_event_id"]


def test_a_reversal_after_corrections_undoes_what_the_chain_counts():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(3))])
    ledger = a["layers"] + co.correct(a["layers"], b)
    rev = co.reverse(ledger, reason="the change was never deployed")
    assert rev[0]["cu_hours"] == -7 and rev[0]["corrects"] == ledger[-1]["ledger_event_id"]
    assert ledger_sum(ledger + rev) == 0


def test_a_reversal_of_a_regression_adds_it_back_and_one_layer_can_be_reversed():
    a = at.attribute(run(), measured(10), [opt("o1", measured(12))], local_avoided=estimated(5))
    rev = co.reverse(a["layers"], reason="o1 never deployed", layers=["o1"])
    assert [(e["layer"], e["cu_hours"]) for e in rev] == [("o1", 2)]
    assert ledger_sum(a["layers"] + rev) == 5


def test_a_reversal_needs_a_reason_and_a_layer_that_exists():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    with pytest.raises(ValueError, match="says why"):
        co.reverse(a["layers"], reason="  ")
    with pytest.raises(ValueError, match="no layer nope"):
        co.reverse(a["layers"], reason="x", layers=["nope"])
    assert co.reverse([], reason="nothing written") == []


def test_a_reversed_layer_that_comes_back_is_a_correction_not_a_second_accrual():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    ledger = a["layers"] + co.reverse(a["layers"], reason="thought it never deployed")
    fix = co.correct(ledger, a)
    assert fix[0]["event_type"] == co.CORRECTION and fix[0]["cu_hours"] == 4
    assert fix[0]["ledger_event_id"] != a["layers"][0]["ledger_event_id"]
    assert co.reconcile(ledger + fix, [a])["reconciled"]


# ---------- reconciliation

def test_reconciliation_reports_a_difference_with_its_amounts_and_fixes_nothing():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(5))])
    events = list(a["layers"])
    report = co.reconcile(events, [b])
    assert not report["reconciled"] and not report["fixed"] and report["mismatched"] == 1
    r = report["runs"][0]
    assert r["status"] == "mismatched" and r["difference"] == -1
    assert r["mismatches"] == [{"layer": "o1", "kind": "amount", "ledger_cu_hours": 4,
                                "ledger_classification": ms.MEASURED, "expected_cu_hours": 5,
                                "expected_classification": ms.MEASURED, "difference": -1}]
    assert events == a["layers"]


def test_reconciliation_finds_missing_extra_and_unknown_differences():
    a = at.attribute(run(eid="e1"), measured(10), [opt("o1", measured(6))], local_avoided=estimated(6))
    b = at.attribute(run(eid="e2"), measured(10), [opt("o1", measured(6))])
    c = at.attribute(run(eid="e3"), measured(8))
    a_unknown = at.attribute(run(eid="e1"), measured(10), [opt("o1", measured(6))], local_avoided=unknown())
    report = co.reconcile(a["layers"] + c["layers"], [a_unknown, b])
    status = {r["execution_id"]: r["status"] for r in report["runs"]}
    assert status == {"e1": "mismatched", "e2": "not_in_ledger", "e3": "not_expected"}
    e1 = report["runs"][0]
    assert [m["kind"] for m in e1["mismatches"]] == ["known_in_ledger"]
    assert report["matched"] == 0 and report["mismatched"] == 3


def test_reconciliation_reports_events_that_do_not_fit():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(5))])
    c = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(4))])
    # Two PCs corrected the same event from different inputs: same id, different figures.
    same_id = co.correct(a["layers"], b) + co.correct(a["layers"], c)
    report = co.reconcile(a["layers"] + same_id, [c])
    assert not report["reconciled"] and any("twice with different figures" in p for p in report["problems"])
    fork = co.correct(a["layers"], b, pricing_version="p1") + co.correct(a["layers"], c, pricing_version="p2")
    report = co.reconcile(a["layers"] + fork, [c])
    assert not report["reconciled"] and any("corrected 2 times" in p for p in report["problems"])
    orphan = dict(co.correct(a["layers"], b)[0], corrects="gone", ledger_event_id="x1")
    report = co.reconcile([orphan], [b])
    assert any("isn't in the ledger" in p for p in report["problems"])
    twice = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))], pricing_version="p2")
    report = co.reconcile(a["layers"] + twice["layers"], [a])
    assert any("separate accruals" in p for p in report["problems"]) and not report["reconciled"]
    assert report["ledger"]["value"] == 8  # what the ledger holds, counted twice and said so


def test_the_same_event_from_two_pcs_counts_once():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    report = co.reconcile(a["layers"] + [dict(x) for x in a["layers"]], [a, a])
    assert report["reconciled"] and report["ledger"]["value"] == 4
    changed = dict(a["layers"][0], cu_hours=3.0)
    assert co.reconcile(a["layers"] + [changed], [a])["problems"]


def test_a_correction_whose_result_does_not_add_up_is_reported():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(5))])
    bad = dict(co.correct(a["layers"], b)[0], result_cu_hours=9.0)
    report = co.reconcile(a["layers"] + [bad], [b])
    assert any("adds up to 5" in p for p in report["problems"])


def test_a_run_with_nothing_to_attribute_matches_an_empty_ledger():
    a = at.attribute(run(at.FABRIC), measured(10))
    assert co.reconcile([], [a])["reconciled"]


def test_two_different_expected_attributions_of_one_run_are_refused():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(5))])
    with pytest.raises(ValueError, match="two different expected"):
        co.reconcile([], [a, b])


# ---------- refusals

def test_scenario_figures_are_refused():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    scen = dict(a["layers"][0], classification=ms.SCENARIO)
    with pytest.raises(ValueError, match="scenario"):
        co.reconcile([scen], [])
    with pytest.raises(ValueError, match="scenario"):
        co.correct([scen], a)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "4", True])
def test_non_finite_figures_are_refused(bad):
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    with pytest.raises(ValueError, match="number"):
        co.reconcile([dict(a["layers"][0], cu_hours=bad)], [])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(5))])
    fix = co.correct(a["layers"], b)[0]
    with pytest.raises(ValueError, match="number"):
        co.reconcile(a["layers"] + [dict(fix, result_cu_hours=bad)], [])


def test_negative_accruals_and_positive_regressions_are_refused():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    with pytest.raises(ValueError, match="negative"):
        co.reconcile([dict(a["layers"][0], cu_hours=-1.0)], [])
    with pytest.raises(ValueError, match="positive"):
        co.reconcile([dict(a["layers"][0], cu_hours=1.0, event_type=at.REGRESSION)], [])


def test_an_unknown_event_with_a_figure_and_a_correction_without_corrects_are_refused():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    with pytest.raises(ValueError, match="never a number"):
        co.reconcile([dict(a["layers"][0], classification=ms.UNKNOWN)], [])
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(5))])
    fix = co.correct(a["layers"], b)[0]
    with pytest.raises(ValueError, match="which event"):
        co.reconcile(a["layers"] + [dict(fix, corrects=None)], [])
    with pytest.raises(ValueError, match="realization"):
        co.reconcile([dict(a["layers"][0], event_type="realization")], [])


def test_correct_refuses_another_run_and_a_ledger_that_does_not_add_up():
    a = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(6))])
    other = at.attribute(run(at.FABRIC, eid="e2"), measured(10), [opt("o1", measured(5))])
    with pytest.raises(ValueError, match="one run at a time"):
        co.correct(a["layers"], other)
    b = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(5))])
    c = at.attribute(run(at.FABRIC), measured(10), [opt("o1", measured(4))])
    fork = co.correct(a["layers"], b) + co.correct(a["layers"], c)
    with pytest.raises(ValueError, match="doesn't add up"):
        co.correct(a["layers"] + fork, c)
    with pytest.raises(ValueError, match="doesn't add up"):
        co.reverse(a["layers"] + fork, reason="never deployed")


# ---------- property: corrections bring the ledger to the latest attribution

def _random_attribution(rnd: random.Random) -> dict:
    def fig(lo: float, hi: float) -> dict | None:
        pick = rnd.random()
        if pick < 0.2:
            return None
        return (measured if pick < 0.6 else estimated)(round(rnd.uniform(lo, hi), 3))

    base = rnd.choice([measured(round(rnd.uniform(1, 20), 3)), estimated(round(rnd.uniform(1, 20), 3)), unknown()])
    opts = [opt(f"o{i}", fig(0, 25), when=f"2026-11-0{i + 1}T00:00:00Z",
                reverted_at=rnd.choice([None, None, "2026-11-08T00:00:00Z"]))
            for i in range(rnd.randint(0, 3))]
    ran = rnd.choice([at.PC, at.FABRIC])
    local = fig(0, 30) or unknown()
    return at.attribute(run(ran), base, opts, local_avoided=local, local_basis=rnd.choice([at.ORIGINAL, at.OPTIMIZED]),
                        overhead=measured(0.1))


def test_applying_corrections_makes_the_ledger_equal_the_latest_attribution():
    rnd = random.Random(19)
    for _ in range(300):
        first = _random_attribution(rnd)
        ledger = list(first["layers"])
        latest = first
        for _ in range(rnd.randint(1, 5)):
            if rnd.random() < 0.15:
                ledger += co.reverse(ledger, reason="never deployed") if ledger else []
            latest = _random_attribution(rnd)
            ledger += co.correct(ledger, latest)
            assert co.correct(ledger, latest) == []  # idempotent once written
        report = co.reconcile(ledger, [latest])
        assert report["reconciled"], report
        known = [x["cu_hours"] for x in latest["layers"] if x["cu_hours"] is not None]
        if known:
            assert report["ledger"]["value"] == pytest.approx(sum(known), abs=1e-6)
            assert report["ledger"]["value"] <= (latest["baseline_cu_hours"] or 0) + 1e-6
        else:
            assert report["ledger"]["value"] in (None, 0)
        assert len({e["ledger_event_id"] for e in ledger}) == len(ledger)  # nothing collides
