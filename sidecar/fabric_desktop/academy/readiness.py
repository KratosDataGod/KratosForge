"""Academy readiness (ACD-006): where a learner stands on each exam objective, from their evidence.

Worked out every time it is shown from the profile's included evidence rows (progress.py), never stored,
so excluding a row takes it out here too. Six separate measures per objective, never blended into one
number:

    knowledge         assessment items answered correctly, of those answered (academy.assessment rows)
    guided            guided exercises passed
    independent       challenge exercises passed with at most one hint and the explanation closed
    troubleshooting   broken environments repaired
    platform_verified exercises passed in the learner's own workspace on the platform (FABRIC VERIFIED,
                      DATABRICKS VERIFIED)
    retention         exercises passed again on a day at least RETENTION_DAYS after the first pass

An objective is **shown** when the learner has practical evidence whose class completes it
(exercises.completes): an objective that needs the platform needs its verified class; any other needs
evidence at least as strong as the best this PC can give for it. It is **practised** when there is
evidence that doesn't complete it, and **not started** otherwise.

Rows earned under another version of the study guide are never rewritten. They count for this version
only through the certification's ``crosswalk`` (``{old version: {old objective: [new objectives]}}``);
without one they're listed as earlier work and left out of the measures.

The result is KratosForge's own estimate. It is labelled so and never presented as the vendor's score
or as a prediction of passing (``note()``).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .. import progress
from . import exercises, registry

RETENTION_DAYS = 7
INDEPENDENT_MAX_HINTS = 1
ESTIMATE_LABEL = "KratosForge readiness estimate"
_NOTE_TEMPLATE = ("This is KratosForge's own estimate from the evidence on this PC. It isn't {vendors} score and it "
                 "doesn't promise a pass.")


def note(platform: str = registry.DEFAULT_PLATFORM) -> str:
    """The estimate's note for a platform's certification, naming its vendor."""
    return _NOTE_TEMPLATE.format(vendors=registry.PLATFORMS[platform]["vendors"])


MEASURES = ("knowledge", "guided", "independent", "troubleshooting", "platform_verified", "retention")


def _day(stamp: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(stamp or "")[:10])
    except ValueError:
        return None


def _objectives_for(row: dict[str, Any], cert: dict[str, Any]) -> list[str] | None:
    """The objectives of this certification version a row counts for, or None when it doesn't."""
    detail = row.get("detail") or {}
    ids = [str(o) for o in detail.get("objectives") or []]
    if detail.get("certification_version") == cert["version"]:
        return ids
    walk = (cert.get("crosswalk") or {}).get(str(detail.get("certification_version")))
    if not isinstance(walk, dict):
        return None
    return sorted({n for o in ids for n in walk.get(o) or []})


def _empty() -> dict[str, Any]:
    return {"knowledge": {"correct": 0, "answered": 0}, "guided": set(), "independent": set(), "troubleshooting": set(),
            "platform_verified": set(), "retention": set(), "classes": set(), "passes": {}}


def readiness(profile: dict[str, Any], pack_id: str = "dp-700") -> dict[str, Any]:
    """Readiness for one certification: per objective, per domain, what to do next, and the estimate's label."""
    cert = registry.certification(pack_id)
    plat = registry.platform(cert)
    objs = registry.objectives(cert)
    acc: dict[str, dict[str, Any]] = {o["id"]: _empty() for o in objs}
    earlier = 0
    for r in progress.included(profile):
        if r.get("type") not in ("academy.exercise", "academy.assessment") or (r.get("detail") or {}).get("certification") != pack_id:
            continue
        targets = _objectives_for(r, cert)
        if targets is None:
            earlier += 1
            continue
        detail = r["detail"]
        for oid in (t for t in targets if t in acc):
            a = acc[oid]
            if r["type"] == "academy.assessment":
                per = (detail.get("per_objective") or {}).get(oid) or {}
                a["knowledge"]["correct"] += int(per.get("correct") or 0)
                a["knowledge"]["answered"] += int(per.get("answered") or 0)
                continue
            eid, cls, mode = str((r.get("ref") or {}).get("id")), detail.get("class"), detail.get("mode")
            a["classes"].add(cls)
            a["passes"].setdefault(eid, []).append(_day(r.get("at")))
            if cls == plat["verified"]:
                a["platform_verified"].add(eid)
            if mode == "guided":
                a["guided"].add(eid)
            elif mode == "troubleshoot":
                a["troubleshooting"].add(eid)
            elif mode == "challenge" and int(detail.get("hints_used") or 0) <= INDEPENDENT_MAX_HINTS \
                    and not detail.get("explanation_seen"):
                a["independent"].add(eid)
    out = []
    for o in objs:
        a = acc[o["id"]]
        for eid, days in a["passes"].items():
            known = sorted(d for d in days if d)
            if known and (known[-1] - known[0]).days >= RETENTION_DAYS:
                a["retention"].add(eid)
        complete = any(exercises.completes(o, c, cert["platform"]) for c in a["classes"])
        needs = registry.required(o, plat)
        state = "shown" if complete else "practised" if a["classes"] or a["knowledge"]["answered"] else "not_started"
        out.append({"id": o["id"], "text": o["text"], "domain": o["domain"], "skill": o["skill"],
                    "platform_required": needs, "state": state,
                    "best_class": max(a["classes"], key=lambda c: exercises.STRENGTH.get(c, -1)) if a["classes"] else None,
                    "measures": {"knowledge": dict(a["knowledge"]), **{m: sorted(a[m]) for m in MEASURES[1:]}},
                    "needs_platform": needs and plat["verified"] not in a["classes"],
                    "gaps": list(o["gaps"])})
    domains = []
    for d in cert["domains"]:
        mine = [x for x in out if x["domain"] == d["id"]]
        domains.append({"id": d["id"], "title": d["title"], "weight": dict(d["weight"]), "objectives": len(mine),
                        "shown": sum(x["state"] == "shown" for x in mine),
                        "practised": sum(x["state"] == "practised" for x in mine),
                        "need_platform": sum(x["needs_platform"] for x in mine)})
    return {"certification": cert["id"], "version": cert["version"], "platform": cert["platform"],
            "platform_label": plat["label"], "label": ESTIMATE_LABEL, "note": note(cert["platform"]),
            "objectives": out, "domains": domains, "earlier_version_rows": earlier,
            "next": next_exercises(out, pack_id)}


def next_exercises(objective_states: list[dict[str, Any]], pack_id: str = "dp-700", limit: int = 3) -> list[dict[str, Any]]:
    """Published exercises for objectives not yet shown: the domain with the fewest shown first, then in
    published order, skipping exercises already passed."""
    live = {e["id"]: e for e in exercises.exercises(pack_id, include_drafts=False)}
    shown_by_domain: dict[str, int] = {}
    for x in objective_states:
        shown_by_domain[x["domain"]] = shown_by_domain.get(x["domain"], 0) + (x["state"] == "shown")
    done = {eid.split("/", 1)[-1] for x in objective_states for m in MEASURES[1:] for eid in x["measures"][m]}
    picks: list[dict[str, Any]] = []
    for x in sorted(objective_states, key=lambda x: (shown_by_domain[x["domain"]], x["domain"])):
        if x["state"] == "shown":
            continue
        for eid, ex in live.items():
            if x["id"] in ex["objectives"] and eid not in done and eid not in {p["exercise"] for p in picks}:
                picks.append({"exercise": eid, "title": ex["title"], "objective": x["id"], "mode": ex["mode"],
                              "class": ex["class"], "minutes": ex["minutes"]})
        if len(picks) >= limit:
            break
    return picks[:limit]
