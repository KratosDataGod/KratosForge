"""Remediation verification: was a finding fixed, and what did the fix change (FEA-602).

``verify`` takes a finding, the snapshot it was found in (or any earlier one) and a later snapshot,
and says what happened to the control on that resource:

- ``verified_fixed``: the control failed before and passes after, on evidence, under the same
  profile and the same rule version;
- ``still_failing``: it fails in both;
- ``regressed``: it passed before and fails after;
- ``not_verified``: anything else, with the reason. A control that went from fail to not evaluated,
  error or not applicable is not verified, never fixed: missing evidence can't pass. Snapshots
  assessed with a different profile or a different version of the rule can't be compared, so they
  are not verified either.

Measured change (CU seconds, run time) is given only when both sides were measured the same way: the
same metric, both ``measured`` (:mod:`fabric_desktop.measurement`), the same method, windows of the
same length, the after window not before the before window, and the same workload. Otherwise the
metric is "not comparable", with the reason. A measured change never decides the status.

Realized savings are ``None`` unless billing evidence shows a billed amount that went down between a
period before and an equally long period after, starting in the verification window, for a capacity
the snapshots covered, with a person's confirmation that the fix caused it. Even then the figure is
only the billed difference, cited with its source. It is never worked out from CU freed: capacity
freed is not money saved, and on a reservation nothing is saved until it renews. A capacity resize,
pause or SKU change in the window is recorded, but it is not a figure until a billing export shows
what was billed.

Pure: no clock, no I/O, no Fabric reads. The same inputs always give the same result, and malformed
input gives ``not_verified`` (or no figure) with the reason, never an exception and never a fix.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from .. import measurement as ms
from ..timeutil import iso, parse_time
from . import findings as fx
from .evidence import canonical

SCHEMA = "kratosforge/excellence-verification@1"
VERIFIED_FIXED, STILL_FAILING, NOT_VERIFIED, REGRESSED = "verified_fixed", "still_failing", "not_verified", "regressed"
STATUSES = (VERIFIED_FIXED, STILL_FAILING, NOT_VERIFIED, REGRESSED)
LABELS = {VERIFIED_FIXED: "Verified fixed", STILL_FAILING: "Still failing", NOT_VERIFIED: "Not verified",
          REGRESSED: "Regressed"}
CAPACITY_CHANGES = ("resize", "pause", "sku_change")
RESERVED = "reserved"
SAVINGS_NOTE = ("Capacity freed is not money saved. Money is saved only when a capacity is resized, paused or its "
                "SKU changed and the bill shows it; on a reservation, nothing is saved until it renews.")
_METRICS = {"cu_seconds": ("capacity", ms.CU_SECONDS, "CU seconds"), "run_seconds": ("time", ms.SECONDS, "run time")}
_DECIDED = ("pass", "fail")


# ---- small checks

def _time(value: Any) -> Any:
    """An ISO time string as an aware datetime, else None. Numbers and booleans aren't taken as times."""
    return parse_time(value) if isinstance(value, str) else None


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _snapshot_problems(s: Any, which: str) -> list[str]:
    if not isinstance(s, dict):
        return [f"the {which} snapshot isn't an assessment snapshot"]
    out = []
    if s.get("schema") != "kratosforge/excellence-assessment@1":
        out.append(f"the {which} snapshot has an unknown schema")
    if not _time(s.get("assessed_at")):
        out.append(f"the {which} snapshot has no assessment time")
    if not isinstance(s.get("outcomes"), list) or not all(isinstance(o, dict) for o in s.get("outcomes") or []):
        out.append(f"the {which} snapshot has no outcomes list")
    if not isinstance(s.get("rule_versions"), dict):
        out.append(f"the {which} snapshot has no rule versions")
    if not isinstance(s.get("profile"), dict):
        out.append(f"the {which} snapshot has no profile")
    return out


def _finding_problems(f: Any) -> list[str]:
    if not isinstance(f, dict):
        return ["the finding isn't a finding record"]
    out = []
    res = f.get("resource")
    if not _text(f.get("id")):
        out.append("the finding has no id")
    if not _text(f.get("rule")) or "@" not in str(f.get("rule")):
        out.append("the finding has no rule version")
    if not isinstance(res, dict) or not _text(res.get("id")):
        out.append("the finding has no resource")
    if not out and f["id"] != fx.finding_id(f["rule"].split("@", 1)[0], res["id"]):
        out.append("the finding's id doesn't match its control and resource")
    return out


def _outcome(snapshot: dict[str, Any], control_id: str, subject_id: str) -> tuple[dict[str, Any] | None, str | None]:
    mine = [o for o in snapshot["outcomes"] if o.get("control_id") == control_id
            and isinstance(o.get("subject"), dict) and o["subject"].get("id") == subject_id]
    if len(mine) > 1:
        return None, "more than one outcome for this control on this resource"
    return (mine[0], None) if mine else (None, None)


def _brief(o: dict[str, Any] | None) -> dict[str, Any] | None:
    if o is None:
        return None
    return {"outcome": o.get("outcome"), "rule": o.get("rule"), "basis": o.get("basis"), "text": o.get("text", ""),
            "evidence_ids": sorted(str(i) for i in o.get("evidence_ids") or []), "needs": o.get("needs"),
            "advisory": bool(o.get("advisory"))}


# ---- status

def _not_verified(code: str, reason: str) -> tuple[str, str, str]:
    return NOT_VERIFIED, code, reason


def _status(finding: Any, before: Any, after: Any) -> tuple[tuple[str, str | None, str], dict | None, dict | None]:
    bad = _finding_problems(finding) + _snapshot_problems(before, "before") + _snapshot_problems(after, "after")
    if bad:
        return _not_verified("malformed", "Can't verify: " + "; ".join(bad) + "."), None, None
    control_id, rule_version = finding["rule"].split("@", 1)
    subject_id = finding["resource"]["id"]
    o_before, amb_b = _outcome(before, control_id, subject_id)
    o_after, amb_a = _outcome(after, control_id, subject_id)
    b, a = _brief(o_before), _brief(o_after)
    if amb_b or amb_a:
        return _not_verified("malformed", f"Can't verify: {amb_b or amb_a}."), b, a
    if _time(after["assessed_at"]) <= _time(before["assessed_at"]):
        return _not_verified("order", "The after snapshot isn't later than the before snapshot."), b, a
    if bool(before.get("fixture")) != bool(after.get("fixture")):
        return _not_verified("fixture", "One snapshot is a demonstration fixture and the other isn't."), b, a
    if canonical(before["profile"]) != canonical(after["profile"]):
        return _not_verified("profile_changed", "The two snapshots used a different assessment profile, so a change "
                                                "may come from the profile, not from a fix."), b, a
    vb, va = before["rule_versions"].get(control_id), after["rule_versions"].get(control_id)
    if vb != va or vb != rule_version:
        return _not_verified("rule_changed", f"The rule {control_id} isn't the same version in both snapshots and the "
                                             f"finding ({rule_version}, before {vb}, after {va}), so a change may "
                                             "come from the rule, not from a fix."), b, a
    for o, which in ((o_before, "before"), (o_after, "after")):
        if o is not None and o.get("rule") != finding["rule"]:
            return _not_verified("rule_changed", f"The {which} outcome was given by {o.get('rule')}, "
                                                 f"not {finding['rule']}."), b, a
    if o_before is None:
        return _not_verified("missing_before", "The before snapshot has no outcome for this control on this "
                                               "resource."), b, a
    if o_after is None:
        return _not_verified("missing_after", "The after snapshot has no outcome for this control on this resource: "
                                              "it was removed or not assessed. That isn't a verified fix."), b, a
    ob, oa = o_before.get("outcome"), o_after.get("outcome")
    if ob not in _DECIDED:
        why = ("it rested only on inferred evidence (advisory)" if o_before.get("advisory")
               else f"it was {ob or 'missing'}")
        return _not_verified("before_undecided", f"The control didn't fail on evidence before: {why}."), b, a
    if oa not in _DECIDED:
        text = f" ({o_after.get('text')})" if o_after.get("text") else ""
        return _not_verified(f"after_{oa if oa in ('not_evaluated', 'not_applicable', 'error') else 'undecided'}",
                             f"The control was {str(oa).replace('_', ' ')} after{text}. A control that couldn't be "
                             "checked is not verified, never fixed."), b, a
    if not o_after.get("evidence_ids") or not o_before.get("evidence_ids"):
        return _not_verified("no_evidence", "An outcome cites no evidence."), b, a
    if ob == "fail" and oa == "pass":
        return (VERIFIED_FIXED, None, ("The control failed before and passes after, on evidence, under the same "
                                       "profile and rule version.")), b, a
    if ob == "fail":
        return (STILL_FAILING, None, "The control fails in both snapshots."), b, a
    if oa == "fail":
        return (REGRESSED, None, "The control passed before and fails after."), b, a
    return _not_verified("not_failing", "The control passed in both snapshots, so there is no fix to verify "
                                        "in this window."), b, a


# ---- measured change

def _measures(value: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """``value`` (a measurement, a list of them, or None) by metric key; and what was wrong."""
    if value is None:
        return {}, []
    items = value if isinstance(value, list) else [value]
    out: dict[str, dict[str, Any]] = {}
    bad: list[str] = []
    for m in items:
        if not isinstance(m, dict):
            bad.append("an entry isn't a measurement")
            continue
        if not isinstance(m.get("unit"), str) or not isinstance(m.get("period") or {}, dict) \
                or not isinstance(m.get("classification"), str):
            bad.append("an entry isn't a measurement")
            continue
        found = ms.problems(m)
        family = ms.UNITS.get(m["unit"])
        key = next((k for k, (fam, _, _) in _METRICS.items() if fam == family), None)
        if key is None:
            bad.append(f"{m.get('metric') or 'a measurement'}: only CU and run time are compared")
            continue
        if key in out:
            bad.append(f"more than one {_METRICS[key][2]} measurement")
            out[key] = {"_invalid": f"more than one {_METRICS[key][2]} measurement"}
            continue
        out[key] = {"_invalid": "; ".join(found)} if found else m
    return out, bad


def _window(m: dict[str, Any]) -> tuple[Any, Any, float | None]:
    p = m.get("period") or {}
    s, e = _time(p.get("start")), _time(p.get("end"))
    return s, e, ((e - s).total_seconds() if s and e else None)


def _compare(key: str, mb: dict[str, Any] | None, ma: dict[str, Any] | None) -> dict[str, Any]:
    _, unit, label = _METRICS[key]
    base = {"metric": key, "label": label, "unit": unit, "comparable": False, "before": None, "after": None,
            "change": None, "change_pct": None}

    def no(reason: str) -> dict[str, Any]:
        return {**base, "text": "Not comparable", "reason": reason}

    if mb is None and ma is None:
        return no(f"{label} wasn't measured on either side.")
    if mb is None or ma is None:
        return no(f"{label} was measured only {'after' if mb is None else 'before'}.")
    for m, which in ((mb, "before"), (ma, "after")):
        if "_invalid" in m:
            return no(f"The {which} {label} isn't a valid measurement: {m['_invalid']}.")
        if m["classification"] != ms.MEASURED:
            return no(f"The {which} {label} is {m['classification']}, not measured.")
    if mb["metric"] != ma["metric"]:
        return no(f"Different metrics: {mb['metric']} before, {ma['metric']} after.")
    if (mb.get("method") or "") != (ma.get("method") or ""):
        return no("Measured with a different method before and after.")
    for k in ("workload_id", "workspace_id", "capacity_id"):
        if mb.get(k) and ma.get(k) and mb[k] != ma[k]:
            return no(f"The two measurements are of a different {k.replace('_id', '')}.")
    sb, eb, lb = _window(mb)
    sa, _, la = _window(ma)
    if lb is None or la is None:
        return no("A measurement has no window, so the two can't be matched.")
    if lb != la:
        return no(f"The windows are different lengths ({lb:g} s before, {la:g} s after).")
    if sa < eb:
        return no("The after window starts before the before window ends.")
    vb = ms.convert(mb["value"], mb["unit"], unit)
    va = ms.convert(ma["value"], ma["unit"], unit)
    change = round(va - vb, 6)
    return {**base, "comparable": True, "before": round(vb, 6), "after": round(va, 6), "change": change,
            "change_pct": round(change / vb * 100, 1) if vb else None, "window_seconds": lb,
            "sources": {"before": mb.get("source"), "after": ma.get("source")},
            "text": f"Measured {label} {'down' if change < 0 else 'up' if change > 0 else 'unchanged'}: "
                    f"{vb:g} to {va:g} over equal windows of {lb:g} s. This is capacity, not money."}


def measured_change(before_measure: Any = None, after_measure: Any = None) -> dict[str, Any]:
    """CU seconds and run time before and after, each compared only when measured the same way."""
    b, bad_b = _measures(before_measure)
    a, bad_a = _measures(after_measure)
    return {"metrics": {k: _compare(k, b.get(k), a.get(k)) for k in _METRICS},
            "ignored": sorted({*(f"before: {x}" for x in bad_b), *(f"after: {x}" for x in bad_a)})}


# ---- realized savings

def _billing_problems(e: dict[str, Any]) -> list[str]:
    out = []
    if not _text(e.get("capacity_id")):
        out.append("no capacity")
    if not _text(e.get("source")):
        out.append("no source")
    change, billed = e.get("capacity_change"), e.get("billed")
    if change is None and billed is None:
        out.append("neither a capacity change nor billed amounts")
    if change is not None:
        if not isinstance(change, dict) or change.get("kind") not in CAPACITY_CHANGES or not _time(change.get("at")):
            out.append("the capacity change needs a kind (" + ", ".join(CAPACITY_CHANGES) + ") and a time")
    if billed is not None:
        ok = isinstance(billed, dict) and _text(billed.get("currency"))
        for side in ("before", "after"):
            p = billed.get(side) if isinstance(billed, dict) else None
            ok = ok and isinstance(p, dict) and _number(p.get("amount")) and bool(_time(p.get("start"))) \
                and bool(_time(p.get("end"))) and _time(p["end"]) > _time(p["start"])
        if not ok:
            out.append("billed amounts need a currency and, before and after, a finite amount of 0 or more "
                       "and a period that ends after it starts")
    if e.get("renewed_at") is not None and not _time(e.get("renewed_at")):
        out.append("renewed_at isn't a time")
    return out


def realized_savings(billing_evidence: Any, *, status: str, before_snapshot: Any, after_snapshot: Any,
                     resource: Any = None) -> tuple[dict[str, Any] | None, str]:
    """The billed difference with its source, or None and why. Never derived from CU."""
    if billing_evidence is None:
        return None, "No billing or capacity change evidence was given. " + SAVINGS_NOTE
    if not isinstance(billing_evidence, dict):
        return None, "The billing evidence isn't a record, so no saving is shown."
    bad = _billing_problems(billing_evidence)
    if bad:
        return None, "The billing evidence can't be used: " + "; ".join(bad) + "."
    if status != VERIFIED_FIXED:
        return None, "The fix isn't verified, so no saving is credited to it."
    e = billing_evidence
    def covered(s: dict[str, Any]) -> set[str]:
        scope = s.get("scope") if isinstance(s.get("scope"), dict) else {}
        listed = scope.get("capacities") if isinstance(scope.get("capacities"), list) else []
        return {str(c.get("id")) for c in listed if isinstance(c, dict)}

    caps = covered(before_snapshot) & covered(after_snapshot)
    if isinstance(resource, dict) and resource.get("type") == "capacity":
        caps.add(str(resource.get("id")))
    if e["capacity_id"] not in caps:
        return None, "The billing evidence is for a capacity these snapshots didn't cover."
    start, end = _time(before_snapshot["assessed_at"]), _time(after_snapshot["assessed_at"])
    change = e.get("capacity_change")
    if change is not None and not start <= _time(change["at"]) <= end:
        return None, "The capacity change isn't between the two assessments."
    billed = e.get("billed")
    if billed is None:
        return None, (f"The capacity had a {change['kind'].replace('_', ' ')} on {iso(change['at'])} ({e['source']}), "
                      "but no billed amounts before and after were given, so there is no figure yet. "
                      + SAVINGS_NOTE)
    pb, pa = billed["before"], billed["after"]
    sb, eb, sa, ea = (_time(pb["start"]), _time(pb["end"]), _time(pa["start"]), _time(pa["end"]))
    if (eb - sb) != (ea - sa):
        return None, "The billed periods before and after are different lengths, so they can't be compared."
    if sa < eb:
        return None, "The billed period after starts before the period before ends."
    if not start <= sa <= end:
        return None, "The billed period after doesn't start between the two assessments."
    if change is not None and not (eb <= _time(change["at"]) <= sa):
        return None, "The capacity change isn't between the two billed periods."
    if e.get("pricing_model") == RESERVED and not (e.get("renewed_at") and _time(e["renewed_at"]) <= sa):
        return None, ("The capacity is on a reservation that hasn't renewed before the billed period after: on a "
                      "reservation, nothing is saved until it renews.")
    if not _text(e.get("confirmed_by")):
        return None, "No one has confirmed that this billing change came from this fix, so no saving is credited."
    diff = round(float(pb["amount"]) - float(pa["amount"]), 2)
    if diff <= 0:
        return None, "The billed amount didn't go down, so there is no realized saving."
    cur = billed["currency"]
    return ({"classification": ms.MEASURED, "amount": diff, "currency": cur, "capacity_id": e["capacity_id"],
             "source": e["source"], "confirmed_by": e["confirmed_by"],
             "before": {"amount": pb["amount"], "start": iso(pb["start"]), "end": iso(pb["end"])},
             "after": {"amount": pa["amount"], "start": iso(pa["start"]), "end": iso(pa["end"])},
             "capacity_change": None if change is None else {"kind": change["kind"], "at": iso(change["at"]),
                                                             "from_sku": change.get("from_sku"),
                                                             "to_sku": change.get("to_sku")},
             "text": f"Billed {diff:g} {cur} less over equal periods, from {e['source']}. This is the billed "
                     "difference for the whole capacity, not worked out from CU."}, "")


# ---- the call

def verify(finding: Any, before_snapshot: Any, after_snapshot: Any, *, before_measure: Any = None,
           after_measure: Any = None, billing_evidence: Any = None) -> dict[str, Any]:
    """Whether ``finding`` was fixed between ``before_snapshot`` and ``after_snapshot``, what was measured
    to change, and the realized saving if billing evidence shows one. Deterministic; never raises on data."""
    (status, code, reason), ob, oa = _status(finding, before_snapshot, after_snapshot)
    change = measured_change(before_measure, after_measure)
    snaps_ok = not _snapshot_problems(before_snapshot, "before") and not _snapshot_problems(after_snapshot, "after")
    if snaps_ok:
        saving, why = realized_savings(billing_evidence, status=status, before_snapshot=before_snapshot,
                                       after_snapshot=after_snapshot,
                                       resource=finding.get("resource") if isinstance(finding, dict) else None)
    else:
        saving, why = None, "The snapshots can't be used, so no saving is shown."
    fid = finding.get("id") if isinstance(finding, dict) else None
    out = {"schema": SCHEMA, "finding_id": fid if isinstance(fid, str) else None,
           "rule": finding.get("rule") if isinstance(finding, dict) and isinstance(finding.get("rule"), str) else None,
           "status": status, "label": LABELS[status], "reason_code": code, "reason": reason,
           "before": ob, "after": oa,
           "from": iso(before_snapshot.get("assessed_at")) if isinstance(before_snapshot, dict) else None,
           "to": iso(after_snapshot.get("assessed_at")) if isinstance(after_snapshot, dict) else None,
           "measured_change": change, "realized_savings": saving, "realized_savings_reason": why or None,
           "savings_note": SAVINGS_NOTE}
    out["id"] = "fv-" + hashlib.sha256(canonical(out).encode()).hexdigest()[:16]
    return out
