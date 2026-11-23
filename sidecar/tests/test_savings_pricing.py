"""ESI-401: versioned pricing scenarios. A price change applies only from its effective date, a run
keeps the version it was priced at, and the value is a Scenario figure, never savings."""

from __future__ import annotations

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import pricing as pr

LIST = "List price"


def v(rate, start, end=None, **kw):
    return pr.scenario(scenario=kw.pop("name", LIST), rate_per_cu_hour=rate, currency=kw.pop("currency", "USD"),
                       effective_from=start, effective_to=end, pricing_model=kw.pop("model", pr.PAY_AS_YOU_GO),
                       confidence=kw.pop("confidence", "confirmed"), source="Azure price sheet", **kw)


OLD = v(0.18, "2026-01-01T00:00:00Z", "2026-11-15T00:00:00Z")
NEW = v(0.20, "2026-11-15T00:00:00Z")


def run(eid, when, avoided=6.0):
    r = {"execution_id": eid, "workload_key": "nb:sales", "baseline_version": "b1", "at": when, "ran": at.PC}
    base = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, 10, run_id="r", source="Capacity Metrics export",
                   collected_at="2026-11-01T00:00:00Z")
    local = (ms.make(ms.ESTIMATED, "fabric_job_cu_avoided", ms.CU_HOURS, avoided, method="calibration",
                     assumptions=["median"]) if avoided is not None else None)
    return at.attribute(r, base, local_avoided=local, overhead=ms.make(ms.MEASURED, "overhead", ms.CU_HOURS, 0,
                                                                       source="sync", collected_at="2026-11-01"))


def test_a_price_change_applies_only_from_its_effective_date():
    before = pr.value(10, "2026-11-14T23:59:59Z", [OLD, NEW], LIST)
    on = pr.value(10, "2026-11-15T00:00:00Z", [OLD, NEW], LIST)
    assert before["value"] == pytest.approx(1.8) and before["pricing_version"] == OLD["pricing_version"]
    assert on["value"] == pytest.approx(2.0) and on["pricing_version"] == NEW["pricing_version"]


def test_history_keeps_its_pricing_version():
    runs = [run("e1", "2026-11-10T00:00:00Z"), run("e2", "2026-11-20T00:00:00Z")]
    out = pr.value_runs(runs, [OLD, NEW], LIST)
    by_run = {x["execution_id"]: x["value"] for x in out["runs"]}
    assert by_run["e1"]["pricing_version"] == OLD["pricing_version"] and by_run["e1"]["value"] == pytest.approx(1.08)
    assert by_run["e2"]["pricing_version"] == NEW["pricing_version"] and by_run["e2"]["value"] == pytest.approx(1.2)
    assert out["total"]["value"] == pytest.approx(2.28) and out["total"]["currency"] == "USD"
    assert out["pricing_versions"] == sorted([OLD["pricing_version"], NEW["pricing_version"]])
    # Adding a later price changes nothing already priced.
    later = v(0.25, "2026-12-01T00:00:00Z")
    new_end = v(0.20, "2026-11-15T00:00:00Z", "2026-12-01T00:00:00Z")
    again = pr.value_runs(runs, [OLD, new_end, later], LIST)
    assert again["total"]["value"] == pytest.approx(2.28)


def test_the_value_is_a_scenario_figure_and_never_adds_to_measured_ones():
    m = pr.value(10, "2026-11-20T00:00:00Z", [NEW], LIST)
    assert m["classification"] == ms.SCENARIO and m["unit"] == ms.CURRENCY
    assert any("not a change in what was billed" in a for a in m["assumptions"])
    measured_money = ms.make(ms.MEASURED, pr.METRIC, ms.CURRENCY, 5, currency="USD", source="Cost Management",
                             collected_at="2026-11-01")
    with pytest.raises(ValueError, match="scenario"):
        ms.total([m, measured_money], pr.METRIC, ms.CURRENCY, currency="USD")


def test_the_same_version_from_two_pcs_is_one_version():
    assert v(0.18, "2026-01-01T00:00:00Z")["pricing_version"] == v(0.18, "2026-01-01T00:00Z")["pricing_version"]
    assert v(0.18, "2026-01-01T00:00:00Z")["pricing_version"] != v(0.19, "2026-01-01T00:00:00Z")["pricing_version"]
    pr.check([NEW, dict(NEW)])  # a duplicate isn't an overlap


def test_the_single_price_today_becomes_an_assumed_scenario():
    d = pr.from_settings({"price_per_cu_hour": 0.21, "currency": "eur"})
    assert d["scenario"] == pr.DEFAULT_SCENARIO and d["pricing_model"] == pr.ASSUMPTION
    assert d["confidence"] == "assumption" and d["rate_per_cu_hour"] == 0.21 and d["currency"] == "EUR"
    assert pr.from_settings()["rate_per_cu_hour"] == 0.18
    m = pr.value(1, "2026-11-20T00:00:00Z", [d], pr.DEFAULT_SCENARIO)
    assert "(assumption, assumption)" in m["assumptions"][0]


def test_the_rows_match_the_lakehouse_table():
    from fabric_desktop.optimize import schema
    assert set(NEW) == set(schema.TABLES_V2["pricing"])


def test_overlaps_and_mixed_currencies_are_refused():
    with pytest.raises(ValueError, match="overlap"):
        pr.check([v(0.18, "2026-01-01T00:00:00Z"), v(0.20, "2026-11-15T00:00:00Z")])  # first never ends
    with pytest.raises(ValueError, match="overlap"):
        pr.check([v(0.18, "2026-01-01T00:00:00Z", "2026-11-16T00:00:00Z"), NEW])
    with pytest.raises(ValueError, match="currencies"):
        pr.check([OLD, v(0.17, "2026-11-15T00:00:00Z", currency="EUR")])
    # Different scenarios, SKUs or regions don't overlap each other.
    pr.check([v(0.18, "2026-01-01T00:00:00Z"), v(0.15, "2026-01-01T00:00:00Z", name="Reserved", model=pr.RESERVED)])


def test_a_sku_or_region_price_beats_a_general_one():
    general = v(0.18, "2026-01-01T00:00:00Z")
    f64 = v(0.16, "2026-01-01T00:00:00Z", sku="f64")
    west = v(0.17, "2026-01-01T00:00:00Z", region="WestEurope")
    when = "2026-11-20T00:00:00Z"
    assert pr.in_effect([general, f64, west], when, LIST, sku="F64")["rate_per_cu_hour"] == 0.16
    assert pr.in_effect([general, f64, west], when, LIST, region="westeurope")["rate_per_cu_hour"] == 0.17
    assert pr.in_effect([general, f64, west], when, LIST, sku="F2")["rate_per_cu_hour"] == 0.18


def test_no_price_or_no_figure_is_unknown_not_zero():
    early = pr.value(10, "2025-06-01T00:00:00Z", [OLD, NEW], LIST)
    assert early["classification"] == ms.UNKNOWN and early["value"] is None and "in effect" in early["reason"]
    assert pr.value(None, "2026-11-20T00:00:00Z", [NEW], LIST)["classification"] == ms.UNKNOWN
    assert pr.value(10, "2026-11-20T00:00:00Z", [NEW], "Other")["value"] is None
    out = pr.value_runs([run("e1", "2026-11-20T00:00:00Z"), run("e2", "2026-11-21T00:00:00Z", avoided=None)],
                        [NEW], LIST)
    assert out["total"]["unknown_parts"] == 1 and out["total"]["value"] == pytest.approx(1.2)


def test_a_run_from_two_devices_is_priced_once():
    r = run("e1", "2026-11-20T00:00:00Z")
    assert len(pr.value_runs([r, r], [NEW], LIST)["runs"]) == 1


def test_gross_or_net():
    r = run("e1", "2026-11-20T00:00:00Z")
    assert pr.value_runs([r], [NEW], LIST, net=False)["basis"] == "gross"


@pytest.mark.parametrize("kw, match", [
    ({"scenario": " "}, "name"),
    ({"rate_per_cu_hour": 0}, "above 0"),
    ({"rate_per_cu_hour": float("nan")}, "finite"),
    ({"rate_per_cu_hour": float("inf")}, "finite"),
    ({"currency": "dollars"}, "three-letter"),
    ({"pricing_model": "free"}, "pricing model"),
    ({"confidence": "sure"}, "confidence"),
    ({"pricing_model": pr.ASSUMPTION, "confidence": "confirmed"}, "assumption"),
    ({"effective_from": "soon"}, "isn't a time"),
    ({"effective_to": "2025-01-01T00:00:00Z"}, "end date must be after the start date"),
    ({"sku": "F3"}, "SKU"),
])
def test_bad_scenarios_are_refused(kw, match):
    base = {"scenario": LIST, "rate_per_cu_hour": 0.18, "currency": "USD", "effective_from": "2026-01-01T00:00:00Z",
            "pricing_model": pr.PAY_AS_YOU_GO, "confidence": "confirmed"}
    with pytest.raises(ValueError, match=match):
        pr.scenario(**(base | kw))


@pytest.mark.parametrize("cu", [-1.0, float("nan"), float("inf")])
def test_bad_cu_is_refused(cu):
    with pytest.raises(ValueError, match="finite"):
        pr.value(cu, "2026-11-20T00:00:00Z", [NEW], LIST)


def test_mixed_currency_runs_are_refused():
    eur = v(0.17, "2026-01-01T00:00:00Z", name="EU", currency="EUR")
    usd = v(0.18, "2026-01-01T00:00:00Z", name="EU", sku="F64")
    with pytest.raises(ValueError, match="currencies"):
        pr.value_runs([run("e1", "2026-11-20T00:00:00Z")], [eur, usd], "EU", sku="F64")


def test_one_version_with_different_labels_is_refused():
    a = v(0.20, "2026-11-15T00:00:00Z")
    b = pr.scenario(scenario=LIST, rate_per_cu_hour=0.20, currency="USD", effective_from="2026-11-15T00:00:00Z",
                    pricing_model=pr.PAY_AS_YOU_GO, confidence="estimated", source="a guess")
    assert a["pricing_version"] == b["pricing_version"]
    with pytest.raises(ValueError, match="different sources or confidence"):
        pr.check([a, b])
    with pytest.raises(ValueError, match="different sources or confidence"):
        pr.value(1, "2026-11-20T00:00:00Z", [a, b], LIST)


def test_two_different_attributions_of_one_run_are_refused_not_picked_by_order():
    low, high = run("e1", "2026-11-10T00:00:00Z", 2.0), run("e1", "2026-11-10T00:00:00Z", 9.0)
    for order in ([low, high], [high, low]):
        with pytest.raises(ValueError, match="two different attributions"):
            pr.value_runs(order, [OLD], LIST)
    same = run("e1", "2026-11-10T00:00:00Z", 2.0)
    assert pr.value_runs([low, same], [OLD], LIST)["total"]["value"] == pr.value_runs([low], [OLD], LIST)["total"]["value"]


def test_no_runs_price_to_a_scenario_zero_not_a_measured_one():
    total = pr.value_runs([], [OLD], LIST)["total"]
    assert total["value"] == 0 and total["classification"] == ms.SCENARIO
