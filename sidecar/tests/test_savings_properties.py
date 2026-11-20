"""Property tests: the savings package never invents a saving (docs/quality/mutation-testing.md,
"Savings properties").

hypothesis isn't in the test lock, so each property draws a few hundred random cases from a seeded
``random.Random``. The same seed gives the same cases on every run, and a failing case is printed
in the assert message. Four promises are checked over runs on a PC or in Fabric, measured and
estimated baselines, optimizations with lower and higher afters, afters not yet known, optimizations
reverted, rejected or not yet in place, local figures and overhead known or not:

1. No saving is negative, and none is more than what it saves from: gross is between 0 and the
   baseline, net between 0 and gross, this PC's layer at most what the optimized workload needs,
   period sums are the runs' sums, a projection is never negative, and a price is CU x rate.
2. The same inputs give the same output, ids included, whatever order the inputs come in.
3. Rounding never creates a saving.
4. With no evidence the figure is unknown, never 0.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.opportunities.model import ACTION_ORDER
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import pricing as pr
from fabric_desktop.savings import recurring as rc

SEEDS = (7, 11, 2026)
CASES = 300
TOL = 1e-6
DAY0 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
# Values near a 6-decimal half point, where round-half rounding could tip a figure up.
EDGES = (0.0, 1e-7, 2.5e-7, 5e-7, 1.5e-6, 2.5e-6, 0.0000005, 0.1234565, 1.0000005, 9.9999995)


def _when(rng: random.Random) -> str:
    return (DAY0 + dt.timedelta(days=rng.randint(0, 29), hours=rng.randint(0, 23))).isoformat()


def _value(rng: random.Random, top: float) -> float:
    pick = rng.random()
    if pick < 0.2:
        return rng.choice(EDGES)
    if pick < 0.3:
        return round(top, 6)
    return round(rng.uniform(0, top), rng.choice((0, 1, 3, 6)))


def measured(v: float) -> dict:
    return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, v, run_id="r", source="Capacity Metrics export",
                   collected_at="2026-11-01T00:00:00Z")


def estimated(v: float) -> dict:
    return ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_HOURS, v, method="calibration", assumptions=["median"])


def _cu(rng: random.Random, top: float, unknown_share: float = 0.15) -> dict | None:
    pick = rng.random()
    if pick < unknown_share / 2:
        return None
    if pick < unknown_share:
        return ms.unknown("fabric_job_cu", ms.CU_HOURS, "not measured")
    v = _value(rng, top)
    return measured(v) if rng.random() < 0.5 else estimated(v)


def case(rng: random.Random, eid: str = "e1", *, baseline_unknown: float = 0.1) -> dict:
    """One random input to ``attribution.attribute``."""
    b = None if rng.random() < baseline_unknown else _value(rng, 50)
    top = 1.6 * (b or 10) + 1
    opts = []
    for i in range(rng.randint(0, 4)):
        o = {"optimization_id": f"o{i}", "implemented_at": _when(rng), "action": rng.choice(ACTION_ORDER),
             "state": rng.choice(("verified", "verified", "validating", "reverted", "rejected", "superseded")),
             "after": _cu(rng, top, 0.25)}
        if rng.random() < 0.2:
            o["reverted_at"] = _when(rng)
        opts.append(o)
    ran = rng.choice((at.PC, at.FABRIC))
    return {"run": {"execution_id": eid, "workload_key": rng.choice(("nb:sales", "nb:hr")),
                    "baseline_version": rng.choice(("b1", "b1", "b2")), "at": _when(rng), "ran": ran},
            "baseline": None if b is None else (measured(b) if rng.random() < 0.6 else estimated(b)),
            "optimizations": opts, "local_avoided": _cu(rng, top), "overhead": _cu(rng, 2),
            "local_basis": rng.choice((at.ORIGINAL, at.OPTIMIZED))}


def attribute(c: dict, optimizations: list | None = None) -> dict:
    return at.attribute(c["run"], c["baseline"], c["optimizations"] if optimizations is None else optimizations,
                        local_avoided=c["local_avoided"], local_basis=c["local_basis"], overhead=c["overhead"])


def cases(seed: int, n: int = CASES, **kw):
    rng = random.Random(seed)
    for i in range(n):
        c = case(rng, f"e{i}", **kw)
        yield rng, c, attribute(c)


# ---------- 1. no saving is negative, and none is more than what it saves from

@pytest.mark.parametrize("seed", SEEDS)
def test_gross_is_between_zero_and_the_baseline_and_net_between_zero_and_gross(seed):
    for _, c, out in cases(seed):
        b, gross, net = out["baseline_cu_hours"], out["gross_cu_hours"], out["net_cu_hours"]
        if gross is None:
            assert net is None, c
            continue
        assert b is not None and 0 <= gross <= b, (c, out)
        if net is not None:
            assert 0 <= net <= gross, (c, out)


@pytest.mark.parametrize("seed", SEEDS)
def test_gross_is_the_known_layers_sum_kept_between_zero_and_the_baseline(seed):
    for _, c, out in cases(seed):
        known = [x["cu_hours"] for x in out["layers"] if x["cu_hours"] is not None]
        if not known:
            assert out["gross_cu_hours"] is None, (c, out)
            continue
        want = max(0.0, min(out["baseline_cu_hours"], sum(known)))
        assert out["gross_cu_hours"] == pytest.approx(want, abs=TOL), (c, out)
        # Only a regression layer is negative; every other layer avoids zero or more.
        for x in out["layers"]:
            if x["cu_hours"] is not None and x["cu_hours"] < 0:
                assert x["event_type"] == at.REGRESSION, (c, x)


@pytest.mark.parametrize("seed", SEEDS)
def test_this_pcs_share_is_at_most_what_the_optimized_workload_needs(seed):
    for _, c, out in cases(seed):
        b = out["baseline_cu_hours"]
        local = [x for x in out["layers"] if x["layer"] == at.LOCAL and x["cu_hours"] is not None]
        unattributed = [x for x in out["layers"] if x["layer"] == at.UNATTRIBUTED]
        if c["run"]["ran"] == at.FABRIC:
            assert not local and not unattributed, (c, out)  # a Fabric run used what it needed
            continue
        if local:
            # R: what the run needs after the optimizations in effect (the last after, or the baseline).
            opt_layers = [x for x in out["layers"] if x["optimization_id"] is not None]
            remaining = max(0.0, b - sum(x["cu_hours"] for x in opt_layers))
            assert 0 <= local[0]["cu_hours"] <= remaining + TOL, (c, out)
        for x in unattributed:
            assert 0 <= x["cu_hours"] <= b, (c, out)


def _results(rng: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        c = case(rng, f"e{i}")
        c["run"]["workload_key"] = "nb:sales"
        out.append(attribute(c))
    return out


@pytest.mark.parametrize("seed", SEEDS)
def test_period_figures_are_the_sums_of_their_runs(seed):
    rng = random.Random(seed)
    for _ in range(40):
        results = _results(rng, rng.randint(0, 12))
        with_copies = results + [dict(r) for r in rng.sample(results, k=min(3, len(results)))]  # a second device
        periods = rc.by_period(with_copies, rng.choice(rc.PERIODS))
        assert sum(p["runs"] for p in periods) == len(results)
        gross = [r["gross_cu_hours"] for r in results if r["gross_cu_hours"] is not None]
        net = [r["net_cu_hours"] for r in results if r["gross_cu_hours"] is not None and r["net_cu_hours"] is not None]
        assert sum(p["gross_cu_hours"] or 0 for p in periods) == pytest.approx(sum(gross), abs=TOL * len(results) + TOL)
        assert sum(p["net_cu_hours"] or 0 for p in periods) == pytest.approx(sum(net), abs=TOL * len(results) + TOL)
        for p in periods:
            assert p["gross_cu_hours"] is None or p["gross_cu_hours"] >= 0, p
            assert p["net_cu_hours"] is None or 0 <= p["net_cu_hours"] <= p["gross_cu_hours"] + TOL, p


@pytest.mark.parametrize("seed", SEEDS)
def test_a_projection_is_never_negative_and_is_the_known_runs_average_times_their_rate(seed):
    rng = random.Random(seed)
    now = "2026-12-01T00:00:00Z"
    for _ in range(60):
        results = _results(rng, rng.randint(0, 15))
        window = rng.choice((7, 30, 45))
        p = rc.project(results, workload_key="nb:sales", baseline_version="b1", now=now, window_days=window,
                       min_runs=rng.randint(1, 4))
        assert p["written"] is False
        if p["state"] != "projected":
            assert p["projection"]["value"] is None and p["projection"]["classification"] == ms.UNKNOWN, p
            continue
        end = ms.parse_time(now)
        known = [r["net_cu_hours"] for r in results if r["baseline_version"] == "b1" and r["net_cu_hours"] is not None
                 and end - dt.timedelta(days=window) < ms.parse_time(r["at"]) <= end]
        want = sum(known) / len(known) * len(known) * rc.DAYS_PER_MONTH / window
        assert p["projection"]["value"] >= 0 and p["projection"]["value"] == pytest.approx(want, abs=TOL), (p, known)
        assert p["projection"]["classification"] == ms.ESTIMATED  # about runs that haven't happened


def _versions(rng: random.Random) -> list[dict]:
    """One scenario's versions back to back, from 2026-11-03, each a few days long, the last open."""
    start, out = DAY0 + dt.timedelta(days=2), []
    for i in range(rng.randint(1, 5)):
        end = None if i == 4 or rng.random() < 0.2 else start + dt.timedelta(days=rng.randint(1, 9))
        out.append(pr.scenario(scenario="List", rate_per_cu_hour=round(rng.uniform(0.01, 1.5), rng.choice((2, 6))),
                               currency="USD", effective_from=start, effective_to=end,
                               pricing_model=pr.PAY_AS_YOU_GO, confidence="confirmed", source="price sheet"))
        if end is None:
            break
        start = end
    return out


@pytest.mark.parametrize("seed", SEEDS)
def test_a_price_is_the_cu_times_the_rate_in_effect_when_the_run_happened(seed):
    rng = random.Random(seed)
    for _ in range(CASES):
        versions = _versions(rng)
        cu, when = _value(rng, 40), _when(rng)
        got = pr.value(cu, when, versions, "List")
        t = ms.parse_time(when)
        live = [v for v in versions if ms.parse_time(v["effective_from"]) <= t
                and (v["effective_to"] is None or t < ms.parse_time(v["effective_to"]))]
        if not live:
            assert got["value"] is None and got["classification"] == ms.UNKNOWN and got["pricing_version"] is None
            continue
        [v] = live
        assert got["classification"] == ms.SCENARIO and got["pricing_version"] == v["pricing_version"], (got, v)
        assert got["value"] >= 0 and got["value"] == pytest.approx(cu * v["rate_per_cu_hour"], abs=TOL), (cu, v, got)


# ---------- 2. the same inputs give the same output, whatever their order

@pytest.mark.parametrize("seed", SEEDS)
def test_attribution_is_the_same_every_time_and_in_any_order_of_optimizations(seed):
    for rng, c, out in cases(seed):
        assert attribute(c) == out
        shuffled = list(c["optimizations"])
        rng.shuffle(shuffled)
        assert attribute(c, shuffled) == out, c


@pytest.mark.parametrize("seed", SEEDS)
def test_periods_projections_and_prices_dont_depend_on_the_order_of_their_inputs(seed):
    rng = random.Random(seed)
    for _ in range(40):
        results = _results(rng, rng.randint(1, 10))
        versions = _versions(rng)
        mixed, mixed_versions = list(results), list(versions)
        rng.shuffle(mixed)
        rng.shuffle(mixed_versions)
        period = rng.choice(rc.PERIODS)
        assert rc.by_period(mixed, period) == rc.by_period(results, period)
        kw = {"workload_key": "nb:sales", "baseline_version": "b1", "now": "2026-12-01T00:00:00Z"}
        assert rc.project(mixed, **kw)["projection"] == rc.project(results, **kw)["projection"]
        a, b = pr.value_runs(results, versions, "List"), pr.value_runs(mixed, mixed_versions, "List")
        assert a["total"] == b["total"] and a["pricing_versions"] == b["pricing_versions"]
        assert sorted(a["runs"], key=lambda x: x["execution_id"]) == sorted(b["runs"], key=lambda x: x["execution_id"])


def test_a_pricing_version_id_is_the_same_for_the_same_price_and_dates():
    a = pr.scenario(scenario="List", rate_per_cu_hour=0.18, currency="usd", effective_from="2026-11-01T00:00:00Z",
                    pricing_model=pr.PAY_AS_YOU_GO, confidence="confirmed", source="one PC")
    b = pr.scenario(scenario=" List ", rate_per_cu_hour=0.180000, currency="USD",
                    effective_from="2026-11-01T01:00:00+01:00", pricing_model=pr.PAY_AS_YOU_GO,
                    confidence="confirmed", source="another PC")
    assert a["pricing_version"] == b["pricing_version"]


@pytest.mark.xfail(strict=True, reason="value_runs keeps whichever attribution of a run it reads first, so two "
                                       "different figures for one run give a total that depends on their order; "
                                       "recurring.by_period refuses the same input")
def test_two_different_attributions_of_one_run_dont_make_the_priced_total_depend_on_order():
    versions = [pr.scenario(scenario="List", rate_per_cu_hour=1, currency="USD", effective_from="2026-01-01",
                            pricing_model=pr.PAY_AS_YOU_GO, confidence="confirmed", source="sheet")]
    run = {"execution_id": "e1", "workload_key": "nb:sales", "baseline_version": "b1",
           "at": "2026-11-10T00:00:00Z", "ran": at.PC}
    low = at.attribute(run, measured(10), local_avoided=estimated(2), overhead=measured(0))
    high = at.attribute(run, measured(10), local_avoided=estimated(9), overhead=measured(0))
    with pytest.raises(ValueError):
        rc.by_period([low, high])  # what the period figures do
    assert pr.value_runs([low, high], versions, "List")["total"] == pr.value_runs([high, low], versions, "List")["total"]


# ---------- 3. rounding never creates a saving

@pytest.mark.parametrize("seed", SEEDS)
def test_rounded_figures_never_exceed_the_baseline_or_the_figure_they_round(seed):
    for _, c, out in cases(seed):
        b = out["baseline_cu_hours"]
        if out["gross_cu_hours"] is None:
            continue
        assert out["gross_cu_hours"] <= b, (c, out)
        known = [x["cu_hours"] for x in out["layers"] if x["cu_hours"] is not None]
        # Each layer is rounded on its own, so their sum may differ from gross by 5e-7 a layer at most.
        assert sum(known) <= b + 5e-7 * len(known) + 1e-12, (c, out)


@pytest.mark.xfail(strict=True, reason="gross is capped at the baseline and then rounded to 6 places, so a baseline "
                                       "given in CU seconds that rounds up leaves gross above the baseline")
def test_a_baseline_in_cu_seconds_doesnt_round_gross_above_it():
    base = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, 1.9, run_id="r", source="Capacity Metrics export",
                   collected_at="2026-11-01T00:00:00Z")
    local = ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, 1.9, method="calibration", assumptions=["median"])
    run = {"execution_id": "e1", "workload_key": "nb:sales", "baseline_version": "b1",
           "at": "2026-11-10T00:00:00Z", "ran": at.PC}
    out = at.attribute(run, base, local_avoided=local, overhead=measured(0))
    # 1.9 CU seconds is 0.000527777... CU hours; gross comes back as 0.000528.
    assert out["gross_cu_hours"] <= out["baseline_cu_hours"], out


@pytest.mark.parametrize("value", EDGES)
def test_a_half_point_value_isnt_rounded_into_a_saving(value):
    run = {"execution_id": "e1", "workload_key": "nb:sales", "baseline_version": "b1",
           "at": "2026-11-10T00:00:00Z", "ran": at.PC}
    out = at.attribute(run, measured(value), [{"optimization_id": "o1", "implemented_at": "2026-11-01",
                                               "action": ACTION_ORDER[0], "state": "verified",
                                               "after": measured(value / 2)}],
                       local_avoided=estimated(value * 3), overhead=measured(value / 3))
    assert 0 <= out["net_cu_hours"] <= out["gross_cu_hours"] <= out["baseline_cu_hours"], out
    priced = pr.value(out["net_cu_hours"], "2026-11-10", [pr.from_settings()], pr.DEFAULT_SCENARIO)
    assert 0 <= priced["value"] <= round(out["net_cu_hours"] * 0.18, 6) + 1e-12, priced


# ---------- 4. with no evidence, unknown, never 0

@pytest.mark.parametrize("seed", SEEDS)
def test_no_baseline_gives_unknown_figures_not_zero(seed):
    for _, c, out in cases(seed, 100, baseline_unknown=1.0):
        assert out["baseline_cu_hours"] is None and out["gross_cu_hours"] is None and out["net_cu_hours"] is None, out
        assert out["gross_classification"] == ms.UNKNOWN and out["net_classification"] == ms.UNKNOWN
        assert all(x["cu_hours"] is None and x["classification"] == ms.UNKNOWN for x in out["layers"]), out
        assert out["complete"] is False or not out["layers"] and c["run"]["ran"] == at.FABRIC


@pytest.mark.parametrize("seed", SEEDS)
def test_an_unknown_figure_for_this_pc_is_an_unknown_layer_not_zero(seed):
    for _, c, out in cases(seed):
        if c["run"]["ran"] != at.PC or out["baseline_cu_hours"] is None:
            continue
        avoided = c["local_avoided"]
        if avoided is None or avoided["value"] is None:
            [local] = [x for x in out["layers"] if x["layer"] == at.LOCAL]
            assert local["cu_hours"] is None and local["classification"] == ms.UNKNOWN, out
            assert out["complete"] is False


@pytest.mark.parametrize("seed", SEEDS)
def test_unknown_runs_are_counted_beside_a_period_never_as_zero(seed):
    rng = random.Random(seed)
    for _ in range(30):
        results = [attribute(case(rng, f"e{i}", baseline_unknown=1.0)) for i in range(rng.randint(1, 8))]
        for p in rc.by_period(results, "month"):
            assert p["gross_cu_hours"] is None and p["net_cu_hours"] is None, p
            assert p["unknown_runs"] == p["runs"] > 0 and p["gross_classification"] == ms.UNKNOWN, p
        p = rc.project(results, workload_key=results[0]["workload_key"], baseline_version=results[0]["baseline_version"],
                       now="2026-12-01T00:00:00Z")
        assert p["state"] != "projected" and p["projection"]["value"] is None, p
        priced = pr.value_runs(results, [pr.from_settings()], pr.DEFAULT_SCENARIO)
        assert priced["total"]["value"] is None and priced["total"]["unknown_parts"] == len(priced["runs"]), priced


def test_no_figure_or_no_price_is_unknown_not_zero():
    versions = [pr.scenario(scenario="List", rate_per_cu_hour=0.2, currency="USD", effective_from="2026-11-15",
                            pricing_model=pr.PAY_AS_YOU_GO, confidence="confirmed", source="sheet")]
    for got in (pr.value(None, "2026-11-20", versions, "List"), pr.value(5, "2026-11-01", versions, "List"),
                pr.value(5, "2026-11-20", versions, "Other")):
        assert got["value"] is None and got["classification"] == ms.UNKNOWN and got["pricing_version"] is None, got
