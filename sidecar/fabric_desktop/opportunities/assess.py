"""One assessment: workloads in, ranked opportunities, honest totals and the next action out (OE-001).

Totals never double count. Savings are added per workload, not per opportunity: when two
opportunities touch the same workload (a notebook that is a duplicate *and* does full refreshes),
the one earlier in the six-question order counts (eliminating it makes optimizing it moot), and
the other is marked as overlapping with it. An opportunity that would reduce a workload by an
amount not yet measured adds an Unknown part to the total, which makes the total incomplete
rather than bigger.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .. import measurement as ms
from . import analyzers, impact, inventory, model

_CONF = {c: i for i, c in enumerate(model.CONFIDENCE)}
_ACT = {a: i for i, a in enumerate(model.ACTION_ORDER)}
_LVL = {lvl: i for i, lvl in enumerate(model.LEVELS)}
FIXTURE_NOTE = ("Demonstration fixture: made-up workspace and runs that show how the engine works. "
                "Nothing here was read from Fabric, and none of it is a finding or a saving.")


def _strength(o: dict[str, Any]) -> tuple[int, int]:
    return (_ACT[o["action"]], _CONF[o["confidence"]])


def combine(opps: list[dict[str, Any]], workloads: list[dict[str, Any]], usage: dict[tuple[str, str], dict[str, Any]],
            now: dt.datetime, price: dict[str, Any] | None) -> dict[str, Any]:
    """Mark overlaps and add up what the counted opportunities avoid, once per workload."""
    by_id = {w["item_id"]: w for w in workloads}
    winner: dict[str, dict[str, Any]] = {}
    for o in opps:
        o["overlaps"] = []
        for item in o["affects"]:
            if item not in winner or _strength(o) < _strength(winner[item]):
                winner[item] = o
    for o in opps:
        lost = [winner[i]["id"] for i in o["affects"] if winner[i] is not o]
        o["overlaps"] = sorted(set(lost) | {x["id"] for x in opps if x is not o and set(x["affects"]) & set(o["affects"])})
        o["counted"] = bool(o["affects"]) and not lost
    secs, cus = [], []
    for item, o in sorted(winner.items()):
        w = by_id[item]
        if o["impact"].get("basis") == "part of the workload":
            reason = o["impact"]["run_seconds_per_month"]["reason"]
            secs.append(ms.unknown("fabric_run_seconds_avoided", ms.SECONDS, reason, workload_id=item))
            cus.append(ms.unknown("fabric_cu_avoided", ms.CU_SECONDS, reason, workload_id=item))
        else:
            got = impact.per_workload(w, usage, now)
            secs.append(got["run_seconds_per_month"])
            cus.append(got["cu_per_month"])
    cu_total = ms.total(cus, "fabric_cu_avoided", ms.CU_HOURS)
    return {"workloads_affected": len(winner),
            "run_seconds_per_month": ms.total(secs, "fabric_run_seconds_avoided", ms.SECONDS),
            "cu_per_month": cu_total,
            "cu_value_per_month": impact.value(cu_total, price, {}) if cu_total["complete"] else
            ms.unknown("cu_value", ms.CURRENCY, "Part of the CU figure isn't known, so no value is given."),
            "realizable": False, "realizable_reason": impact.NOT_REALIZABLE,
            "verified": ms.unknown("fabric_cu_avoided_verified", ms.CU_HOURS,
                                   "Nothing has been implemented and measured yet.")}


def _known(o: dict[str, Any]) -> float:
    v = (o["impact"].get("run_seconds_per_month") or {}).get("value")
    return float(v) if isinstance(v, (int, float)) else -1.0


def rank(opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Counted before overlapping; then surer, bigger (known), easier, safer, earlier in the six questions."""
    return sorted(opps, key=lambda o: (not o.get("counted"), _CONF[o["confidence"]], -_known(o), _LVL[o["effort"]],
                                       _LVL[o["risk"]], _ACT[o["action"]], o["id"]))


def _coverage(workloads: list[dict[str, Any]], usage_items: int) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for w in workloads:
        kinds[w["type"]] = kinds.get(w["type"], 0) + 1
    return {"items": len(workloads), "by_type": dict(sorted(kinds.items())),
            "analyzed": sum(1 for w in workloads if w["definition"] == "read"),
            "not_read": [{"item_id": w["item_id"], "name": w["name"], "type": w["type"], "problem": w["problem"]}
                         for w in workloads if w["definition"] == "not_read"],
            "not_analyzed_types": sorted({w["type"] for w in workloads if w["definition"] == "unsupported"}),
            "history_not_read": sum(1 for w in workloads if not w["runs"]["read"]),
            "usage_items": usage_items,
            "analyzers": [a.__name__ for a in analyzers.ANALYZERS]}


def _next_action(ranked: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, Any]:
    top = next((o for o in ranked if o.get("counted")), ranked[0] if ranked else None)
    if top is not None:
        return {"opportunity_id": top["id"], "title": top["title"], **top["next_step"]}
    if coverage["not_read"]:
        return {"kind": "fix_access", "label": "Some definitions couldn't be read; check your access to them",
                "items": coverage["not_read"]}
    return {"kind": "none", "label": "No opportunity found in what these analyzers check. That isn't proof there "
                                     "is none: the coverage list says what was read."}


def assess(sources: list[dict[str, Any]], *, usage_imports: list[dict[str, Any]], price: dict[str, Any] | None,
           now: dt.datetime, days: int = 30, scope: dict[str, Any] | None = None, fixture: bool = False) -> dict[str, Any]:
    """The assessment of one or more workspaces. ``sources``: one dict per workspace with
    ``workspace``, ``items``, ``definitions`` and ``jobs`` as inventory.workloads takes them."""
    workloads = [w for s in sources for w in inventory.workloads(s["workspace"], s["items"], s.get("definitions") or {},
                                                                 s.get("jobs") or {}, now=now, days=days)]
    usage = impact.usage_by_item(usage_imports)
    ctx = analyzers.Context(usage=usage, now=now, price=price)
    opps = [o for a in analyzers.ANALYZERS for o in a(workloads, ctx)]
    totals = combine(opps, workloads, usage, now, price)
    ranked = rank(opps)
    if fixture:
        for o in ranked:
            o["fixture"] = True
    coverage = _coverage(workloads, len(usage))
    for w in workloads:  # code stays on this PC's screen only; the result carries what it found, not the code
        w.pop("cells", None)
        w.pop("activities", None)
    return {"scope": scope or {}, "fixture": fixture, "fixture_note": FIXTURE_NOTE if fixture else None,
            "assessed_at": now.isoformat(timespec="seconds"), "days": days,
            "workspaces": [{"id": s["workspace"].get("id"), "name": s["workspace"].get("displayName"),
                            "capacity_id": s["workspace"].get("capacityId"), "problem": s.get("problem")}
                           for s in sources],
            "price": price, "workloads": workloads, "opportunities": ranked, "totals": totals,
            "counts": {"opportunities": len(ranked), "counted": sum(1 for o in ranked if o["counted"]),
                       "by_category": _count(ranked, "category"), "by_confidence": _count(ranked, "confidence"),
                       "by_action": _count(ranked, "action")},
            "coverage": coverage, "next_action": _next_action(ranked, coverage)}


def _count(opps: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for o in opps:
        out[o[key]] = out.get(o[key], 0) + 1
    return out
