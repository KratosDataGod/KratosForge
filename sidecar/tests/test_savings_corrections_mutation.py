"""Mutation tests for savings/corrections.py: the behaviour its other tests left loose. Which classification is
the weaker, a zero accrual, the tolerance at exactly 1e-6 (a chain that adds up, a change too small to write,
a reconciled amount), rounding to six places, the order events come out in, the notes each event carries, what
a reversal copies from the chain, and the counts and differences reconciliation reports."""

from __future__ import annotations

from fabric_desktop import measurement as ms
from fabric_desktop.savings import corrections as co


def accrual(eid="a1", run="e1", layer="o1", cu=5.0, cls=ms.MEASURED, **kw):
    return {"ledger_event_id": eid, "execution_id": run, "workload_key": "nb:sales", "baseline_version": "b1",
            "layer": layer, "event_type": co.ACCRUAL, "classification": cls, "cu_hours": cu, **kw}


def correction(eid, corrects, cu, result, result_cls=ms.MEASURED, **kw):
    return accrual(eid, cu=cu, event_type=co.CORRECTION, corrects=corrects, result_cu_hours=result,
                   result_classification=result_cls, layer_removed=False, **kw)


def latest(*layers):
    return {"execution_id": "e1", "workload_key": "nb:sales", "baseline_version": "b1", "layers": list(layers)}


def expected(*layers, run="e1"):
    return {"execution_id": run, "workload_key": "nb:sales", "layers": list(layers)}


def test_a_measured_figure_corrected_by_an_estimate_is_as_weak_as_the_estimate():
    [fix] = co.correct([accrual(cu=5.0)], latest(accrual("n1", cu=6.0, cls=ms.ESTIMATED)))
    assert (fix["classification"], fix["cu_hours"]) == (ms.ESTIMATED, 1.0)
    [fix] = co.correct([accrual(cu=5.0, cls=ms.ESTIMATED)], latest(accrual("n1", cu=6.0)))
    assert fix["classification"] == ms.ESTIMATED and fix["result_classification"] == ms.MEASURED


def test_an_accrual_of_zero_is_a_figure_the_ledger_takes():
    out = co.reconcile([accrual(cu=0.0)], [expected(accrual("x", cu=0.0))])
    assert out["reconciled"] and out["problems"] == []


def test_a_chain_off_by_exactly_the_tolerance_still_adds_up():
    # 0 + 0.000001 is 1e-6 from what its latest event says (0): within the tolerance, not past it.
    out = co.reconcile([accrual(cu=0.0), correction("c1", "a1", 0.000001, 0.0)], [])
    assert out["problems"] == []


def test_a_chain_that_doesnt_add_up_names_its_layer_and_run():
    out = co.reconcile([accrual(cu=5.0), correction("c1", "a1", 1.0, 7.0)], [])
    assert out["problems"] == ["Layer o1 of run e1 adds up to 6 CU-hours, but its latest event says 7."]


def test_a_change_of_exactly_the_tolerance_writes_nothing():
    assert co.correct([accrual(cu=0.0)], latest(accrual("n1", cu=0.000001))) == []


def test_figures_are_kept_to_six_places():
    [fix] = co.correct([accrual(cu=5.0)], latest(accrual("n1", cu=1.0000004)))
    assert fix["result_cu_hours"] == 1.0 and fix["cu_hours"] == -4.0
    # What a chain counts is rounded to six places too, and reconciliation shows it as counted.
    out = co.reconcile([accrual(cu=1.0), correction("c1", "a1", 0.0000004, 1.0000004)],
                       [expected(accrual("x", cu=3.0))])
    [m] = out["runs"][0]["mismatches"]
    assert m["kind"] == "amount" and m["ledger_cu_hours"] == 1.0 and m["difference"] == -2.0


def test_each_correction_says_what_the_layer_was_and_is_now_and_why():
    [fix] = co.correct([accrual(cu=5.0)], latest(accrual("n1", cu=6.0)), reason="revised export")
    assert fix["reason"] == "Re-attributed: was 5 CU-hours, now 6 CU-hours. revised export"
    [fix] = co.correct([accrual(cu=5.0)], latest(accrual("n1", cu=6.0)))
    assert fix["reason"] == "Re-attributed: was 5 CU-hours, now 6 CU-hours"


def test_a_layer_no_longer_attributed_is_reversed_with_its_note_and_its_accruals_optimization():
    earlier = [accrual(cu=5.0, optimization_id="opt-1", layer_order=2),
               correction("c1", "a1", 1.0, 6.0)]
    [gone] = co.correct(earlier, latest(), reason="o1 never deployed")
    assert gone["event_type"] == co.REVERSAL and gone["cu_hours"] == -6.0 and gone["layer_removed"]
    assert gone["reason"] == "No longer part of this run's attribution (was 6 CU-hours). o1 never deployed"
    assert gone["optimization_id"] == "opt-1" and gone["layer_order"] == 2
    [gone] = co.correct(earlier, latest())
    assert gone["reason"] == "No longer part of this run's attribution (was 6 CU-hours)"


def test_corrections_come_out_in_layer_order_with_unordered_layers_last():
    earlier = [accrual("a1", layer="z", cu=1.0), accrual("a2", layer="a", cu=1.0), accrual("a3", layer="m", cu=1.0)]
    fixes = co.correct(earlier, latest(accrual("n1", layer="z", cu=2.0, layer_order=0),
                                       accrual("n2", layer="a", cu=2.0, layer_order=1),
                                       accrual("n3", layer="m", cu=2.0)))
    assert [f["layer"] for f in fixes] == ["z", "a", "m"]


def test_reversals_come_out_in_layer_order():
    earlier = [accrual("a1", layer="z", cu=1.0, layer_order=0), accrual("a2", layer="a", cu=1.0, layer_order=1)]
    assert [r["layer"] for r in co.reverse(earlier, reason="never deployed")] == ["z", "a"]


def test_an_amount_exactly_the_tolerance_apart_matches():
    out = co.reconcile([accrual(cu=0.000001)], [expected(accrual("x", cu=0.0))])
    assert out["runs"][0]["status"] == "matched" and out["matched"] == 1 and out["reconciled"]


def test_an_unknown_ledger_figure_against_a_known_one_has_no_difference():
    out = co.reconcile([accrual(cu=None, cls=ms.UNKNOWN)], [expected(accrual("x", cu=5.0))])
    [m] = out["runs"][0]["mismatches"]
    assert (m["kind"], m["difference"], m["expected_cu_hours"]) == ("unknown_in_ledger", None, 5.0)
    assert out["matched"] == 0 and out["mismatched"] == 1


def test_layers_in_the_same_place_come_out_by_name():
    earlier = [accrual("a1", layer="b", cu=1.0), accrual("a2", layer="a", cu=1.0)]
    fixes = co.correct(earlier, latest(accrual("n1", layer="b", cu=2.0, layer_order=3),
                                       accrual("n2", layer="a", cu=2.0, layer_order=3)))
    assert [f["layer"] for f in fixes] == ["a", "b"]
