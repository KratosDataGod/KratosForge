"""ESI-402: capacity headroom (measure B) and estimated capacity value (measure C).

Headroom is Estimated, per timepoint, from imported timepoints and never from totals; a timepoint
with no data is Unknown, never 0; a period the timepoints don't cover is Incomplete with the missing
windows listed; a SKU change is applied at each timepoint; capacities are never mixed (scenario 5);
an F64 using less shows headroom, not savings; capacity value is a Scenario figure, never savings."""

from __future__ import annotations

import datetime as dt
import json
import random
import re
from collections.abc import Sequence
from typing import Any

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import accruals as ac
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import headroom as hr
from fabric_desktop.savings import pricing as pr

T0 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
HOUR = dt.timedelta(hours=1)
PERIOD = {"start": T0.isoformat(), "end": (T0 + 4 * HOUR).isoformat()}
LIST = "List price"
CAP, CAP2 = "cap-a", "cap-b"
TOL = 1e-6


def t(hours: float) -> str:
    return (T0 + dt.timedelta(hours=hours)).isoformat()


def row(cap: str, h0: float, h1: float, cu: float | None, cls: str = ms.MEASURED, **kw: Any) -> dict[str, Any]:
    return {"capacity_id": cap, "period_start": t(h0), "period_end": t(h1), "cu_seconds": cu,
            "classification": cls, "source": "Capacity Metrics export", "collected_at": t(10), **kw}


def hourly(cap: str, cus: Sequence[float | None], start: float = 0) -> list[dict[str, Any]]:
    return [row(cap, start + i, start + i + 1, cu, ms.UNKNOWN if cu is None else ms.MEASURED)
            for i, cu in enumerate(cus)]


def span(cap: str, sku: str, start: float = -100, end: float | None = None) -> dict[str, Any]:
    return {"capacity_id": cap, "sku": sku, "from": t(start), "to": None if end is None else t(end),
            "source": "entered"}


def run(eid: str, when: float, avoided: float | None = 1.0, cap: str | None = CAP, *,
        overhead: float | None = 0.0) -> dict[str, Any]:
    r = {"execution_id": eid, "workload_key": "nb:sales", "baseline_version": "b1", "at": t(when), "ran": at.PC}
    base = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, 10, run_id=eid, source="Capacity Metrics export",
                   collected_at=t(0))
    local = (ms.make(ms.ESTIMATED, "fabric_job_cu_avoided", ms.CU_HOURS, avoided, method="calibration",
                     assumptions=["median"]) if avoided is not None else None)
    over = (ms.make(ms.MEASURED, "overhead", ms.CU_HOURS, overhead, source="sync", collected_at=t(0))
            if overhead is not None else None)
    out = at.attribute(r, base, local_avoided=local, overhead=over)
    return out if cap is None else out | {"capacity_id": cap}


def one(result: dict[str, Any], cap: str = CAP) -> dict[str, Any]:
    return next(c for c in result["capacities"] if c["capacity_id"] == cap)


F2 = 2 * 3600  # CU seconds an F2 can use in an hour


def test_headroom_is_estimated_per_timepoint_with_peak_and_p95():
    usage = hourly(CAP, [0.5 * F2, 0.25 * F2, 0.75 * F2, 0.1 * F2])
    runs = [run("e1", 2.5, avoided=0.4)]  # 0.4 CU hours = 1440 CU seconds = 20% of an F2 hour
    out = hr.headroom(usage, runs, [span(CAP, "F2")], period=PERIOD)
    c = one(out)
    assert c["status"] == hr.COMPLETE and c["complete"] and c["missing_windows"] == []
    tp = c["timepoints"][2]
    assert tp["utilization_pct"] == pytest.approx(75)
    assert tp["utilization_if_not_avoided_pct"] == pytest.approx(95)
    assert tp["headroom_gained_pct"] == pytest.approx(20)
    assert tp["headroom_gained_cu_seconds"] == pytest.approx(1440) == tp["avoided_cu_seconds"]
    assert tp["runs"] == ["e1"] and tp["skus"] == ["F2"] and tp["limit_cu_seconds"] == F2
    assert c["timepoints"][0]["headroom_gained_pct"] == 0  # a known timepoint with no runs gains nothing
    peak = c["peak"]
    assert peak["as_observed"]["value"] == pytest.approx(75)
    assert peak["if_not_avoided"]["value"] == pytest.approx(95)
    assert peak["headroom_gained"]["value"] == pytest.approx(20)
    # Nearest rank over 4 values: the 4th, the peak itself.
    assert c["p95"]["as_observed"]["value"] == pytest.approx(75)
    for name in ("peak", "p95"):
        for f in c[name].values():
            assert f["classification"] == ms.ESTIMATED and f["label"] == "Estimated"
            assert f["method"] and f["assumptions"] and f["complete"]
    assert c["method"] and c["assumptions"]
    assert all(tp["classification"] == ms.ESTIMATED for tp in c["timepoints"])
    assert out["written"] is False and out["is_savings"] is False


def test_p95_is_a_value_a_timepoint_really_had():
    cus = [float(i) for i in range(1, 41)]  # 40 hours, 1..40 CU seconds
    period = {"start": t(0), "end": t(40)}
    c = one(hr.headroom(hourly(CAP, cus), [], [span(CAP, "F2")], period=period))
    # ceil(0.95 * 40) = 38th smallest: 38 CU seconds.
    assert c["p95"]["as_observed"]["value"] == pytest.approx(38 / F2 * 100)
    assert c["peak"]["as_observed"]["value"] == pytest.approx(40 / F2 * 100)


def test_a_timepoint_with_no_data_is_unknown_never_zero_or_an_average():
    usage = hourly(CAP, [0.5 * F2, None, 0.25 * F2, 0.1 * F2])
    c = one(hr.headroom(usage, [], [span(CAP, "F2")], period=PERIOD))
    tp = c["timepoints"][1]
    assert tp["used_cu_seconds"] is None and tp["utilization_pct"] is None
    assert tp["headroom_gained_pct"] is None and tp["classification"] == ms.UNKNOWN
    assert re.fullmatch(r"1 of its 1 rows have no CU figure, so its use isn't known\.", tp["reason"])
    assert c["status"] == hr.INCOMPLETE and not c["complete"]
    peak = c["peak"]["as_observed"]
    assert peak["value"] == pytest.approx(50)  # the known maximum, not an average and not filled in
    assert not peak["complete"] and peak["timepoints"] == 3 and peak["of_timepoints"] == 4
    assert "could be higher" in peak["reason"]


def test_a_split_timepoint_with_one_unknown_part_is_unknown():
    usage = [row(CAP, 0, 1, 100, item_id="a"), row(CAP, 0, 1, None, ms.UNKNOWN, item_id="b")]
    c = one(hr.headroom(usage, [], [span(CAP, "F2")], period={"start": t(0), "end": t(1)}))
    assert c["timepoints"][0]["used_cu_seconds"] is None
    usage = [row(CAP, 0, 1, 100, item_id="a"), row(CAP, 0, 1, 50, ms.ESTIMATED, item_id="b")]
    tp = one(hr.headroom(usage, [], [span(CAP, "F2")], period={"start": t(0), "end": t(1)}))["timepoints"][0]
    assert tp["used_cu_seconds"] == 150 and tp["used_classification"] == ms.ESTIMATED


def test_timepoints_that_dont_cover_the_period_are_incomplete_with_the_missing_windows():
    usage = [row(CAP, 0, 1, 100), row(CAP, 2, 3, 100)]
    runs = [run("e-gap", 1.5)]
    c = one(hr.headroom(usage, runs, [span(CAP, "F2")], period=PERIOD))
    assert c["status"] == hr.INCOMPLETE
    assert c["missing_windows"] == [{"start": hr.iso(t(1)), "end": hr.iso(t(2))},
                                    {"start": hr.iso(t(3)), "end": hr.iso(t(4))}]
    assert len(c["timepoints"]) == 2  # no timepoint is made up for a gap
    assert c["peak"]["as_observed"]["of_timepoints"] == 4
    assert re.fullmatch(r"2 windows of the period have no timepoints\.", c["reason"])
    assert [r["execution_id"] for r in c["runs_not_in_a_timepoint"]] == ["e-gap"]
    # The run still counts as CU avoided, and is priced, though no timepoint holds it.
    assert c["cu_avoided"]["value"] == pytest.approx(1.0)


def test_a_row_straddling_the_period_edge_is_left_out_and_its_window_is_missing():
    usage = [row(CAP, -0.5, 0.5, 100), row(CAP, 0.5, 4, 100)]
    out = hr.headroom(usage, [], [span(CAP, "F2")], period=PERIOD)
    assert out["outside_period"]["usage_rows"] == 1
    assert one(out)["missing_windows"] == [{"start": hr.iso(t(0)), "end": hr.iso(t(0.5))}]


def test_a_capacity_with_no_timepoints_is_unknown():
    out = hr.headroom([], [run("e1", 1)], [span(CAP, "F64")], period=PERIOD, capacity_ids=[CAP2])
    for cap in (CAP, CAP2):
        c = one(out, cap)
        assert c["status"] == hr.UNKNOWN and c["timepoints"] == []
        assert c["missing_windows"] == [{"start": hr.iso(t(0)), "end": hr.iso(t(4))}]
        for f in c["peak"].values():
            assert f["value"] is None and f["classification"] == ms.UNKNOWN and f["reason"]


def test_sku_spans_come_from_capacity_history_and_the_sku_in_effect_is_used():
    changes: list[dict[str, Any]] = [
        {"record_id": "c1", "scope_type": "capacity", "scope_id": "CAP-A", "setting": "sku", "changed_after": None,
         "changed_by_time": t(-10), "old_json": None, "new_json": json.dumps("F64")},
        {"record_id": "c2", "scope_type": "capacity", "scope_id": "cap-a", "setting": "sku",
         "changed_after": t(2), "changed_by_time": t(3), "old_json": json.dumps("F64"), "new_json": json.dumps("F32")},
        {"record_id": "x", "scope_type": "capacity", "scope_id": "cap-a", "setting": "region",
         "changed_after": None, "changed_by_time": t(-10), "old_json": None, "new_json": json.dumps("westeurope")},
    ]
    spans = hr.sku_spans(changes)
    assert [(s["sku"], s["from"], s["to"]) for s in spans] == [("F64", hr.iso(t(-10)), hr.iso(t(2))),
                                                                ("F32", hr.iso(t(3)), None)]
    assert spans[1]["assumptions"] and not spans[0]["assumptions"]
    usage = hourly(CAP, [32 * 3600, 32 * 3600, 32 * 3600, 32 * 3600])
    c = one(hr.headroom(usage, [], spans, period=PERIOD))
    tps = c["timepoints"]
    assert tps[0]["skus"] == ["F64"] and tps[0]["utilization_pct"] == pytest.approx(50)
    assert tps[1]["utilization_pct"] == pytest.approx(50)
    # Between the read that last saw F64 and the one that first saw F32, the SKU isn't known.
    assert tps[2]["limit_cu_seconds"] is None and tps[2]["utilization_pct"] is None
    assert "SKU isn't known" in tps[2]["reason"]
    assert tps[3]["skus"] == ["F32"] and tps[3]["utilization_pct"] == pytest.approx(100)
    assert c["status"] == hr.INCOMPLETE
    assert any("unchanged after the last read" in a for a in c["assumptions"])


def test_a_timepoint_a_sku_change_falls_inside_is_limited_by_each_sku_for_its_part():
    spans = [span(CAP, "F64", -10, 0.5), span(CAP, "F32", 0.5)]
    c = one(hr.headroom([row(CAP, 0, 1, 1000)], [], spans, period={"start": t(0), "end": t(1)}))
    tp = c["timepoints"][0]
    assert tp["limit_cu_seconds"] == pytest.approx(64 * 1800 + 32 * 1800)
    assert tp["skus"] == ["F64", "F32"]


def test_an_unknown_sku_size_makes_the_limit_unknown():
    c = one(hr.headroom(hourly(CAP, [100]), [], [span(CAP, "A1")], period={"start": t(0), "end": t(1)}))
    assert c["timepoints"][0]["limit_cu_seconds"] is None
    assert c["status"] == hr.UNKNOWN


def test_sku_history_that_doesnt_chain_is_refused():
    changes: list[dict[str, Any]] = [
        {"record_id": "c1", "scope_id": "cap-a", "setting": "sku", "changed_after": None, "changed_by_time": t(0),
         "old_json": None, "new_json": json.dumps("F64")},
        {"record_id": "c2", "scope_id": "cap-a", "setting": "sku", "changed_after": t(1), "changed_by_time": t(2),
         "old_json": json.dumps("F16"), "new_json": json.dumps("F32")},
    ]
    with pytest.raises(ValueError, match="doesn't chain"):
        hr.sku_spans(changes)


def test_several_capacities_are_never_mixed():
    """Scenario 5: the same hours on two capacities of different sizes, each with its own runs."""
    usage = hourly(CAP, [0.5 * F2] * 4) + hourly(CAP2, [0.5 * 64 * 3600] * 4)
    runs = [run("a1", 0.5, 0.2, CAP), run("b1", 0.5, 3.2, CAP2), run("b2", 1.5, 6.4, CAP2),
            run("nowhere", 0.5, 1.0, None)]
    spans = [span(CAP, "F2"), span(CAP2, "F64")]
    out = hr.headroom(usage, runs, spans, period=PERIOD)
    a, b = one(out, CAP), one(out, CAP2)
    assert a["timepoints"][0]["runs"] == ["a1"] and b["timepoints"][0]["runs"] == ["b1"]
    assert a["timepoints"][0]["headroom_gained_pct"] == pytest.approx(0.2 * 3600 / F2 * 100)
    assert b["timepoints"][0]["headroom_gained_pct"] == pytest.approx(3.2 / 64 * 100)
    assert b["timepoints"][1]["headroom_gained_pct"] == pytest.approx(10)
    assert a["cu_avoided"]["value"] == pytest.approx(0.2) and b["cu_avoided"]["value"] == pytest.approx(9.6)
    assert a["peak"]["headroom_gained"]["value"] == pytest.approx(10)
    assert b["peak"]["headroom_gained"]["value"] == pytest.approx(10)
    assert {s["sku"] for s in a["sku_spans"]} == {"F2"} and {s["sku"] for s in b["sku_spans"]} == {"F64"}
    assert out["unplaced"] == [{"execution_id": "nowhere",
                                "reason": "Neither it nor its ledger events name a capacity."}]
    # No figure adds the capacities together.
    assert set(out) == {"measures", "period", "basis", "capacities", "unplaced", "outside_period", "is_savings",
                        "not_savings_reason", "written"}


def test_an_accrual_result_is_placed_by_the_capacity_its_events_name():
    r = run("e1", 0.5, cap=None)
    accrued = ac.accrue(r, scope={"capacity_id": CAP})
    c = one(hr.headroom(hourly(CAP, [100]), [accrued], [span(CAP, "F2")], period={"start": t(0), "end": t(1)}))
    assert c["timepoints"][0]["runs"] == ["e1"]
    two = dict(accrued, events=[dict(accrued["events"][0], capacity_id=CAP),
                                dict(accrued["events"][0], capacity_id=CAP2)])
    out = hr.headroom([], [two], [], period=PERIOD)
    assert out["unplaced"][0]["execution_id"] == "e1" and "2 capacities" in out["unplaced"][0]["reason"]


def test_an_f64_using_less_shows_headroom_never_realized_savings():
    usage = hourly(CAP, [40 * 3600, 30 * 3600, 20 * 3600, 10 * 3600])
    runs = [run("e1", 0.5, 6.4), run("e2", 1.5, 6.4)]
    versions = [pr.scenario(scenario=LIST, rate_per_cu_hour=0.18, currency="USD", effective_from=t(-100),
                            pricing_model=pr.PAY_AS_YOU_GO, confidence="confirmed", source="price sheet")]
    out = hr.headroom(usage, runs, [span(CAP, "F64")], period=PERIOD, versions=versions, scenario=LIST)
    c = one(out)
    assert c["peak"]["headroom_gained"]["value"] == pytest.approx(10)
    assert c["is_savings"] is False and "aren't savings" in c["not_savings_reason"]
    assert c["capacity_value"]["is_savings"] is False
    for f in [*c["peak"].values(), *c["p95"].values()]:
        assert f["classification"] == ms.ESTIMATED and "saving" not in f["metric"]
    assert c["capacity_value"]["total"]["classification"] == ms.SCENARIO
    assert c["capacity_value"]["total"]["label"] == "Scenario"
    assert "isn't money saved" in c["capacity_value"]["text"]
    assert not any(k.startswith("realized") for k in c)


def test_capacity_value_is_a_scenario_from_pricing_value_runs():
    old = pr.scenario(scenario=LIST, rate_per_cu_hour=0.18, currency="USD", effective_from=t(-100),
                      effective_to=t(2), pricing_model=pr.PAY_AS_YOU_GO, confidence="confirmed", source="sheet")
    new = pr.scenario(scenario=LIST, rate_per_cu_hour=0.20, currency="USD", effective_from=t(2),
                      pricing_model=pr.PAY_AS_YOU_GO, confidence="confirmed", source="sheet")
    runs = [run("e1", 0.5, 2.0), run("e2", 2.5, 3.0)]
    out = hr.headroom(hourly(CAP, [100] * 4), runs, [span(CAP, "F2")], period=PERIOD, versions=[old, new],
                      scenario=LIST)
    value = one(out)["capacity_value"]
    assert value["measure"] == hr.MEASURE_C and value["scenario"] == LIST
    assert value["total"]["value"] == pytest.approx(2.0 * 0.18 + 3.0 * 0.20)
    assert value["total"]["currency"] == "USD"
    assert value["pricing_versions"] == sorted([old["pricing_version"], new["pricing_version"]])
    same = pr.value_runs([{"execution_id": r["execution_id"], "at": r["at"], "net_cu_hours": r["net_cu_hours"],
                           "gross_cu_hours": r["gross_cu_hours"]} for r in runs], [old, new], LIST)
    assert value["total"]["value"] == same["total"]["value"]
    assert re.fullmatch(r"Estimated capacity value under the List price pricing scenario: 0\.96 USD\. .*"
                        r"It isn't money saved: .*", value["text"])
    for x in value["runs"]:
        assert x["value"]["classification"] == ms.SCENARIO


def test_capacity_value_uses_the_price_for_the_sku_in_effect_when_each_run_happened():
    any_sku = pr.scenario(scenario=LIST, rate_per_cu_hour=0.18, currency="USD", effective_from=t(-100),
                          pricing_model=pr.RESERVED, confidence="confirmed", source="sheet")
    f32 = pr.scenario(scenario=LIST, rate_per_cu_hour=0.10, currency="USD", effective_from=t(-100), sku="F32",
                      pricing_model=pr.RESERVED, confidence="confirmed", source="sheet")
    spans = [span(CAP, "F64", -10, 2), span(CAP, "F32", 2)]
    out = hr.headroom(hourly(CAP, [100] * 4), [run("e1", 0.5, 1.0), run("e2", 2.5, 1.0)], spans, period=PERIOD,
                      versions=[any_sku, f32], scenario=LIST)
    runs = {x["execution_id"]: x for x in one(out)["capacity_value"]["runs"]}
    assert runs["e1"]["sku"] == "F64" and runs["e1"]["value"]["value"] == pytest.approx(0.18)
    assert runs["e2"]["sku"] == "F32" and runs["e2"]["value"]["value"] == pytest.approx(0.10)


def test_with_no_scenario_capacity_value_is_unknown():
    c = one(hr.headroom(hourly(CAP, [100] * 4), [run("e1", 0.5)], [span(CAP, "F2")], period=PERIOD))
    v = c["capacity_value"]
    assert v["total"]["value"] is None and v["total"]["classification"] == ms.UNKNOWN
    assert v["total"]["reason"] and v["runs"] == [] and "aren't savings" in v["text"]
    # Headroom doesn't need a price.
    assert c["peak"]["headroom_gained"]["value"] is not None


def test_a_scenario_no_version_covers_is_unknown_not_zero():
    versions = [pr.scenario(scenario="Other", rate_per_cu_hour=0.1, currency="EUR", effective_from=t(-100))]
    c = one(hr.headroom([], [run("e1", 0.5)], [], period=PERIOD, versions=versions, scenario=LIST))
    assert c["capacity_value"]["total"]["value"] is None
    assert c["capacity_value"]["total"]["classification"] == ms.UNKNOWN


def test_unknown_overhead_makes_net_avoided_unknown_and_gross_is_a_choice():
    usage = hourly(CAP, [100, 100])
    runs = [run("e1", 0.5, 1.0, overhead=None)]
    period = {"start": t(0), "end": t(2)}
    net = one(hr.headroom(usage, runs, [span(CAP, "F2")], period=period))
    tp = net["timepoints"][0]
    assert tp["avoided_cu_seconds"] is None and tp["utilization_pct"] == pytest.approx(100 / F2 * 100)
    assert tp["utilization_if_not_avoided_pct"] is None and tp["headroom_gained_pct"] is None
    assert net["status"] == hr.INCOMPLETE and net["cu_avoided"]["value"] is None
    gross = one(hr.headroom(usage, runs, [span(CAP, "F2")], period=period, basis=hr.GROSS))
    assert gross["timepoints"][0]["avoided_cu_seconds"] == pytest.approx(3600)
    assert gross["status"] == hr.COMPLETE


def test_an_unknown_run_makes_its_timepoint_unknown_and_is_not_counted_as_zero():
    usage = hourly(CAP, [100, 100])
    runs = [run("known", 0.2, 1.0), run("unknown", 0.7, None)]
    c = one(hr.headroom(usage, runs, [span(CAP, "F2")], period={"start": t(0), "end": t(2)}))
    assert c["timepoints"][0]["avoided_cu_seconds"] is None
    assert c["cu_avoided"]["value"] == pytest.approx(1.0) and c["cu_avoided"]["unknown_parts"] == 1
    assert not c["cu_avoided"]["complete"]


def test_kinds_stay_apart():
    versions = [pr.scenario(scenario=LIST, rate_per_cu_hour=0.18, currency="USD", effective_from=t(-100))]
    c = one(hr.headroom(hourly(CAP, [100] * 4), [run("e1", 0.5)], [span(CAP, "F2")], period=PERIOD,
                        versions=versions, scenario=LIST))
    assert c["cu_avoided"]["classification"] == ms.ESTIMATED and c["cu_avoided"]["unit"] == ms.CU_HOURS
    assert c["capacity_value"]["total"]["classification"] == ms.SCENARIO
    with pytest.raises(ValueError, match="scenario figures can't be added"):
        ms.total([c["cu_avoided"], c["capacity_value"]["total"]], "x", ms.CU_HOURS)


def test_identical_rows_count_once_and_inputs_in_any_order_give_the_same_result():
    usage = hourly(CAP, [100, 200, 300, 400]) + hourly(CAP2, [50, 60, 70, 80])
    runs = [run("e1", 0.5, 0.01), run("e2", 3.5, 0.02, CAP2)]
    spans = [span(CAP, "F2"), span(CAP2, "F4")]
    first = hr.headroom(usage + usage[:2], runs + runs[:1], spans, period=PERIOD)
    rng = random.Random(4)
    u, r, s = usage[:], runs[:], spans[:]
    rng.shuffle(u)
    rng.shuffle(r)
    rng.shuffle(s)
    assert hr.headroom(u, r, s, period=PERIOD) == first
    assert one(first)["timepoints"][0]["used_cu_seconds"] == 100


@pytest.mark.parametrize(("usage", "runs", "spans", "message"), [
    ([row(CAP, 0, 1, 1), row(CAP, 0.5, 1.5, 1)], [], [], "overlap"),
    ([row(CAP, 0, 0, 1)], [], [], "no length"),
    ([row(CAP, 0, 1, 1, ms.SCENARIO)], [], [], "Scenario"),
    ([row(CAP, 0, 1, -1)], [], [], "negative"),
    ([row(CAP, 0, 1, float("nan"))], [], [], "finite"),
    ([row("", 0, 1, 1)], [], [], "doesn't name its capacity"),
    ([row(CAP, 0, 1, 1), row(CAP, 0, 1, 2)], [], [], "twice with different figures"),
    ([], [run("e1", 1, 1.0), run("e1", 1, 2.0)], [], "two different results"),
    ([], [], [span(CAP, "F2", 0, 2), span(CAP, "F4", 1)], "overlap with different SKUs"),
    ([], [], [span(CAP, "F2", 2, 1)], "ends before it starts"),
])
def test_bad_input_is_refused(usage, runs, spans, message):
    with pytest.raises(ValueError, match=message):
        hr.headroom(usage, runs, spans, period=PERIOD)


def test_bad_period_and_basis_are_refused():
    with pytest.raises(ValueError, match="ends before"):
        hr.headroom([], [], [], period={"start": t(2), "end": t(1)})
    with pytest.raises(ValueError, match="net or gross"):
        hr.headroom([], [], [], period=PERIOD, basis="both")
    scenario_run = dict(run("e1", 1), net_classification=ms.SCENARIO)
    with pytest.raises(ValueError, match="Scenario"):
        hr.headroom([], [scenario_run], [], period=PERIOD)


# ---------- property: avoided CU never makes utilization negative, and headroom gained never exceeds it

SEEDS = (7, 11, 2026)
CASES = 200


def _case(rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    hours = rng.randint(1, 8)
    caps = [CAP, CAP2][: rng.randint(1, 2)]
    usage: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for cap in caps:
        sku = rng.choice(["F2", "F8", "F64"])
        if rng.random() < 0.3:
            change = rng.randint(0, hours)
            spans += [span(cap, sku, -10, change), span(cap, rng.choice(["F4", "F32"]), change)]
        elif rng.random() < 0.9:
            spans.append(span(cap, sku))
        for h in range(hours):
            if rng.random() < 0.15:
                continue  # a gap
            cu = None if rng.random() < 0.1 else round(rng.uniform(0, 80 * 3600), rng.choice((0, 3, 6)))
            usage.append(row(cap, h, h + 1, cu, ms.UNKNOWN if cu is None else ms.MEASURED))
        for i in range(rng.randint(0, 6)):
            avoided = None if rng.random() < 0.1 else round(rng.uniform(0, 10), rng.choice((0, 3, 6)))
            overhead = None if rng.random() < 0.1 else round(rng.uniform(0, 0.5), 3)
            runs.append(run(f"{cap}-{i}", rng.uniform(0, hours), avoided, cap, overhead=overhead))
    return usage, runs, spans, {"start": t(0), "end": t(hours)}


@pytest.mark.parametrize("seed", SEEDS)
def test_property_avoided_cu_never_makes_utilization_negative_or_headroom_bigger_than_itself(seed):
    rng = random.Random(seed)
    for n in range(CASES):
        usage, runs, spans, period = _case(rng)
        for basis in (hr.NET, hr.GROSS):
            out = hr.headroom(usage, runs, spans, period=period, basis=basis)
            for c in out["capacities"]:
                where = f"seed {seed} case {n} {basis} {c['capacity_id']}"
                gains: list[float] = []
                for tp in c["timepoints"]:
                    obs, ifn, g = tp["utilization_pct"], tp["utilization_if_not_avoided_pct"], tp["headroom_gained_pct"]
                    for v in (obs, ifn, g, tp["avoided_cu_seconds"], tp["used_cu_seconds"]):
                        assert v is None or v >= 0, where
                    if tp["used_cu_seconds"] is None:
                        assert obs is None and ifn is None and g is None, where
                    if g is not None:
                        assert ifn >= obs - TOL, where
                        assert g * tp["limit_cu_seconds"] / 100 <= tp["avoided_cu_seconds"] + TOL, where
                        assert tp["headroom_gained_cu_seconds"] <= tp["avoided_cu_seconds"] + TOL, where
                        assert ifn == pytest.approx(obs + g, abs=1e-5), where
                        gains.append(g)
                    else:
                        assert tp["classification"] == ms.UNKNOWN and tp["reason"], where
                for name in ("peak", "p95"):
                    gained = c[name]["headroom_gained"]["value"]
                    if gains:
                        assert 0 <= gained <= max(gains) + TOL, where
                        assert c[name]["if_not_avoided"]["value"] >= c[name]["as_observed"]["value"] - TOL, where
                    else:
                        assert gained is None and c["status"] == hr.UNKNOWN, where
                assert c["complete"] == (not c["missing_windows"]
                                         and all(tp["classification"] == ms.ESTIMATED for tp in c["timepoints"])
                                         and bool(c["timepoints"])), where
