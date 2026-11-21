"""Remediation verification (FEA-602): a fix is verified only on evidence under the same profile and rule;
measured change only when both sides were measured the same way; realized savings only from billing."""

from __future__ import annotations

import copy
import json
import math

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.excellence import assess, controls as ct, evidence as ev, profiles as pf
from fabric_desktop.excellence import verification as vf

T0, T1 = "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00"
CAP = {"type": "capacity", "id": "cap-1", "name": "F64"}
TEMPLATE = {"why": "Failures waste capacity.", "solution": "Fix the failing refresh.", "steps": ["Fix it."],
            "benefit": "Capacity freed, estimated. Not money saved.", "effort": "low", "risk": "low",
            "validation": "Assess again.", "rollback": "Undo the change."}
REF = ({"title": "Capacity Metrics app", "url": "https://learn.microsoft.com/fabric/enterprise/metrics-app"},)


def healthy(ctx: ct.Context) -> dict:
    r = ctx.one("capacity.usage")
    state = r["payload"].get("state")
    if state == "unknown":
        return ct.not_evaluated("The export had no Status column.", needs="an export with Status")
    if state == "ok":
        return ct.passed([r["id"]], "No failed operations.")
    return ct.failed([r["id"]], "Failed operations.")


def control(version: str = "1.0.0") -> ct.Control:
    return ct.Control(id="FEA-CAP-900", version=version, dimension="capacity_finops", title="Failed operations",
                      severity="medium", requirement="microsoft_recommendation", scope="capacity",
                      evidence_kinds=("capacity.usage",), evaluate=healthy, remediation=TEMPLATE, references=REF)


def registry(version: str = "1.0.0") -> ct.Registry:
    r = ct.Registry()
    r.register(control(version))
    return r


def snap(state: str | None, at: str, *, version: str = "1.0.0", profile: pf.Profile | None = None,
         confidence: str = "measured") -> dict:
    recs = [] if state is None else [ev.record("capacity.usage", CAP, source="Capacity Metrics export",
                                               confidence=confidence, collected_at=at, payload={"state": state})]
    return assess.assess(recs, registry(version), scope={"capacities": [CAP]}, assessed_at=at, profile=profile)


BEFORE = snap("fail", T0)
FINDING = BEFORE["findings"][0]


def measure(value: float, start: str = "2026-09-01T00:00:00+00:00", end: str = "2026-09-08T00:00:00+00:00", *,
            unit: str = ms.CU_SECONDS, metric: str = "cu", classification: str = ms.MEASURED, **kw) -> dict:
    extra = {"source": "Capacity Metrics export", "collected_at": end} if classification == ms.MEASURED else \
        {"method": "run time x CUs", "assumptions": ["configured CUs"]}
    return ms.make(classification, metric, unit, value, period_start=start, period_end=end,
                   **{"workload_id": "nb-1", **extra, **kw})


AFTER_M = ("2026-10-10T00:00:00+00:00", "2026-10-17T00:00:00+00:00")


def billing(**kw) -> dict:
    e = {"capacity_id": "cap-1", "source": "Azure Cost Management export cost-2026-10.csv", "confirmed_by": "pat",
         "capacity_change": {"kind": "resize", "at": "2026-10-09T00:00:00+00:00", "from_sku": "F64", "to_sku": "F32"},
         "billed": {"currency": "USD",
                    "before": {"amount": 8000.0, "start": "2026-09-01T00:00:00+00:00", "end": "2026-09-08T00:00:00+00:00"},
                    "after": {"amount": 4000.0, "start": "2026-10-10T00:00:00+00:00", "end": "2026-10-17T00:00:00+00:00"}}}
    e.update(kw)
    return e


# ---- statuses

def test_fail_then_pass_is_verified_fixed():
    r = vf.verify(FINDING, BEFORE, snap("ok", T1))
    assert r["status"] == "verified_fixed" and r["reason_code"] is None
    assert r["before"]["outcome"] == "fail" and r["after"]["outcome"] == "pass"
    assert r["before"]["evidence_ids"] and r["after"]["evidence_ids"]


def test_fail_then_fail_is_still_failing():
    assert vf.verify(FINDING, BEFORE, snap("fail", T1))["status"] == "still_failing"


def test_pass_then_fail_is_regressed():
    r = vf.verify(FINDING, snap("ok", T0), snap("fail", T1))
    assert r["status"] == "regressed"


def test_pass_then_pass_is_not_verified():
    r = vf.verify(FINDING, snap("ok", T0), snap("ok", T1))
    assert r["status"] == "not_verified" and r["reason_code"] == "not_failing"


@pytest.mark.parametrize("after", [snap(None, T1), snap("unknown", T1)])
def test_fail_then_not_evaluated_is_never_fixed(after):
    r = vf.verify(FINDING, BEFORE, after)
    assert r["after"]["outcome"] == "not_evaluated"
    assert r["status"] == "not_verified" and r["reason_code"] == "after_not_evaluated"
    assert "never fixed" in r["reason"]


def test_fail_then_not_applicable_or_error_is_not_verified():
    for outcome in ("not_applicable", "error"):
        after = snap("ok", T1)
        after["outcomes"][0].update(outcome=outcome, text="Doesn't apply.", evidence_ids=[])
        r = vf.verify(FINDING, BEFORE, after)
        assert r["status"] == "not_verified" and r["reason_code"] == f"after_{outcome}"


def test_resource_missing_after_is_not_verified():
    after = assess.assess([], registry(), scope={"capacities": []}, assessed_at=T1)
    r = vf.verify(FINDING, BEFORE, after)
    assert r["status"] == "not_verified" and r["reason_code"] == "missing_after"


def test_inferred_pass_after_is_not_verified():
    r = vf.verify(FINDING, BEFORE, snap("ok", T1, confidence="inferred"))
    assert r["status"] == "not_verified" and r["after"]["advisory"] and r["reason_code"] == "after_not_evaluated"


def test_different_profile_is_not_verified():
    other = pf.Profile(**{**pf.get().__dict__, "severity_overrides": {"FEA-CAP-900": "high"}})
    r = vf.verify(FINDING, BEFORE, snap("ok", T1, profile=other))
    assert r["status"] == "not_verified" and r["reason_code"] == "profile_changed"


def test_different_rule_version_is_not_verified():
    r = vf.verify(FINDING, BEFORE, snap("ok", T1, version="1.1.0"))
    assert r["status"] == "not_verified" and r["reason_code"] == "rule_changed"
    r = vf.verify(dict(FINDING, rule="FEA-CAP-900@0.9.0"), BEFORE, snap("ok", T1))
    assert r["status"] == "not_verified"


def test_after_not_later_is_not_verified():
    assert vf.verify(FINDING, BEFORE, snap("ok", T0))["reason_code"] == "order"
    assert vf.verify(FINDING, snap("fail", T1), snap("ok", T0))["reason_code"] == "order"


def test_fixture_against_real_is_not_verified():
    after = snap("ok", T1)
    after["fixture"] = True
    assert vf.verify(FINDING, BEFORE, after)["reason_code"] == "fixture"


def test_forged_finding_is_not_verified():
    forged = dict(FINDING, resource={"type": "capacity", "id": "cap-2"})
    assert vf.verify(forged, BEFORE, snap("ok", T1))["reason_code"] == "malformed"


def test_fixed_outcome_without_evidence_is_not_verified():
    after = snap("ok", T1)
    after["outcomes"][0]["evidence_ids"] = []
    assert vf.verify(FINDING, BEFORE, after)["reason_code"] == "no_evidence"


def test_duplicate_outcomes_are_not_verified():
    after = snap("ok", T1)
    after["outcomes"].append(copy.deepcopy(after["outcomes"][0]))
    assert vf.verify(FINDING, BEFORE, after)["reason_code"] == "malformed"


# ---- measured change

def test_same_way_measures_give_a_change():
    r = vf.verify(FINDING, BEFORE, snap("ok", T1),
                  before_measure=[measure(1000), measure(600, unit=ms.SECONDS, metric="run")],
                  after_measure=[measure(400, *AFTER_M), measure(300, *AFTER_M, unit=ms.SECONDS, metric="run")])
    cu, rt = r["measured_change"]["metrics"]["cu_seconds"], r["measured_change"]["metrics"]["run_seconds"]
    assert cu["comparable"] and cu["before"] == 1000 and cu["after"] == 400 and cu["change"] == -600
    assert cu["change_pct"] == -60.0 and "not money" in cu["text"]
    assert rt["comparable"] and rt["change"] == -300
    assert r["realized_savings"] is None  # CU freed is never money


def test_cu_hours_convert_to_seconds():
    r = vf.measured_change(measure(1, unit=ms.CU_HOURS), measure(3600, *AFTER_M))
    assert r["metrics"]["cu_seconds"]["comparable"] and r["metrics"]["cu_seconds"]["change"] == 0


@pytest.mark.parametrize("after, why", [
    (measure(400, "2026-10-10T00:00:00+00:00", "2026-10-20T00:00:00+00:00"), "different lengths"),
    (measure(400, *AFTER_M, classification=ms.ESTIMATED), "not measured"),
    (measure(400, *AFTER_M, method="sampled"), "different method"),
    (measure(400, *AFTER_M, metric="other"), "Different metrics"),
    (measure(400, *AFTER_M, workload_id="nb-2"), "different workload"),
    (measure(400, "2026-09-05T00:00:00+00:00", "2026-09-12T00:00:00+00:00"), "starts before"),
    (None, "only before"),
])
def test_not_comparable_measures(after, why):
    m = vf.measured_change(measure(1000), after)["metrics"]["cu_seconds"]
    assert not m["comparable"] and m["text"] == "Not comparable" and why in m["reason"]
    assert m["change"] is None and m["before"] is None


def test_no_measures_are_not_comparable():
    m = vf.verify(FINDING, BEFORE, snap("ok", T1))["measured_change"]["metrics"]
    assert not m["cu_seconds"]["comparable"] and not m["run_seconds"]["comparable"]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -5, True, "12", None])
def test_bad_measure_values_are_not_comparable(value):
    bad = dict(measure(1000), value=value)
    m = vf.measured_change(bad, measure(400, *AFTER_M))["metrics"]["cu_seconds"]
    assert not m["comparable"] and "isn't a valid measurement" in m["reason"]


@pytest.mark.parametrize("junk", ["x", 3, [None], {"unit": ["cu"]}, {"unit": "cu_seconds", "period": "w"},
                                  [measure(1), measure(2)], measure(5, unit=ms.CURRENCY, currency="USD")])
def test_malformed_measures_never_raise(junk):
    r = vf.measured_change(junk, measure(400, *AFTER_M))
    assert not r["metrics"]["cu_seconds"]["comparable"] or r["ignored"]


# ---- realized savings

def test_savings_null_without_billing_evidence():
    r = vf.verify(FINDING, BEFORE, snap("ok", T1), before_measure=measure(1000), after_measure=measure(1, *AFTER_M))
    assert r["realized_savings"] is None
    assert "Capacity freed is not money saved" in r["realized_savings_reason"]
    assert "nothing is saved until it renews" in r["savings_note"]


def test_billing_evidence_gives_only_the_billed_difference():
    r = vf.verify(FINDING, BEFORE, snap("ok", T1), before_measure=measure(1000), after_measure=measure(400, *AFTER_M),
                  billing_evidence=billing())
    s = r["realized_savings"]
    assert s["amount"] == 4000.0 and s["currency"] == "USD" and s["classification"] == "measured"
    assert s["source"] == "Azure Cost Management export cost-2026-10.csv" and s["capacity_change"]["kind"] == "resize"
    assert "not worked out from CU" in s["text"] and r["realized_savings_reason"] is None


def test_billed_change_without_capacity_change_counts():
    s = vf.verify(FINDING, BEFORE, snap("ok", T1), billing_evidence=billing(capacity_change=None))["realized_savings"]
    assert s["amount"] == 4000.0 and s["capacity_change"] is None


def test_capacity_change_alone_is_not_a_figure():
    r = vf.verify(FINDING, BEFORE, snap("ok", T1), billing_evidence=billing(billed=None))
    assert r["realized_savings"] is None and "no figure yet" in r["realized_savings_reason"]


@pytest.mark.parametrize("kw, why", [
    ({"pricing_model": "reserved"}, "until it renews"),
    ({"confirmed_by": ""}, "No one has confirmed"),
    ({"capacity_id": "cap-9"}, "didn't cover"),
    ({"capacity_change": {"kind": "resize", "at": "2026-12-01T00:00:00+00:00"}}, "between the two assessments"),
    ({"capacity_change": {"kind": "resize", "at": "2026-09-03T00:00:00+00:00"}}, "between the two assessments"),
])
def test_billing_evidence_that_does_not_count(kw, why):
    r = vf.verify(FINDING, BEFORE, snap("ok", T1), billing_evidence=billing(**kw))
    assert r["realized_savings"] is None and why in r["realized_savings_reason"]


def test_reserved_after_renewal_counts():
    e = billing(pricing_model="reserved", renewed_at="2026-10-09T00:00:00+00:00")
    assert vf.verify(FINDING, BEFORE, snap("ok", T1), billing_evidence=e)["realized_savings"]["amount"] == 4000.0


def test_billed_periods_must_match_and_go_down():
    e = billing()
    e["billed"]["after"]["end"] = "2026-10-20T00:00:00+00:00"
    assert "different lengths" in vf.verify(FINDING, BEFORE, snap("ok", T1), billing_evidence=e)["realized_savings_reason"]
    e = billing()
    e["billed"]["after"]["amount"] = 9000.0
    assert "didn't go down" in vf.verify(FINDING, BEFORE, snap("ok", T1), billing_evidence=e)["realized_savings_reason"]
    e = billing(capacity_change=None)
    e["billed"]["after"].update(start="2026-09-20T00:00:00+00:00", end="2026-09-27T00:00:00+00:00")
    assert "between the two assessments" in vf.verify(FINDING, BEFORE, snap("ok", T1),
                                                      billing_evidence=e)["realized_savings_reason"]


@pytest.mark.parametrize("status_after", ["fail", "unknown"])
def test_no_saving_unless_verified_fixed(status_after):
    r = vf.verify(FINDING, BEFORE, snap(status_after, T1), billing_evidence=billing())
    assert r["realized_savings"] is None and "isn't verified" in r["realized_savings_reason"]


@pytest.mark.parametrize("amount", [math.nan, math.inf, -1, "4000", None, True])
def test_bad_billed_amounts_give_no_saving(amount):
    e = billing()
    e["billed"]["after"]["amount"] = amount
    r = vf.verify(FINDING, BEFORE, snap("ok", T1), billing_evidence=e)
    assert r["realized_savings"] is None and "can't be used" in r["realized_savings_reason"]


@pytest.mark.parametrize("junk", ["x", 5, [], {}, {"capacity_id": "cap-1", "source": "s"},
                                  billing(source=""), billing(capacity_change={"kind": "grow", "at": T1}, billed=None),
                                  billing(billed={"currency": "USD", "before": 1, "after": {}}),
                                  billing(renewed_at=12)])
def test_malformed_billing_evidence_gives_no_saving(junk):
    r = vf.verify(FINDING, BEFORE, snap("ok", T1), billing_evidence=junk)
    assert r["status"] == "verified_fixed" and r["realized_savings"] is None


# ---- malformed input and determinism

@pytest.mark.parametrize("finding, before, after", [
    (None, BEFORE, BEFORE), ("x", BEFORE, BEFORE), ({}, BEFORE, BEFORE), (FINDING, None, BEFORE),
    (FINDING, BEFORE, {"schema": "x"}), (FINDING, {**BEFORE, "assessed_at": 5}, BEFORE),
    (FINDING, {**BEFORE, "outcomes": "x"}, BEFORE), (dict(FINDING, rule="no-version"), BEFORE, BEFORE),
    (FINDING, {**BEFORE, "rule_versions": None}, BEFORE), (FINDING, BEFORE, {**BEFORE, "outcomes": [1]}),
])
def test_malformed_input_is_not_verified_and_never_raises(finding, before, after):
    r = vf.verify(finding, before, after, billing_evidence=billing(), before_measure="x", after_measure=[1])
    assert r["status"] == "not_verified" and r["reason_code"] == "malformed"
    assert r["realized_savings"] is None
    json.dumps(r)


def test_deterministic_and_does_not_change_its_inputs():
    after = snap("ok", T1)
    args = (FINDING, BEFORE, after)
    kw = {"before_measure": [measure(1000)], "after_measure": [measure(400, *AFTER_M)], "billing_evidence": billing()}
    keep = copy.deepcopy((args, kw))
    a, b = vf.verify(*args, **kw), vf.verify(*copy.deepcopy(args), **copy.deepcopy(kw))
    assert a == b and a["id"].startswith("fv-")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert (args, kw) == keep
    assert vf.verify(*args)["id"] != a["id"]


def test_every_status_has_a_label_and_text_never_promises_money_from_cu():
    for s in vf.STATUSES:
        assert vf.LABELS[s]
    assert "Capacity freed is not money saved" in vf.SAVINGS_NOTE
    r = vf.verify(FINDING, BEFORE, snap("ok", T1))
    assert r["savings_note"] == vf.SAVINGS_NOTE
