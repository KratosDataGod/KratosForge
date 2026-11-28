"""savings/exports: the edges the earlier tests left open (mutation batch 478). Each report's counts, periods,
notes and row order, which realized result readiness calls the latest, and the checks on other modules'
results. Realized results here are the smallest dicts the checks take; nothing reads a file."""

from __future__ import annotations

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import exports as ex
from fabric_desktop.savings import realized as rz
from fabric_desktop.savings import recurring as rc
from tests.test_savings_exports import body, estimated, fabric, ledger, measured, opt, pc, run, unknown, written


def real(value, *, cap="c1", start="2026-11-01T00:00:00Z", end="2026-11-30T00:00:00Z", currency="USD",
         method="bill", not_claimed=None):
    """A realized.realize result as the checks read it: realized, with a Measured figure."""
    r = {"realized": True, "billing_capacity_id": cap, "after": {"start": start, "end": end},
         "realized_savings": {"metric": rz.METRIC, "classification": ms.MEASURED, "value": value,
                              "currency": currency, "method": method}}
    if not_claimed is not None:
        r["not_claimed"] = {"value": not_claimed, "classification": ms.MEASURED, "currency": currency}
    return r


def not_real(*, cap="c1", start="2026-11-01T00:00:00Z", end="2026-11-30T00:00:00Z", reason="no bill drop"):
    return {"realized": False, "billing_capacity_id": cap, "after": {"start": start, "end": end}, "reason": reason,
            "realized_savings": {"metric": rz.METRIC, "classification": ms.UNKNOWN, "value": None}}


def priced(*runs, scenario="List price"):
    return {"scenario": scenario, "runs": [{"execution_id": f"r{i}", "at": at_, "value": {
        "classification": ms.SCENARIO, "value": v, "currency": cur}} for i, (at_, v, cur) in enumerate(runs)]}


def iso(day):
    return f"2026-11-{day:02d}T00:00:00+00:00"


# ---- the shared reads

def test_missing_sorts_last_and_the_rest_as_text():
    assert ex._key(None) == (True, "") and ex._key("") == (True, "")
    assert ex._key("a") == (False, "a") and ex._key(3) == (False, "3")


def test_a_layer_is_corrected_only_once_a_correction_follows_it():
    from fabric_desktop.savings import corrections as co
    first = written(pc("p1", "2026-11-04T10:00:00Z", estimated(7)))
    fix = co.correct(first, pc("p1", "2026-11-04T10:00:00Z", estimated(5)), reason="Calibration revised")
    assert [r["corrected"] for r in ex.layers(first)[0]] == [False]
    assert [r["corrected"] for r in ex.layers(first + fix)[0]] == [True]


def test_workspace_rows_count_runs_and_layers():
    rows = body(ex.workspace(ledger()))
    got = [(r["Workspace"], r["Classification"], r[ex.CU_AVOIDED], r["Runs"], r["Layers"]) for r in rows]
    assert got == [("w1", "Measured", "4", "1", "1"), ("w1", "Estimated", "7", "1", "1"),
                   ("w1", "Unknown", "", "1", "1"), ("w2", "Estimated", "2", "1", "1")]


def test_two_layers_of_one_run_count_as_one_run_and_two_layers():
    two = at.attribute(run("f2", "2026-11-03T10:00:00Z", at.FABRIC), measured(10),
                       [opt("o1", measured(6)), opt("o2", measured(5), "2026-10-02T00:00:00Z")])
    rows = body(ex.workspace(written(two)))
    assert [(r[ex.CU_AVOIDED], r["Runs"], r["Layers"]) for r in rows] == [("5", "1", "2")]
    audit = body(ex.audit_history(written(two)))  # two chains with one run time sort by run and layer
    assert [r["Layer"] for r in audit] == ["o1", "o2"]
    assert [r["Optimization"] for r in audit] == ["o1", "o2"]
    assert [r["Layer removed"] for r in audit] == ["No", "No"]
    assert [r["Layer after CU hours"] for r in audit] == ["4", "1"]


def test_money_rows_round_to_6_places_and_sort_by_currency_in_a_classification():
    rows = ex._money_rows("M", [("USD", ms.SCENARIO, 0.1234567), ("EUR", ms.SCENARIO, 1.0)], lambda c: "")
    assert [(r[3], r[1]) for r in rows] == [("EUR", 1.0), ("USD", 0.123457)]


# ---- checks on the other modules' results

def test_a_realized_result_that_isnt_one_is_named_by_its_place():
    with pytest.raises(ValueError, match=r"^Realized result 1 isn't a realized.realize result"):
        ex.executive_summary([], realized=[{"realized": True}])
    bad = real(5)
    bad["realized_savings"]["classification"] = ms.ESTIMATED
    with pytest.raises(ValueError, match=r"^Realized result 2 says realized is True with a estimated figure"):
        ex.executive_summary([], realized=[real(1), bad])


def test_the_after_period_comes_before_the_figure_period():
    r = real(5, start="2026-11-09T00:00:00Z", end="2026-11-16T00:00:00Z")
    r["realized_savings"]["period"] = {"start": "2026-10-01T00:00:00Z", "end": "2026-10-08T00:00:00Z"}
    assert ex._realized_period(r) == ("2026-11-09T00:00:00+00:00", "2026-11-16T00:00:00+00:00")


def test_the_capacity_is_the_billing_one_then_the_result_then_the_figure():
    r = real(5, cap="bill-cap")
    r["capacity_id"] = "fabric-cap"
    assert ex._realized_capacity(r) == "bill-cap"
    r.pop("billing_capacity_id")
    assert ex._realized_capacity(r) == "fabric-cap"
    r.pop("capacity_id")
    r["realized_savings"]["capacity_id"] = "figure-cap"
    assert ex._realized_capacity(r) == "figure-cap"


def test_capacity_value_must_be_a_value_runs_result_with_priced_runs():
    with pytest.raises(ValueError, match="pricing.value_runs result"):
        ex.executive_summary([], capacity_value={"runs": []})
    with pytest.raises(ValueError, match="priced value"):
        ex.executive_summary([], capacity_value={"scenario": "S", "runs": [{"value": 5}]})


def test_a_projection_that_isnt_a_recurring_result_is_refused():
    good = {"metric": rc.METRIC, "classification": ms.ESTIMATED, "value": 3.0}
    with pytest.raises(ValueError, match="isn't a recurring.project result"):
        ex.executive_summary([], projections=[{"workload_key": "w", "projection": good | {"metric": "x"}}])
    with pytest.raises(ValueError, match="isn't a recurring.project result"):
        ex.executive_summary([], projections=[{"workload_key": "w", "projection": good | {
            "classification": ms.MEASURED}}])


def test_the_latest_validation_wins_whatever_order_they_come_in():
    vals = [{"validation_id": "v2", "optimization_id": "o1", "result": "regressed", "at": "2026-11-10T00:00:00Z"},
            {"validation_id": "v1", "optimization_id": "o1", "result": "verified", "at": "2026-11-01T00:00:00Z"}]
    assert ex._validations(vals)["o1"]["validation_id"] == "v2"


# ---- the reports

def test_the_summary_counts_no_realized_results_as_0_and_notes_a_measured_one():
    rows = body(ex.executive_summary([]))
    [none] = [r for r in rows if r["Measure"] == ex.REALIZED]
    assert none["Count"] == "0" and none["Value"] == ""
    [yes] = [r for r in body(ex.executive_summary([], realized=[real(5)])) if r["Measure"] == ex.REALIZED]
    assert yes["Note"] == "From a bill and a person's confirmation."


def test_capacity_rows_carry_the_period_capacity_and_counts_and_sort_by_measure_then_start():
    rows = body(ex.capacity(ledger(), realized=[real(5, cap="c1", start="2026-10-01T00:00:00Z", not_claimed=2.0)]))
    got = [(r["Capacity"], r["Measure"], r["Classification"], r["Count"], r["Period start"], r["Period end"])
           for r in rows]
    assert got == [
        ("c1", ex.CU_AVOIDED, "Estimated", "2", "2026-10-31T04:30:00+00:00", "2026-11-04T10:00:00+00:00"),
        ("c1", ex.CU_AVOIDED, "Measured", "1", "2026-11-03T10:00:00+00:00", "2026-11-03T10:00:00+00:00"),
        ("c1", ex.CU_AVOIDED, "Unknown", "1", "2026-11-05T10:00:00+00:00", "2026-11-05T10:00:00+00:00"),
        ("c1", ex.REALIZED, "Measured", "1", "2026-10-01T00:00:00+00:00", "2026-11-30T00:00:00+00:00"),
        ("c1", ex.NOT_CLAIMED, "Measured", "1", "2026-10-01T00:00:00+00:00", "2026-11-30T00:00:00+00:00")]


def test_realized_rows_in_one_capacity_sort_by_period_start():
    rows = body(ex.capacity([], realized=[real(1, start=iso(15), end=iso(16)), real(2, start=iso(1), end=iso(30))]))
    assert [r["Value"] for r in rows] == ["2", "1"]


def test_an_unrealized_result_notes_its_reason():
    [row] = body(ex.capacity([], realized=[not_real(reason="the drop wasn't confirmed")]))
    assert row["Note"] == "the drop wasn't confirmed"
    r = not_real(reason="")
    r["realized_savings"]["reason"] = "from the figure"
    assert body(ex.capacity([], realized=[r]))[0]["Note"] == "from the figure"


def test_the_monthly_statement_notes_and_orders_priced_runs():
    rows = body(ex.monthly_statement([], capacity_value=priced(
        ("2026-11-03T00:00:00Z", 1.0, "USD"), ("2026-11-04T00:00:00Z", 1.0, "EUR"),
        ("2026-11-05T00:00:00Z", 1.0, "EUR"))))
    assert [(r["Unit"], r["Count"]) for r in rows] == [("EUR", "2"), ("USD", "1")]
    assert {r["Note"] for r in rows} == {"Scenario List price. " + ex.PRICE_NOTE}


def test_a_validation_without_runs_is_listed_with_no_runs_or_layers():
    vals = [{"validation_id": "v9", "optimization_id": "o9", "result": "verified", "at": "2026-11-10T00:00:00Z"}]
    [row] = [r for r in body(ex.estimated_vs_verified([], validations=vals))]
    assert (row["Optimization"], row["Runs"], row["Layers"], row["Classification"]) == ("o9", "0", "0", "Unknown")


def test_sources_without_an_optimization_sort_by_source_before_classification():
    events = written(pc("p2", "2026-11-05T10:00:00Z", unknown()))
    other = written(pc("p9", "2026-11-06T10:00:00Z", estimated(3)))
    for e in other:
        e["layer"] = at.UNATTRIBUTED
    rows = body(ex.estimated_vs_verified(events + other))
    assert [(r["Source"], r["Classification"]) for r in rows] == [("This PC", "Unknown"),
                                                                    ("Unattributed", "Estimated")]


def test_readiness_counts_a_verified_optimization_after_estimated_cu_avoided_with_its_note():
    events = written(pc("p1", "2026-11-04T10:00:00Z", estimated(7))) + written(fabric("f1", "2026-11-03T10:00:00Z"))
    vals = [{"validation_id": "v1", "optimization_id": "o1", "result": "verified", "at": "2026-11-10T00:00:00Z"}]
    rows = body(ex.capacity_readiness(events, validations=vals))
    got = [(r["Evidence"], r["Classification"], r["Count"], r["Note"]) for r in rows]
    assert got == [(ex.CU_AVOIDED, "Measured", "1", ""), (ex.CU_AVOIDED, "Estimated", "1", ""),
                   ("Optimizations verified", "Measured", "1", "Measured before and after on the same inputs."),
                   (ex.REALIZED, "Unknown", "0", ex.NO_BILL), (ex.HEADROOM, "Unknown", "0", ex.NO_HEADROOM)]


def test_readiness_takes_the_realized_result_whose_after_period_ends_last():
    late_end = real(2, start=iso(1), end=iso(30), currency="EUR", method="sku change")
    late_start = real(1, start=iso(20), end=iso(21))
    rows = body(ex.capacity_readiness([], realized=[late_start, late_end]))
    [row] = [r for r in rows if r["Evidence"] == ex.REALIZED]
    assert (row["Value"], row["Unit"], row["Count"]) == ("2", "EUR", "1")
    assert row["Note"] == f"Latest after period {iso(1)} to {iso(30)}. sku change"
    rows = body(ex.capacity_readiness([], realized=[late_end, late_start]))
    assert [r["Value"] for r in rows if r["Evidence"] == ex.REALIZED] == ["2"]


def test_readiness_keeps_the_first_of_two_results_ending_together():
    rows = body(ex.capacity_readiness([], realized=[real(1, end=iso(30)), real(2, end=iso(30))]))
    assert [r["Value"] for r in rows if r["Evidence"] == ex.REALIZED] == ["1"]


def test_readiness_without_an_after_start_notes_only_the_method():
    r = real(1, start=None)
    r["realized_savings"]["period"] = {}
    [row] = [x for x in body(ex.capacity_readiness([], realized=[r])) if x["Evidence"] == ex.REALIZED]
    assert row["Note"] == "bill"


def test_a_row_of_the_wrong_width_is_refused():
    with pytest.raises(ValueError, match="^A workspace row has 1 cells, not 2.$"):
        ex.to_csv({"report": "workspace", "columns": ["a", "b"], "rows": [[1]], "notes": []})
