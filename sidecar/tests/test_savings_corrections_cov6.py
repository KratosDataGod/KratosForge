"""Corrections, reversals and reconciliation (savings/corrections.py), the paths the other tests leave: events
missing a part or with a classification the ledger doesn't know, corrections that don't say what their layer is
afterwards, corrections in a circle or across runs, a new attribution that is incomplete, for another workload
or run, or has a layer twice, and reconciliation of an ambiguous Unknown chain, a missing layer and a
classification that differs. Plain events, as the ledger holds them."""

from __future__ import annotations

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import corrections as co


def accrual(eid="a1", run="e1", layer="o1", cu=5.0, cls=ms.MEASURED, **kw):
    return {"ledger_event_id": eid, "execution_id": run, "workload_key": "nb:sales", "baseline_version": "b1",
            "layer": layer, "event_type": co.ACCRUAL, "classification": cls, "cu_hours": cu, **kw}


def correction(eid, corrects, cu, result, result_cls=ms.MEASURED, **kw):
    return accrual(eid, cu=cu, event_type=co.CORRECTION, corrects=corrects, result_cu_hours=result,
                   result_classification=result_cls, **kw)


@pytest.mark.parametrize("missing", ["ledger_event_id", "execution_id", "layer"])
def test_an_event_without_its_id_run_or_layer_is_refused(missing):
    event = accrual(**{missing: ""})
    with pytest.raises(ValueError, match=f"^A ledger event needs {missing} "):
        co.reconcile([event], [])


def test_an_event_with_a_classification_the_ledger_doesnt_know_is_refused():
    with pytest.raises(ValueError, match="^Event a1 has unknown classification 'guessed'.$"):
        co.reconcile([accrual(cls="guessed")], [])


@pytest.mark.parametrize("result_cls", [ms.SCENARIO, "guessed", None])
def test_a_correction_that_doesnt_say_what_its_layer_is_afterwards_is_refused(result_cls):
    with pytest.raises(ValueError, match="^Correction c1 doesn't say what its layer is afterwards.$"):
        co.reconcile([accrual(), correction("c1", "a1", 1.0, 6.0, result_cls)], [])


@pytest.mark.parametrize(("result", "result_cls", "removed"), [
    (None, ms.MEASURED, False),     # a known layer with no figure
    (6.0, ms.UNKNOWN, False),       # an Unknown layer with one
    (6.0, ms.MEASURED, True),       # a removed layer with one
])
def test_a_result_figure_that_doesnt_fit_its_classification_is_refused(result, result_cls, removed):
    with pytest.raises(ValueError, match="^Correction c1 has a result figure that doesn't fit its classification.$"):
        co.reconcile([accrual(), correction("c1", "a1", 1.0, result, result_cls, layer_removed=removed)], [])


def test_corrections_that_correct_each_other_in_a_circle_are_reported():
    out = co.reconcile([correction("c1", "c2", 1.0, 1.0), correction("c2", "c1", 1.0, 1.0)], [])
    assert any("Correction c1 corrects event c2, which isn't in the ledger (or leads back to itself)" in p
               for p in out["problems"])
    assert not out["reconciled"]


def test_a_correction_of_another_runs_event_is_reported():
    out = co.reconcile([accrual(), correction("c1", "a1", 1.0, 6.0, execution_id="e2")], [])
    assert "Event c1 corrects an event of another run or layer." in out["problems"]


def _latest(**changes):
    out = {"execution_id": "e1", "workload_key": "nb:sales", "baseline_version": "b1",
           "layers": [accrual("n1", cu=6.0)]}
    return out | changes


@pytest.mark.parametrize("key", ["execution_id", "workload_key", "baseline_version", "layers"])
def test_a_new_attribution_missing_a_part_is_refused(key):
    with pytest.raises(ValueError, match=f"^The new attribution needs {key}.$"):
        co.correct([accrual()], _latest(**{key: None}))


def test_events_for_another_workload_are_refused():
    with pytest.raises(ValueError, match="^Event a1 is for workload nb:other, not nb:sales.$"):
        co.correct([accrual(workload_key="nb:other")], _latest())


def test_a_new_attribution_with_another_runs_layer_or_a_layer_twice_is_refused():
    with pytest.raises(ValueError, match="^Layer o1 of the new attribution is for another run.$"):
        co.correct([accrual()], _latest(layers=[accrual("n1", run="e2")]))
    with pytest.raises(ValueError, match="^The new attribution has layer o1 twice.$"):
        co.correct([accrual()], _latest(layers=[accrual("n1"), accrual("n2")]))


def test_the_same_figures_correct_nothing():
    assert co.correct([accrual()], _latest(layers=[accrual("n1")])) == []


def test_an_ambiguous_unknown_chain_counts_as_unknown():
    # Two accruals for one layer: no head, so the ledger counts what it holds, as weak as its weakest.
    events = [accrual("a1", cu=None, cls=ms.UNKNOWN), accrual("a2", cu=None, cls=ms.UNKNOWN)]
    out = co.reconcile(events, [{"execution_id": "e1", "workload_key": "nb:sales",
                                 "layers": [accrual("x", cu=None, cls=ms.UNKNOWN)]}])
    assert any("has 2 separate accruals for layer o1" in p for p in out["problems"])
    [run] = out["runs"]
    assert run["status"] == "matched" and run["ledger"]["value"] is None


def test_reconciling_names_a_layer_on_one_side_only_and_a_classification_that_differs():
    events = [accrual("a1", layer="o1", cu=5.0), accrual("a2", layer="o2", cu=2.0)]
    expected = [{"execution_id": "e1", "workload_key": "nb:sales",
                 "layers": [accrual("x1", layer="o1", cu=5.0, cls=ms.ESTIMATED), accrual("x3", layer="o3", cu=1.0)]}]
    out = co.reconcile(events, expected)
    [run] = out["runs"]
    assert run["status"] == "mismatched" and not out["reconciled"]
    assert [(m["layer"], m["kind"]) for m in run["mismatches"]] == [
        ("o1", "classification"), ("o2", "not_expected"), ("o3", "not_in_ledger")]
    o1, o2, o3 = run["mismatches"]
    assert (o1["difference"], o1["ledger_classification"], o1["expected_classification"]) == (0.0, ms.MEASURED, ms.ESTIMATED)
    assert (o2["ledger_cu_hours"], o2["expected_cu_hours"], o2["difference"]) == (2.0, None, None)
    assert (o3["ledger_cu_hours"], o3["expected_cu_hours"]) == (None, 1.0)


def test_a_correction_without_layer_removed_is_read_as_not_removed():
    # layer_removed is optional on a correction read back from the ledger: a known result with no
    # layer_removed fits (it was refused as "doesn't fit its classification" when the key was absent).
    out = co.reconcile([accrual(), correction("c1", "a1", 1.0, 6.0)],
                       [{"execution_id": "e1", "workload_key": "nb:sales", "layers": [accrual("x", cu=6.0)]}])
    assert out["problems"] == [] and out["reconciled"]
    assert out["runs"][0]["ledger"]["value"] == 6.0
