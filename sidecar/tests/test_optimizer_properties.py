"""Property tests: the optimizer side never invents a saving (mutation batches 198 to 204,
docs/quality/mutation-testing.md).

The figures that feed Optimize and the fdopt_ tables (measurement.py, calibration.py,
opportunities/impact.py and the duplicate notebook analyzer) are checked over generated inputs:

- No saving is negative, and none is more than what it avoids.
- The same inputs always give the same output, in any order the code says doesn't matter.
- Rounding never makes a figure bigger than half a unit in its sixth decimal (times the scale, for a
  figure rounded when measured and then scaled to a month).
- With no evidence the figure is Unknown with a reason, never 0.

No hypothesis (it isn't in the CI lock): each property draws a few hundred cases from
``random.Random`` with fixed seeds, so a failure reproduces and its inputs are in the message.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from fabric_desktop import calibration as cal
from fabric_desktop import measurement as ms
from fabric_desktop.opportunities import analyzers, impact

SEEDS = (1, 7, 42)
CASES = 200
NOW = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)
ROUND = 5e-7  # half a unit in the sixth decimal: the most one round(x, 6) can add


def _scaled_round(parts: int) -> float:
    """What rounding can add to a monthly figure: each part is rounded when it is measured and then
    scaled by up to 30 (a period of a day or less), and the sum is rounded once more."""
    return ROUND * (impact.MONTH_DAYS * parts + 1)


def _cases():
    for seed in SEEDS:
        rnd = random.Random(seed)
        for i in range(CASES):
            yield seed, i, rnd


def _cu(rnd: random.Random, cls: str | None = None, at: dt.datetime = NOW) -> dict:
    cls = cls or rnd.choice([ms.MEASURED, ms.ESTIMATED])
    unit = rnd.choice([ms.CU_SECONDS, ms.CU_HOURS])
    value = rnd.choice([0.0, rnd.uniform(0, 10), rnd.uniform(0, 1e6), rnd.uniform(0, 1e-5)])
    if cls == ms.MEASURED:
        return ms.make(cls, "fabric_job_cu", unit, value, source="job api", collected_at=at)
    return ms.make(cls, "fabric_job_cu", unit, value, method="run time x CUs", assumptions=["configured CUs"])


def _seconds(m: dict) -> float:
    return ms.convert(m["value"], m["unit"], ms.CU_SECONDS)


# ---- calibration: the CU a run on this PC avoided


def _baselines(rnd: random.Random) -> list[dict]:
    out = []
    ages = rnd.sample(range(-5, 130), rnd.randint(0, 9))  # distinct times, some in the future or too old
    for age in ages:
        at = NOW - dt.timedelta(days=age, minutes=rnd.randint(0, 59))
        kind = rnd.choice([ms.MEASURED, ms.ESTIMATED, ms.ESTIMATED, ms.UNKNOWN])
        cu = (ms.unknown("fabric_job_cu", ms.CU_SECONDS, "no figure") if kind == ms.UNKNOWN else _cu(rnd, kind, at))
        b = {"cu": cu, "at": at.isoformat()}
        if rnd.random() < 0.5:
            b["rows"] = rnd.choice([0, rnd.randint(1, 10_000)])
        out.append(b)
    return out


def _usable(baselines: list[dict]) -> list[dict]:
    return [b for b in baselines if cal._usable(b, NOW, cal.MAX_AGE_DAYS)]


def test_calibrated_cu_is_never_negative_and_never_more_than_the_baselines_it_comes_from():
    for seed, i, rnd in _cases():
        baselines = _baselines(rnd)
        run = {"workload_id": "w", "run_id": f"r{i}", "at": NOW.isoformat()}
        got = cal.calibrate(run, baselines, now=NOW)
        usable = _usable(baselines)
        if not usable:
            continue
        measured = [b for b in usable if b["cu"]["classification"] == ms.MEASURED]
        pick = sorted(measured or usable, key=cal._when, reverse=True)[:cal.MAX_BASELINES]
        per = [_seconds(b["cu"]) for b in pick]
        # Without rows read on this run nothing is scaled: the figure is one of the baselines' own.
        assert 0 <= got["value"] <= max(per) + ROUND, (seed, i, baselines, got)
        assert got["value"] >= min(per) - ROUND, (seed, i, baselines, got)


def test_scaling_by_rows_read_stays_between_the_scaled_baselines():
    for seed, i, rnd in _cases():
        baselines = _baselines(rnd)
        rows = rnd.randint(1, 10_000)
        got = cal.calibrate({"workload_id": "w", "run_id": "r", "at": NOW.isoformat(), "rows": rows}, baselines, now=NOW)
        usable = _usable(baselines)
        if not usable:
            assert got["value"] is None
            continue
        measured = [b for b in usable if b["cu"]["classification"] == ms.MEASURED]
        pick = sorted(measured or usable, key=cal._when, reverse=True)[:cal.MAX_BASELINES]
        scaled = all(isinstance(b.get("rows"), int) and b["rows"] > 0 for b in pick)
        per = [_seconds(b["cu"]) * (rows / b["rows"] if scaled else 1.0) for b in pick]
        assert 0 <= got["value"], (seed, i, got)
        assert min(per) - ROUND <= got["value"] <= max(per) + ROUND, (seed, i, baselines, rows, got)


def test_with_no_usable_baseline_the_cu_avoided_is_unknown_with_a_reason_never_zero():
    for seed, i, rnd in _cases():
        baselines = [b for b in _baselines(rnd) if not _usable([b])]
        got = cal.calibrate({"workload_id": "w", "run_id": "r", "at": NOW.isoformat()}, baselines, now=NOW)
        assert got["classification"] == ms.UNKNOWN and got["value"] is None and got["reason"], (seed, i, got)


def test_calibration_is_the_same_every_time_and_in_any_order():
    for seed, i, rnd in _cases():
        baselines = _baselines(rnd)
        run = {"workload_id": "w", "run_id": "r", "at": NOW.isoformat(), "rows": rnd.choice([None, 500])}
        first = cal.calibrate(run, baselines, now=NOW)
        shuffled = baselines[:]
        rnd.shuffle(shuffled)
        again = cal.calibrate(run, shuffled, now=NOW)
        assert again == cal.calibrate(run, baselines, now=NOW) == first, (seed, i, baselines)


# ---- impact: what an opportunity could avoid, and its value


def _workload(rnd: random.Random, n: int) -> dict:
    read = rnd.random() < 0.85
    days = rnd.choice([1, 7, 14, 30, 45])
    seconds = None if rnd.random() < 0.1 else rnd.uniform(0, 50_000)
    return {"item_id": f"i{n}", "name": f"Notebook {n}", "type": "Notebook", "workspace_id": "ws",
            "workspace_name": "Sales",
            "runs": {"read": read, "runs": rnd.randint(0, 40), "scheduled": rnd.randint(0, 40), "days": days,
                     "seconds_total": seconds if read else None}}


def _usage(rnd: random.Random, workloads: list[dict]) -> list[dict]:
    rows = []
    for w in workloads:
        if rnd.random() < 0.2:
            continue  # this item isn't in any imported file
        for k in range(rnd.randint(1, 3)):
            start = NOW - dt.timedelta(days=rnd.uniform(0.1, 60))
            end = start + dt.timedelta(hours=rnd.uniform(0, 48))
            cu = ms.make(ms.MEASURED, "fabric_item_cu", ms.CU_SECONDS, rnd.uniform(0, 1e5), source="metrics",
                         collected_at=NOW, period_start=start, period_end=end)
            rows.append({"key": f"{w['item_id']}-{k}", "workspace": "Sales", "item": w["name"], "cu": cu})
    rows += rows[: rnd.randint(0, 2)]  # rows seen in two imports count once
    return [{"id": "u1", "file_name": "metrics.csv", "rows": rows}]


def _bound(removed: list[dict], usage: dict) -> float:
    """The most a month could avoid: each item's imported CU scaled from its period to 30 days."""
    total = 0.0
    for w in removed:
        u = usage[impact._key(w["workspace_name"], w["name"])]
        days = max((u["end"] - u["start"]).total_seconds() / 86400, 1.0)
        total += u["cu_seconds"] / days * impact.MONTH_DAYS
    return total


def test_cu_an_opportunity_avoids_is_never_negative_nor_more_than_the_imported_usage_scaled_to_a_month():
    for seed, i, rnd in _cases():
        removed = [_workload(rnd, n) for n in range(rnd.randint(1, 4))]
        usage = impact.usage_by_item(_usage(rnd, removed))
        got = impact.avoidable(removed, usage, NOW)["cu_per_month"]
        if any(impact._key(w["workspace_name"], w["name"]) not in usage for w in removed):
            assert got["classification"] == ms.UNKNOWN and got["value"] is None and got["reason"], (seed, i, got)
            continue
        bound = _bound(removed, usage)
        tol = _scaled_round(len(removed))
        assert 0 <= got["value"] <= bound + tol, (seed, i, got, bound)
        assert got["value"] == pytest.approx(bound, abs=tol), (seed, i, got, bound)
        assert got["classification"] == ms.ESTIMATED


def test_run_time_an_opportunity_avoids_is_unknown_when_any_history_is_missing_never_zero():
    for seed, i, rnd in _cases():
        removed = [_workload(rnd, n) for n in range(rnd.randint(1, 4))]
        got = impact.avoidable(removed, {}, NOW)["run_seconds_per_month"]
        missing = [w for w in removed if not w["runs"]["read"] or w["runs"]["seconds_total"] is None]
        if missing:
            assert got["value"] is None and got["reason"], (seed, i, got)
        else:
            bound = sum(w["runs"]["seconds_total"] / max(w["runs"]["days"], 1) * impact.MONTH_DAYS for w in removed)
            assert 0 <= got["value"] <= bound + _scaled_round(len(removed)), (seed, i, got, bound)


def test_reducing_part_of_a_workload_is_unknown_whatever_was_measured():
    for seed, i, rnd in _cases():
        removed = [_workload(rnd, n) for n in range(rnd.randint(1, 3))]
        usage = impact.usage_by_item(_usage(rnd, removed))
        got = impact.avoidable(removed, usage, NOW, partial_reason="How much less is read isn't known yet.",
                               price={"price_per_cu_hour": 0.2, "currency": "USD"})
        for key in ("run_seconds_per_month", "cu_per_month", "cu_value_per_month"):
            assert got[key]["value"] is None and got[key]["reason"], (seed, i, key, got[key])
        assert got["realizable"] is False


def test_the_value_of_cu_is_never_negative_and_is_cu_hours_times_the_price():
    for seed, i, rnd in _cases():
        cu = _cu(rnd)
        price = {"price_per_cu_hour": rnd.choice([rnd.uniform(0.001, 5), 1.0]), "currency": "USD"}
        got = impact.value(cu, price, {})
        expected = ms.convert(cu["value"], cu["unit"], ms.CU_HOURS) * price["price_per_cu_hour"]
        assert got["value"] >= 0 and got["value"] == pytest.approx(expected, abs=ROUND), (seed, i, cu, price, got)
        assert got["value"] <= expected + ROUND and got["currency"] == "USD"


@pytest.mark.parametrize("price", [None, {}, {"price_per_cu_hour": 0, "currency": "USD"},
                                   {"price_per_cu_hour": 0.2}, {"price_per_cu_hour": 0.2, "currency": ""}])
def test_with_no_price_the_value_is_unknown_never_zero(price):
    got = impact.value(_cu(random.Random(1), ms.MEASURED), price, {})
    assert got["classification"] == ms.UNKNOWN and got["value"] is None and got["reason"]


def test_a_negative_price_is_refused_rather_than_making_a_negative_value():
    with pytest.raises(ValueError, match="negative"):
        impact.value(_cu(random.Random(3), ms.MEASURED) | {"value": 5.0}, {"price_per_cu_hour": -0.2, "currency": "USD"}, {})


def test_impact_is_the_same_every_time():
    for seed, i, rnd in _cases():
        removed = [_workload(rnd, n) for n in range(rnd.randint(1, 3))]
        usage = _usage(rnd, removed)
        a = impact.avoidable(removed, impact.usage_by_item(usage), NOW, price={"price_per_cu_hour": 0.2, "currency": "USD"})
        b = impact.avoidable(removed, impact.usage_by_item(usage), NOW, price={"price_per_cu_hour": 0.2, "currency": "USD"})
        assert a == b, (seed, i)


# ---- measurement: totals, units and rounding


def _part(rnd: random.Random, kinds: tuple[str, ...]) -> dict:
    kind = rnd.choice(kinds)
    if kind == ms.UNKNOWN:
        return ms.unknown("cu", rnd.choice([ms.CU_SECONDS, ms.CU_HOURS]), "not read")
    if kind == ms.SCENARIO:
        return ms.make(ms.SCENARIO, "cu", ms.CU_HOURS, rnd.uniform(0, 100), method="as if", assumptions=["a"])
    return _cu(rnd, kind)


def test_a_total_is_never_negative_never_more_than_its_parts_and_the_same_in_any_order():
    for seed, i, rnd in _cases():
        parts = [_part(rnd, (ms.MEASURED, ms.ESTIMATED, ms.UNKNOWN)) for _ in range(rnd.randint(1, 8))]
        got = ms.total(parts, "cu", ms.CU_SECONDS)
        known = [p for p in parts if p["value"] is not None]
        if not known:
            assert got["classification"] == ms.UNKNOWN and got["value"] is None and got["reason"], (seed, i, got)
            continue
        exact = sum(_seconds(p) for p in known)
        assert 0 <= got["value"] <= exact + ROUND, (seed, i, parts, got)
        shuffled = parts[:]
        rnd.shuffle(shuffled)
        again = ms.total(shuffled, "cu", ms.CU_SECONDS)
        assert again["value"] == pytest.approx(got["value"], abs=2 * ROUND), (seed, i)
        assert again["classification"] == got["classification"] and again["unknown_parts"] == got["unknown_parts"]
        assert got["complete"] == (len(known) == len(parts))


def test_scenario_figures_are_never_added_to_measured_or_estimated_ones():
    for _seed, _i, rnd in _cases():
        parts = [_part(rnd, (ms.SCENARIO,)), _part(rnd, (ms.MEASURED, ms.ESTIMATED))]
        parts += [_part(rnd, (ms.MEASURED, ms.ESTIMATED, ms.SCENARIO, ms.UNKNOWN)) for _ in range(rnd.randint(0, 4))]
        rnd.shuffle(parts)
        with pytest.raises(ValueError, match="scenario"):
            ms.total(parts, "cu", ms.CU_HOURS)


def test_converting_there_and_back_never_grows_a_figure():
    for seed, i, rnd in _cases():
        v = rnd.choice([rnd.uniform(0, 1e-3), rnd.uniform(0, 1e3), rnd.uniform(0, 1e9)])
        for a, b in ((ms.CU_SECONDS, ms.CU_HOURS), (ms.CU_HOURS, ms.CU_SECONDS)):
            back = ms.convert(ms.convert(v, a, b), b, a)
            assert back <= v * (1 + 1e-12) + 1e-12, (seed, i, v, a, b, back)


def test_rounding_adds_at_most_half_a_unit_in_the_sixth_decimal_and_never_makes_something_from_nothing():
    for seed, i, rnd in _cases():
        v = rnd.choice([0.0, rnd.uniform(0, 1e-6), rnd.uniform(0, 1), rnd.uniform(0, 1e6)])
        m = ms.make(ms.ESTIMATED, "cu", ms.CU_SECONDS, v, method="m", assumptions=["a"])
        assert m["value"] <= v + ROUND, (seed, i, v, m["value"])
        if v == 0:
            assert m["value"] == 0
        hours = ms.to_unit(m, ms.CU_HOURS)
        assert hours["value"] <= m["value"] / 3600 + ROUND, (seed, i, m["value"], hours["value"])


def test_an_unknown_measurement_never_carries_a_value():
    with pytest.raises(ValueError, match="no value"):
        ms.make(ms.UNKNOWN, "cu", ms.CU_SECONDS, 0.0, reason="not read")
    with pytest.raises(ValueError, match="says why"):
        ms.make(ms.UNKNOWN, "cu", ms.CU_SECONDS, None)
    with pytest.raises(ValueError, match="negative"):
        ms.make(ms.ESTIMATED, "cu", ms.CU_SECONDS, -1e-3, method="m", assumptions=["a"])


def test_measurement_ids_are_the_same_every_time():
    for seed, i, rnd in _cases():
        m = _cu(rnd)
        again = dict(m)
        del again["id"]
        assert ms._id(again) == m["id"], (seed, i)


# ---- the duplicate notebook analyzer: what it claims to avoid


def _duplicates(rnd: random.Random, lakehouse: str | None = "lh-1") -> list[dict]:
    group = []
    for n in range(rnd.randint(2, 5)):
        w = _workload(rnd, n)
        w.update({"name": f"Copy {n}", "definition": "read", "default_lakehouse": lakehouse,
                  "cells": ["df = spark.read.table('bronze.sales')\ndf.write.saveAsTable('silver.sales')"]})
        group.append(w)
    return group


def test_a_duplicate_never_claims_the_kept_notebooks_cu():
    for seed, i, rnd in _cases():
        group = _duplicates(rnd)
        usage = impact.usage_by_item(_usage(rnd, group))
        ctx = analyzers.Context(usage=usage, now=NOW, price={"price_per_cu_hour": 0.2, "currency": "USD"})
        [opp] = analyzers.duplicate_notebooks(group, ctx)
        roles = {a["item_id"]: a["role"] for a in opp["artifacts"]}
        assert list(roles.values()).count("keep") == 1, (seed, i, roles)
        keep = next(k for k, r in roles.items() if r == "keep")
        assert keep not in opp["affects"], (seed, i, opp["affects"])
        running = [w for w in group if roles[w["item_id"]] == "remove" and w["runs"]["read"] and w["runs"]["runs"] > 0]
        assert sorted(opp["affects"]) == sorted(w["item_id"] for w in running), (seed, i)
        if not running:
            assert "cu_per_month" not in opp["impact"]
            continue
        cu = opp["impact"]["cu_per_month"]
        if cu["value"] is not None:
            assert 0 <= cu["value"] <= _bound(running, usage) + _scaled_round(len(running)), (seed, i, cu)
        # However much the kept notebook used, the figure is the same.
        bigger = dict(usage)
        k = impact._key("Sales", next(w["name"] for w in group if w["item_id"] == keep))
        if k in bigger:
            bigger[k] = dict(bigger[k], cu_seconds=bigger[k]["cu_seconds"] * 10 + 1e6)
        [again] = analyzers.duplicate_notebooks(group, analyzers.Context(usage=bigger, now=NOW, price=ctx.price))
        assert again["impact"]["cu_per_month"] == cu, (seed, i)


def test_the_kept_notebook_is_the_same_in_any_order():
    for seed, i, rnd in _cases():
        group = _duplicates(rnd)
        ctx = analyzers.Context(usage={}, now=NOW)
        first = analyzers.duplicate_notebooks(group, ctx)
        shuffled = group[:]
        rnd.shuffle(shuffled)
        again = analyzers.duplicate_notebooks(shuffled, ctx)
        keep = [[a["item_id"] for a in o["artifacts"] if a["role"] == "keep"] for o in (first[0], again[0])]
        assert keep[0] == keep[1], (seed, i)
        assert first[0]["impact"] == again[0]["impact"], (seed, i)


def test_copies_with_no_known_lakehouse_claim_nothing():
    for seed, i, rnd in _cases():
        group = _duplicates(rnd, lakehouse=None)
        usage = impact.usage_by_item(_usage(rnd, group))
        [opp] = analyzers.duplicate_notebooks(group, analyzers.Context(usage=usage, now=NOW))
        assert opp["affects"] == [] and "cu_per_month" not in opp["impact"], (seed, i)
        assert all(a["role"] != "remove" for a in opp["artifacts"])
