"""Academy readiness (ACD-006): where a learner stands on each exam objective, from their evidence.

Worked out every time it is shown from the profile's included evidence rows (progress.py), never stored,
so excluding a row takes it out here too. Six separate measures per objective, never blended into one
number:

    knowledge         assessment items answered correctly, of those answered (academy.assessment rows); in
                      points, so a multi-part question's partial credit counts its share (2 places)
    guided            guided exercises passed
    independent       challenge exercises passed with at most one hint, the explanation closed and (a quiz)
                      no checked answer changed before the pass
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
KNOWLEDGE_MIN_ANSWERED = 2   # the dashboard's Knowledge: at least this many answered...
KNOWLEDGE_MET = 0.75         # ...and at least this share of the points right
LIST_MAX = 5                 # strengths and weaknesses shown
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
    return {"knowledge": {"correct": 0.0, "answered": 0}, "guided": set(), "independent": set(), "troubleshooting": set(),
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
                a["knowledge"]["correct"] = round(a["knowledge"]["correct"] + float(per.get("correct") or 0), 2)
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
                    and not detail.get("explanation_seen") and not detail.get("answers_changed"):
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
    result = {"certification": cert["id"], "version": cert["version"], "platform": cert["platform"],
              "platform_label": plat["label"], "label": ESTIMATE_LABEL, "note": note(cert["platform"]),
              "objectives": out, "domains": domains, "earlier_version_rows": earlier,
              "next": next_exercises(out, pack_id)}
    result["dashboard"] = dashboard(result)
    return result


def knowledge_met(k: dict[str, Any]) -> bool:
    """Knowledge counts as met for an objective with at least KNOWLEDGE_MIN_ANSWERED answered and at least
    KNOWLEDGE_MET of the points right."""
    return int(k.get("answered") or 0) >= KNOWLEDGE_MIN_ANSWERED and float(k["correct"]) >= KNOWLEDGE_MET * k["answered"]


def _met(o: dict[str, Any], m: str) -> bool:
    return knowledge_met(o["measures"]["knowledge"]) if m == "knowledge" else bool(o["measures"][m])


def dashboard(r: dict[str, Any]) -> dict[str, Any]:
    """The Readiness dashboard's summary (ACD-015), worked out from ``readiness()`` with fixed rules, nothing
    guessed: by domain, how many objectives meet each measure (Platform verified counts only objectives that
    need the platform); strengths (proved objectives, then those meeting the most measures); weaknesses
    (objectives with questions answered and under half of the points right, then started objectives with no
    exercise passed); and what's still to do."""
    objs = r["objectives"]
    domains = []
    for d in r["domains"]:
        mine = [o for o in objs if o["domain"] == d["id"]]
        measures = {}
        for m in MEASURES:
            pool = [o for o in mine if o["platform_required"]] if m == "platform_verified" else mine
            measures[m] = {"met": sum(_met(o, m) for o in pool), "of": len(pool)}
        domains.append({"id": d["id"], "measures": measures})
    count = lambda o: sum(_met(o, m) for m in MEASURES)  # noqa: E731 - a sort key, used twice
    strong = sorted((o for o in objs if o["state"] == "shown" or count(o) >= 2), key=lambda o: (o["state"] != "shown", -count(o), o["id"]))
    weak_k = [o for o in objs if o["measures"]["knowledge"]["answered"] >= KNOWLEDGE_MIN_ANSWERED
              and o["measures"]["knowledge"]["correct"] < 0.5 * o["measures"]["knowledge"]["answered"]]
    weak_x = [o for o in objs if o["state"] == "practised" and o not in weak_k
              and not any(o["measures"][m] for m in MEASURES[1:])]
    item = lambda o, why: {"id": o["id"], "text": o["text"], "domain": o["domain"], "why": why}  # noqa: E731
    k = lambda o: o["measures"]["knowledge"]  # noqa: E731
    todo = [o for o in objs if o["state"] == "not_started"]
    return {"knowledge_rule": {"min_answered": KNOWLEDGE_MIN_ANSWERED, "share": KNOWLEDGE_MET},
            "domains": domains,
            "strengths": [item(o, "proved" if o["state"] == "shown" else f"{count(o)} measures met") for o in strong[:LIST_MAX]],
            "weaknesses": ([item(o, f"{_points(k(o)['correct'])} of {k(o)['answered']} question points right") for o in weak_k]
                           + [item(o, "started, no exercise passed yet") for o in weak_x])[:LIST_MAX],
            "todo": {"objectives": len(todo), "by_domain": {d["id"]: sum(o["domain"] == d["id"] for o in todo) for d in r["domains"]},
                     "need_platform": sum(o["platform_required"] for o in todo)}}


def _points(x: float) -> str:
    return f"{x:g}"


def report(r: dict[str, Any], title: str, today: str) -> str:
    """The exported readiness report as Markdown: the estimate's label and note first, then the totals, each
    domain's measures, every objective, strengths, weaknesses and what's left. Only what this screen shows."""
    names = {"knowledge": "Knowledge", "guided": "Guided", "independent": "Independent", "troubleshooting": "Troubleshooting",
             "platform_verified": f"{r['platform_label']} verified", "retention": "Retention"}
    dash = r.get("dashboard") or dashboard(r)
    objs = r["objectives"]
    cell = lambda s: " ".join(str(s).replace("|", "/").split())  # noqa: E731 - one table cell, on one line
    lines = [f"# {cell(title)}: {r['label']}", "", f"Exported {today}. Study guide of {r['version']}.", "", r["note"], "",
             f"- Objectives proved: {sum(o['state'] == 'shown' for o in objs)} of {len(objs)}",
             f"- Started, not proved yet: {sum(o['state'] == 'practised' for o in objs)}",
             f"- Still to do: {dash['todo']['objectives']}", "", "## By domain", "",
             "| Domain | " + " | ".join(names[m] for m in MEASURES) + " |", "|" + " --- |" * (len(MEASURES) + 1)]
    titles = {d["id"]: d["title"] for d in r["domains"]}
    for d in dash["domains"]:
        lines.append(f"| {d['id']} {cell(titles[d['id']])} | " + " | ".join(
            f"{x['met']} of {x['of']}" if x["of"] else "not needed" for x in (d["measures"][m] for m in MEASURES)) + " |")
    lines += ["", "## By objective", "", "| Objective | State | " + " | ".join(names[m] for m in MEASURES) + " |",
              "|" + " --- |" * (len(MEASURES) + 2)]
    state = {"shown": "proved", "practised": "started", "not_started": "not started"}
    for o in objs:
        kn = o["measures"]["knowledge"]
        vals = [f"{_points(kn['correct'])} of {kn['answered']}" if kn["answered"] else "not yet"]
        for m in MEASURES[1:]:
            vals.append("not needed" if m == "platform_verified" and not o["platform_required"]
                        else ", ".join(e.split("/")[-1] for e in o["measures"][m]) or "not yet")
        lines.append(f"| {o['id']} {cell(o['text'])} | {state[o['state']]} | " + " | ".join(cell(v) for v in vals) + " |")
    for head, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses")):
        lines += ["", f"## {head}", ""] + ([f"- {x['id']} {cell(x['text'])}: {x['why']}" for x in dash[key]] or ["- None yet"])
    lines += ["", "## How it's worked out", "",
              (f"Knowledge counts as met with at least {KNOWLEDGE_MIN_ANSWERED} questions answered and {round(KNOWLEDGE_MET * 100)}% "
               "of the points right. Guided, Independent, Troubleshooting and Retention each need an exercise passed that way. "
               f"{names['platform_verified']} counts only objectives that need it. Worked out on this PC from Academy records only."), ""]
    return "\n".join(lines)


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
