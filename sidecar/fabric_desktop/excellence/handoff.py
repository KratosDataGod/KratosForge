"""Optimization planning (FEA-604): hand approved findings to the optimization engine and the F64
planner without counting anything twice.

:func:`handoff` takes an assessment's findings, the ids the person approved, and (optionally) the
``optimization_assess`` result the assessment was built from, and returns::

    {"schema", "fixture", "items": [...], "dedup": {...}, "planner_inputs": [...], "planner": {...},
     "savings_note"}

Rules it keeps:

- Only approved findings are handed off. An approved id that names no finding is reported.
- Matching is explicit. :data:`MATCHES` says, per control, which optimization engine rules look at
  the same concern. A finding is linked to an opportunity when the opportunity's rule is in its
  control's entry *and* the opportunity's artifacts include the finding's resource. A control with
  no entry is reported under ``dedup.unmapped`` and handed nowhere: no match is guessed.
- A finding the optimization engine already reports is ``already_in_optimization`` and lists the
  opportunity ids; it adds nothing of its own. Anything else in the table is a ``new_candidate``.
- Savings are never added up here, and the assessment's own figures are never cited as savings.
  A planner input cites only the optimization engine's own per-month figures (run time and CU, each a
  measurement with its classification and method), and only for an opportunity the engine counts in
  its totals; an overlapping one is cited with no figure and the opportunity it is counted under.
  So adding up every planner input can never exceed the engine's own totals.
- Capacity freed is not money saved: no currency figure is handed to the planner.
- Advisory findings (inferred evidence only) and findings without measured or verified evidence never
  become planner inputs. Nor does a finding whose control has engine rules when no optimization result
  was given: it couldn't be checked against the engine, so handing it on could count it twice.

The F64 planner (OE-040 demand model, OE-041 consolidation simulator, docs/optimize/engine-plan.md) is
not built yet. ``planner_inputs`` is in the shape documented in docs/optimize/excellence-assessment.md
and nothing consumes it yet; ``planner.consumed`` says so.

Pure and deterministic: no Fabric reads, no storage, no clock. The same inputs in any order give the
same output.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from .evidence import SCORED

SCHEMA = "kratosforge/excellence-handoff@1"

# Control id -> the optimization engine rules (opportunities/analyzers.py) that report the same concern
# on the same resource. An empty tuple: the control is handed to the planner, but no engine rule covers
# it yet. A control missing here is not handed off at all (security, governance, DevOps, reliability,
# architecture and semantic model controls aren't optimization work).
MATCHES: dict[str, tuple[str, ...]] = {
    "FEA-PERF-001": ("OE-DUP-NB", "OE-DUP-NB-SUSPECT"),
    "FEA-PERF-002": ("OE-DUP-COPY", "OE-REPEAT-EXTRACT"),
    "FEA-DE-001": ("OE-FULL-REFRESH",),
    "FEA-DE-002": ("OE-DUP-NB-PARAM",),
    "FEA-CAP-002": (),
    "FEA-CAP-003": (),
    "FEA-CAP-004": (),
}

PLANNER = {"consumed": False,
           "note": "The F64 planner (OE-040, OE-041) isn't built yet. These inputs are not consumed by anything."}
SAVINGS_NOTE = ("No savings are added up here. Figures are the optimization engine's own, per opportunity it "
                "counts in its totals, with their basis; capacity freed is not money saved.")
NO_FIGURE = "The assessment doesn't estimate savings; the optimization engine has no opportunity for this yet."


def _control(f: dict[str, Any]) -> str:
    return str(f.get("rule") or "").split("@", 1)[0]


def _problem(f: Any) -> str | None:
    if not isinstance(f, dict):
        return "not a finding record"
    if not isinstance(f.get("id"), str) or not f["id"]:
        return "no finding id"
    if not _control(f):
        return "no control rule"
    r = f.get("resource")
    if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"]:
        return "no resource id"
    if not isinstance(f.get("advisory"), bool):
        return "advisory isn't true or false"
    return None


def _opportunities(result: dict[str, Any] | None) -> tuple[list[dict[str, Any]], int]:
    """The well-formed opportunities in the result, and how many others were skipped."""
    if result is None:
        return [], 0
    raw = result.get("opportunities")
    if not isinstance(raw, list):
        return [], 0
    good, bad = [], 0
    for o in raw:
        arts = o.get("artifacts") if isinstance(o, dict) else None
        if (not isinstance(o, dict) or not isinstance(o.get("id"), str) or not isinstance(o.get("rule"), str)
                or not isinstance(arts, list) or not all(isinstance(a, dict) for a in arts)):
            bad += 1
            continue
        good.append(o)
    # Two opportunities under one id can't be told apart: linking either could cite the wrong figure, and
    # which one wins would depend on input order. Both are skipped as malformed.
    seen: dict[str, int] = {}
    for o in good:
        seen[o["id"]] = seen.get(o["id"], 0) + 1
    bad += sum(n for n in seen.values() if n > 1)
    good = [o for o in good if seen[o["id"]] == 1]
    return sorted(good, key=lambda o: o["id"]), bad


def _names(v: Any) -> list[str]:
    """A list of ids as sorted text; anything that isn't a list or tuple names nothing."""
    return sorted(str(x) for x in v) if isinstance(v, (list, tuple)) else []


def _touches(o: dict[str, Any], resource: dict[str, Any]) -> bool:
    ws = resource.get("workspace_id")
    for a in o["artifacts"]:
        if str(a.get("item_id")) != resource["id"]:
            continue
        if ws and a.get("workspace_id") and str(a["workspace_id"]) != str(ws):
            continue
        return True
    return False


def _cite(o: dict[str, Any]) -> dict[str, Any]:
    """The engine's own figures for one opportunity, only when it counts them in its totals."""
    raw = o.get("impact")
    imp: dict[str, Any] = raw if isinstance(raw, dict) else {}
    out = {"basis": imp.get("basis"), "realizable": False, "realizable_reason": imp.get("realizable_reason")}
    if o.get("counted") is True:
        return out | {"counted_in_optimization_totals": True, "run_seconds_per_month": copy.deepcopy(imp.get("run_seconds_per_month")),
                      "cu_per_month": copy.deepcopy(imp.get("cu_per_month")), "counted_under": [], "reason": None}
    under = _names(o.get("overlaps"))
    return out | {"counted_in_optimization_totals": False, "run_seconds_per_month": None, "cu_per_month": None,
                  "counted_under": under,
                  "reason": "The optimization engine counts this workload under another opportunity; citing it "
                            "here too would count it twice." if under else
                            "The optimization engine doesn't count this opportunity in its totals."}


def _plannable(f: dict[str, Any]) -> str | None:
    """Why a finding can't be a planner input, or None when it can."""
    if f["advisory"]:
        return "advisory: rests on inferred evidence only"
    c = f.get("confidence")
    if not isinstance(c, str) or c not in SCORED:
        return "its evidence isn't measured or verified"
    return None


def handoff(findings: list[dict[str, Any]], approved_ids: Iterable[str],
            optimization_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """The handoff of the approved findings, or ValueError when the inputs aren't of the right kind."""
    if not isinstance(findings, list):
        raise ValueError("findings must be a list of finding records")
    if isinstance(approved_ids, (str, bytes)) or not isinstance(approved_ids, Iterable):
        raise ValueError("approved_ids must be a collection of finding ids")
    approved = {str(i) for i in approved_ids}
    if optimization_result is not None and not isinstance(optimization_result, dict):
        raise ValueError("optimization_result must be an optimization_assess result or None")

    rejected: list[dict[str, Any]] = []
    by_id: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        why = _problem(f)
        if why:
            rejected.append({"finding_id": f.get("id") if isinstance(f, dict) and isinstance(f.get("id"), str) else None,
                             "reason": why})
            continue
        by_id.setdefault(f["id"], []).append(f)
    for fid, same in by_id.items():
        if len(same) > 1:
            rejected.append({"finding_id": fid, "reason": "the same finding id appears more than once"})
    valid = sorted((same[0] for same in by_id.values() if len(same) == 1), key=lambda f: f["id"])

    fixture = any(bool(f.get("fixture")) for f in valid)
    if fixture and not all(bool(f.get("fixture")) for f in valid):
        raise ValueError("a demonstration fixture can't be mixed with a real assessment's findings")
    if optimization_result is not None and valid and bool(optimization_result.get("fixture")) != fixture:
        raise ValueError("a demonstration fixture can't be matched with a real assessment")
    opps, bad_opps = _opportunities(optimization_result)
    opp_by_id = {o["id"]: o for o in opps}

    items, unmapped, not_checked = [], [], []
    linked: dict[str, list[str]] = {}
    planner: dict[str, dict[str, Any]] = {}
    chosen = [f for f in valid if f["id"] in approved]
    for f in chosen:
        cid = _control(f)
        if cid not in MATCHES:
            unmapped.append({"finding_id": f["id"], "control_id": cid,
                             "reason": "No optimization mapping for this control; it isn't handed off."})
            continue
        rules = MATCHES[cid]
        if rules and optimization_result is None:
            match, ids = "not_checked", []
            not_checked.append(f["id"])
        elif rules:
            ids = [o["id"] for o in opps if o["rule"] in rules and _touches(o, f["resource"])]
            match = "matched" if ids else "no_opportunity"
        else:
            match, ids = "no_rule", []
        action = "already_in_optimization" if ids else "new_candidate"
        why_not = _plannable(f) or ("not checked against the optimization engine" if match == "not_checked" else None)
        key = None
        for oid in ids:
            linked.setdefault(oid, []).append(f["id"])
        if why_not is None:
            if ids:
                for oid in ids:
                    planner.setdefault("opp:" + oid, {"key": "opp:" + oid, "source": "optimization_engine",
                                                      "opportunity_id": oid, "finding_ids": [],
                                                      "rule": opp_by_id[oid]["rule"],
                                                      "affects": _names(opp_by_id[oid].get("affects")),
                                                      **_cite(opp_by_id[oid])})["finding_ids"].append(f["id"])
                key = ["opp:" + oid for oid in ids]
            else:
                planner["finding:" + f["id"]] = {
                    "key": "finding:" + f["id"], "source": "excellence_assessment", "opportunity_id": None,
                    "finding_ids": [f["id"]], "rule": f["rule"], "affects": [f["resource"]["id"]], "basis": None,
                    "realizable": False, "realizable_reason": None, "counted_in_optimization_totals": False,
                    "run_seconds_per_month": None, "cu_per_month": None, "counted_under": [], "reason": NO_FIGURE}
                key = ["finding:" + f["id"]]
        items.append({"finding_id": f["id"], "control_id": cid, "rule": f["rule"], "resource": copy.deepcopy(f["resource"]),
                      "opportunity_ids": ids, "action": action, "match": match, "advisory": f["advisory"],
                      "confidence": f.get("confidence"), "planner_keys": key or [], "not_planned_reason": why_not})

    for p in planner.values():
        p["finding_ids"] = sorted(p["finding_ids"])
    known = {f["id"] for f in valid} | {r["finding_id"] for r in rejected if r["finding_id"]}
    return {"schema": SCHEMA, "fixture": fixture, "items": items,
            "dedup": {"already_in_optimization": sum(1 for i in items if i["action"] == "already_in_optimization"),
                      "new_candidates": sum(1 for i in items if i["action"] == "new_candidate"),
                      "opportunities_linked": sorted(linked),
                      "shared_opportunities": [{"opportunity_id": o, "finding_ids": sorted(fs)}
                                               for o, fs in sorted(linked.items()) if len(fs) > 1],
                      "not_approved": sum(1 for f in valid if f["id"] not in approved),
                      "unknown_approved_ids": sorted(approved - known),
                      "unmapped": unmapped, "not_checked": not_checked,
                      "rejected": sorted(rejected, key=lambda r: (str(r["finding_id"]), r["reason"])),
                      "malformed_opportunities": bad_opps},
            "planner_inputs": [planner[k] for k in sorted(planner)], "planner": dict(PLANNER),
            "savings_note": SAVINGS_NOTE}
