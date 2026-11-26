"""Corrections, reversals and reconciliation: what the first mutation run of savings/corrections.py left
alive (batch 332)."""

from __future__ import annotations

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import corrections as co


def measured(cu_hours: float) -> dict:
    return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, cu_hours, source="Capacity Metrics export",
                   collected_at="2026-11-01T00:00:00Z")


def run(ran: str = at.FABRIC, eid: str = "e1") -> dict:
    return {"execution_id": eid, "workload_key": "nb:sales", "baseline_version": "b1",
            "at": "2026-11-10T00:00:00Z", "ran": ran}


def opt(oid: str, after: dict | None) -> dict:
    return {"optimization_id": oid, "implemented_at": "2026-11-05T00:00:00Z", "action": "optimize_in_place",
            "state": "verified", "after": after}


def accrual(**kw) -> dict:
    return {"ledger_event_id": "a1", "execution_id": "e1", "layer": "o1", "event_type": co.ACCRUAL,
            "classification": ms.MEASURED, "cu_hours": 1.0, **kw}


def fix(**kw) -> dict:
    return accrual(**{"ledger_event_id": "c1", "event_type": co.CORRECTION, "cu_hours": -1.0, "corrects": "a1",
                      "result_classification": ms.MEASURED, "result_cu_hours": 0.0, "layer_removed": False, **kw})


def test_the_weakest_of_measured_and_estimated_is_estimated_in_either_order():
    assert co._weakest(ms.MEASURED, ms.ESTIMATED) == ms.ESTIMATED
    assert co._weakest(ms.ESTIMATED, ms.MEASURED) == ms.ESTIMATED


@pytest.mark.parametrize("key", ["ledger_event_id", "execution_id", "layer"])
def test_an_event_without_an_id_run_or_layer_is_refused_by_name(key):
    with pytest.raises(ValueError, match=f"A ledger event needs {key}"):
        co._check(accrual(**{key: ""}))


def test_a_scenario_event_is_refused_as_a_scenario():
    with pytest.raises(ValueError, match="is a scenario figure"):
        co._check(accrual(classification=ms.SCENARIO))


def test_an_event_of_an_unknown_classification_is_refused():
    with pytest.raises(ValueError, match="has unknown classification 'guess'"):
        co._check(accrual(classification="guess"))


@pytest.mark.parametrize("rcls", [ms.SCENARIO, None, "guess"])
def test_a_correction_that_doesnt_say_what_its_layer_is_now_is_refused(rcls):
    with pytest.raises(ValueError, match="doesn't say what its layer is afterwards"):
        co._check(fix(result_classification=rcls))


def test_a_correction_whose_result_doesnt_fit_its_classification_is_refused():
    with pytest.raises(ValueError, match="result figure that doesn't fit"):
        co._check(fix(result_cu_hours=None))
    with pytest.raises(ValueError, match="result figure that doesn't fit"):
        co._check(fix(result_classification=ms.UNKNOWN))
    assert co._check(fix(result_classification=ms.UNKNOWN, result_cu_hours=None))["ledger_event_id"] == "c1"


def test_a_correction_of_another_runs_event_in_the_same_layer_is_reported():
    _, problems = co._chains([accrual(), fix(execution_id="e2")])
    assert "Event c1 corrects an event of another run or layer." in problems
    _, problems = co._chains([accrual(), fix(layer="o2")])
    assert "Event c1 corrects an event of another run or layer." in problems
    _, problems = co._chains([accrual(), fix()])
    assert problems == []


def test_the_reason_says_what_the_layer_was_and_is_now():
    a = at.attribute(run(), measured(10), [opt("o1", measured(6))])
    b = at.attribute(run(), measured(10), [opt("o1", measured(7))])
    out = co.correct(a["layers"], b, reason="revised")
    assert out[0]["reason"] == "Re-attributed: was 4 CU-hours, now 3 CU-hours. revised"


def test_the_reason_says_removed_for_a_reversed_layer_and_unknown_for_none():
    a = at.attribute(run(), measured(10), [opt("o1", measured(6))])
    ledger = a["layers"] + co.reverse(a["layers"], reason="never deployed")
    out = co.correct(ledger, a)
    assert out[0]["reason"] == "Re-attributed: was removed, now 4 CU-hours"
    assert co._was(None, False) == "Unknown"
    assert co._was(None, True) == co._was(2.0, True) == "removed"


@pytest.mark.parametrize("key", ["execution_id", "workload_key", "baseline_version", "layers"])
def test_a_new_attribution_without_its_run_workload_baseline_or_layers_is_refused(key):
    b = at.attribute(run(), measured(10), [opt("o1", measured(6))])
    b[key] = None
    with pytest.raises(ValueError, match=f"The new attribution needs {key}"):
        co.correct([], b)


def test_correct_refuses_events_of_another_workload_and_layers_of_another_run_or_twice():
    a = at.attribute(run(), measured(10), [opt("o1", measured(6))])
    b = at.attribute(run(), measured(10), [opt("o1", measured(7))])
    other = [dict(a["layers"][0], workload_key="nb:other")]
    with pytest.raises(ValueError, match="is for workload nb:other, not nb:sales"):
        co.correct(other, b)
    stray = dict(b, layers=[dict(b["layers"][0], execution_id="e2")])
    with pytest.raises(ValueError, match="of the new attribution is for another run"):
        co.correct(a["layers"], stray)
    twice = dict(b, layers=[b["layers"][0], dict(b["layers"][0])])
    with pytest.raises(ValueError, match="has layer o1 twice"):
        co.correct(a["layers"], twice)


def latest(*layers: dict, wk: str = "w") -> dict:
    return {"execution_id": "e1", "workload_key": wk, "baseline_version": "b1", "layers": list(layers)}


def test_a_layer_gone_from_the_new_attribution_is_reversed_like_its_first_accrual_with_the_reason():
    earlier = [accrual(workload_key="w", optimization_id="o1", layer_order=3),
               fix(workload_key="w", optimization_id="oX", layer_order=9)]
    out = co.correct(earlier, latest(), reason="dropped")
    assert len(out) == 1 and out[0]["event_type"] == co.REVERSAL
    assert out[0]["optimization_id"] == "o1" and out[0]["layer_order"] == 3
    assert out[0]["reason"] == "No longer part of this run's attribution (was 0 CU-hours). dropped"
    out = co.correct([accrual(workload_key="w", optimization_id="o1")], latest())
    assert out[0]["optimization_id"] == "o1"
    assert out[0]["reason"] == "No longer part of this run's attribution (was 1 CU-hours)"


def test_corrections_that_correct_each_other_in_a_circle_are_reported():
    c1 = fix(ledger_event_id="c1", corrects="c2")
    c2 = fix(ledger_event_id="c2", corrects="c1")
    _, problems = co._chains([c1, c2])
    assert any("leads back to itself" in p for p in problems)


def test_figures_are_kept_to_six_decimal_places():
    chains, _ = co._chains([accrual(cu_hours=1.0000004)])
    assert chains[("e1", "o1")]["counted"] == 1.0
    out = co.correct([accrual(workload_key="w")], latest(accrual(ledger_event_id="n1", cu_hours=2.0000004)))
    assert out[0]["cu_hours"] == 1.0 and out[0]["result_cu_hours"] == 2.0
    assert co._difference({"value": 1.0000004}, {"value": 1.0}) == 0.0
    report = co.reconcile([accrual(cu_hours=1.0000004)],
                          [{"execution_id": "e1", "layers": [accrual(cu_hours=1.0000008)]}])
    assert report["runs"][0]["status"] == "matched"
    report = co.reconcile([accrual(cu_hours=3.0000004)], [{"execution_id": "e1", "layers": [accrual(cu_hours=1.0)]}])
    assert report["runs"][0]["mismatches"][0]["difference"] == 2.0


def test_a_layer_whose_figure_was_withdrawn_gets_the_new_figure_less_what_is_left():
    # what is left after the withdrawal is 1e-6, within the tolerance, so the chain adds up
    gone = fix(cu_hours=-0.999999, result_classification=ms.UNKNOWN, result_cu_hours=None, workload_key="w")
    earlier = [accrual(workload_key="w"), gone]
    chains, problems = co._chains(earlier)
    assert problems == [] and chains[("e1", "o1")]["counted"] == 0.000001
    out = co.correct(earlier, latest(accrual(ledger_event_id="n1", cu_hours=5.0)))
    assert out[0]["cu_hours"] == 4.999999 and out[0]["result_cu_hours"] == 5.0


def test_a_change_of_exactly_the_tolerance_is_no_change():
    assert co.correct([accrual(workload_key="w", cu_hours=0.0)],
                      latest(accrual(ledger_event_id="n1", cu_hours=0.000001))) == []
    report = co.reconcile([accrual(cu_hours=0.0)], [{"execution_id": "e1", "layers": [accrual(cu_hours=0.000001)]}])
    assert report["runs"][0]["status"] == "matched" and report["matched"] == 1 and report["reconciled"]


def test_zero_is_a_valid_accrual_and_a_valid_regression():
    assert co._check(accrual(cu_hours=0.0))["cu_hours"] == 0.0
    assert co._check(accrual(event_type=co.REGRESSION, cu_hours=0.0))["cu_hours"] == 0.0


def test_new_layers_come_in_layer_order_with_layers_without_one_last():
    layers = [accrual(ledger_event_id=n, layer=n, layer_order=o)
              for n, o in (("c", None), ("a", 1), ("b", 0), ("e", 5), ("d", 5))]
    out = co.correct([], latest(*layers))
    assert [e["layer"] for e in out] == ["b", "a", "d", "e", "c"]


def test_reversals_come_in_layer_order():
    earlier = [accrual(ledger_event_id="a1", layer="a", layer_order=1),
               accrual(ledger_event_id="b1", layer="b", layer_order=0)]
    assert [e["layer"] for e in co.reverse(earlier, reason="never deployed")] == ["b", "a"]


def test_reconcile_names_each_kind_of_layer_difference():
    ledger = [accrual(ledger_event_id="a1", layer="o1"), accrual(ledger_event_id="a2", layer="o2"),
              accrual(ledger_event_id="a4", layer="o4", classification=ms.UNKNOWN, cu_hours=None)]
    expected = [{"execution_id": "e1", "layers": [accrual(layer="o1"), accrual(layer="o3"), accrual(layer="o4")]}]
    report = co.reconcile(ledger, expected)
    kinds = {m["layer"]: m["kind"] for m in report["runs"][0]["mismatches"]}
    assert kinds == {"o2": "not_expected", "o3": "not_in_ledger", "o4": "unknown_in_ledger"}
    o4 = next(m for m in report["runs"][0]["mismatches"] if m["layer"] == "o4")
    assert o4["difference"] is None and o4["expected_cu_hours"] == 1.0
    assert report["matched"] == 0 and report["mismatched"] == 1


def test_a_run_only_in_the_ledger_keeps_the_ledgers_workload():
    report = co.reconcile([accrual(workload_key="nb:sales")], [])
    assert report["runs"][0]["workload_key"] == "nb:sales" and report["runs"][0]["status"] == "not_expected"
    report = co.reconcile([accrual(workload_key="nb:sales")],
                          [{"execution_id": "e1", "workload_key": "nb:new", "layers": [accrual()]}])
    assert report["runs"][0]["workload_key"] == "nb:new"


def test_problems_name_the_run_and_the_layer():
    _, problems = co._chains([accrual(ledger_event_id="a1"), accrual(ledger_event_id="a2")])
    assert problems == [("Run e1 has 2 separate accruals for layer o1 (a1, a2); one of them is counted twice and "
                         "needs a reversal.")]
    _, problems = co._chains([accrual(), fix(result_cu_hours=5.0)])
    assert problems == ["Layer o1 of run e1 adds up to 0 CU-hours, but its latest event says 5."]
