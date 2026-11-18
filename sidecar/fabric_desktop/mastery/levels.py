"""Mastery levels (CM-03): what the profile's verified evidence shows for each skill, talent and class.

Everything is worked out from ``progress.included(profile)`` every time, with the rules below
(``RULES_VERSION``), so it is deterministic and idempotent: the same rows in any order give the same
answer, a row recorded twice is one row (progress.add refuses a repeated id), and excluding a row takes
it out of every level. Nothing is stored here.

A row counts for a skill when one of the skill's evidence hooks names it (catalog.py). Each counted row
falls in one category: knowledge (a lesson finished, a knowledge-only exercise, a practice exam's
objective answered well), practice (a Learn or Academy exercise or lab passed; only the skill's own
lessons and labs count, and finishing a lab and passing its exercise are one source), engineering (your own
notebook passing Validate, a fix checked with Compare) or mastery (a Prove match). Simulated Academy
exercises check a configuration, not a service, so they are practice marked ``simulated``.

Levels (each needs everything the level below needs):

1 Initiate      any counted row
2 Apprentice    a knowledge row, or any practice row
3 Journeyman    a practice row verified on this PC (a cloud-only skill accepts a simulated one)
4 Adept         practice from 2 different sources whose first passes are at least 7 days apart, one of
                them independent
                (a Learn exercise, or an Academy one with at most 1 hint, the explanation unopened and
                no checked answers changed)
5 Expert        engineering evidence on 2 different artifacts, and one measured result
6 Master        an advanced row (a capstone lab, a Prove match, or an independent Academy challenge
                verified on this PC) and practice again at least 30 days after the first
7 Grandmaster   reviewer-verified evidence, a community contribution and 180 days of evidence. No row
                type carries a reviewer yet, so nobody reaches it, and ``ceiling`` says why

A cloud-only skill stops at Journeyman unless a row was verified on the platform itself. A skill with no
hooks is planned content: ceiling 0. Character XP and level stay progress.progression()'s; ``xp_breakdown``
only sorts that same XP into the five categories.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .. import progress
from ..academy import registry as academy_registry
from . import catalog

RULES_VERSION = 1
LEVELS = ("Not started", "Initiate", "Apprentice", "Journeyman", "Adept", "Expert", "Master", "Grandmaster")
CATEGORIES = ("knowledge", "practice", "engineering", "mastery", "community")

UNLOCK_LEVEL = 2          # every prerequisite at Apprentice or higher opens a talent
MASTERED_LEVEL = 4        # a talent shows as mastered from Adept
ADEPT_SOURCES = 2
ADEPT_DAYS = 7
EXPERT_ARTIFACTS = 2
MASTER_DAYS = 30
GRANDMASTER_DAYS = 180
INDEPENDENT_MAX_HINTS = 1
ASSESSMENT_PASS = 0.7     # a practice exam objective counts as knowledge at 70% correct, 2 or more answered
ASSESSMENT_MIN = 2
CLASS_MIN_TALENTS = 10    # class mastery: this many talents at the level ...
CLASS_MIN_PER_SPEC = 2    # ... with at least this many in every specialization
ADVANCED_LABS = ("LAB-10", "LAB-11", "LAB-12")
PLATFORM_CLASSES = frozenset(p["verified"] for p in academy_registry.PLATFORMS.values())


def _day(stamp: Any) -> dt.date | None:
    try:
        return dt.datetime.fromisoformat(str(stamp)).date() if stamp else None
    except ValueError:
        return None


def _module(ref_id: str) -> str:
    return ref_id.split("/", 1)[0]


def counts(row: dict[str, Any], skill: dict[str, Any]) -> dict[str, Any] | None:
    """How a row counts for a skill, or None: its category, the source it came from, and flags."""
    ev = skill.get("evidence") or {}
    t = row.get("type")
    ref = str((row.get("ref") or {}).get("id") or "")
    detail = row.get("detail") or {}
    measured = bool(row.get("measurement"))
    if t in ("module.complete", "lab.pass"):
        # Only the skill's own lessons and labs count; a check kind never widens that (it only marks a
        # measured result). Finishing a lab and passing its exercise are one source, the module.
        module = _module(ref)
        if module not in (ev.get("learn") or []):
            return None
        lab = module.startswith("LAB-")
        if t == "module.complete":
            return {"category": "practice" if lab else "knowledge", "source": module, "independent": lab,
                    "advanced": module in ADVANCED_LABS, "measured": False, "simulated": False, "platform": False}
        return {"category": "practice", "source": module, "independent": True, "advanced": module in ADVANCED_LABS,
                "measured": measured or "faster_than" in (detail.get("checks") or []), "simulated": False, "platform": False}
    if t == "academy.exercise":
        pack = str(detail.get("certification") or ref.split("/", 1)[0])
        objectives = {f"{pack}:{o}" for o in detail.get("objectives") or []}
        if ref not in (ev.get("academy") or []) and not objectives & set(ev.get("objectives") or []):
            return None
        cls = detail.get("class")
        independent = (int(detail.get("hints_used") or 0) <= INDEPENDENT_MAX_HINTS and not detail.get("explanation_seen")
                       and not detail.get("answers_changed"))
        if cls == "knowledge_only":
            return {"category": "knowledge", "source": ref, "independent": independent, "advanced": False,
                    "measured": False, "simulated": False, "platform": False}
        simulated = cls == "simulated"
        return {"category": "practice", "source": ref, "independent": independent, "simulated": simulated,
                "advanced": not simulated and detail.get("mode") == "challenge" and independent,
                "measured": measured, "platform": cls in PLATFORM_CLASSES}
    if t == "academy.assessment":
        pack = str(detail.get("certification") or "")
        for oid, per in sorted((detail.get("per_objective") or {}).items()):
            answered, correct = int((per or {}).get("answered") or 0), int((per or {}).get("correct") or 0)
            if f"{pack}:{oid}" in (ev.get("objectives") or []) and answered >= ASSESSMENT_MIN \
                    and correct >= ASSESSMENT_PASS * answered:
                return {"category": "knowledge", "source": f"{pack}:{oid}", "independent": True, "advanced": False,
                        "measured": False, "simulated": False, "platform": False}
        return None
    name = (row.get("artifact") or {}).get("name") or row["id"]   # an unnamed artifact is its own source
    if t == "validate.pass" and "validate.pass" in (ev.get("events") or []):
        return {"category": "engineering", "source": str(name), "independent": True,
                "advanced": False, "measured": measured, "simulated": False, "platform": False}
    if t == "fix.checked":
        rule = str(name)
        if rule in (ev.get("rules") or []) or "fix.checked" in (ev.get("events") or []):
            return {"category": "engineering", "source": f"{rule}@{ref}", "independent": True, "advanced": False,
                    "measured": measured, "simulated": False, "platform": False}
        return None
    if t == "prove.match" and "prove.match" in (ev.get("events") or []):
        return {"category": "mastery", "source": str(name), "independent": True,
                "advanced": True, "measured": measured, "simulated": False, "platform": True}
    return None


def ceiling(skill: dict[str, Any], rows: list[dict[str, Any]] | None = None) -> tuple[int, str]:
    """The highest level the skill can reach today, and why it stops there."""
    if not any(skill.get("evidence", {}).values()):
        return 0, "No lesson, exercise or check verifies this skill yet."
    if skill.get("cloud") and not any(r["platform"] for r in rows or []):
        return 3, "Fully proving this needs the cloud service itself; work on this PC reaches Journeyman."
    return 6, "Grandmaster needs reviewer verification, which KratosForge doesn't offer yet."


def level(skill: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    """The skill's mastery level from its counted rows (each: the row's ``at`` plus ``counts()``)."""
    if not rows:
        return 0
    top, _ = ceiling(skill, rows)
    practice = [r for r in rows if r["category"] == "practice"]
    real = [r for r in practice if not r["simulated"]] + ([r for r in practice if r["simulated"]] if skill.get("cloud") else [])
    lv = 1
    if any(r["category"] == "knowledge" for r in rows) or practice:
        lv = 2
    if lv == 2 and real:
        lv = 3
    days = sorted(d for d in (_day(r["at"]) for r in real) if d)
    first: dict[str, dt.date] = {}   # each source's first pass: Adept needs two sources a week apart
    for r in real:
        d = _day(r["at"])
        if d and (r["source"] not in first or d < first[r["source"]]):
            first[r["source"]] = d
    spread = (max(first.values()) - min(first.values())).days if len(first) >= ADEPT_SOURCES else -1
    if lv == 3 and spread >= ADEPT_DAYS and any(r["independent"] for r in real):
        lv = 4
    work = {r["source"] for r in rows if r["category"] == "engineering"}
    if lv == 4 and len(work) >= EXPERT_ARTIFACTS and any(r["measured"] for r in rows):
        lv = 5
    if lv == 5 and any(r["advanced"] for r in rows) and (days[-1] - days[0]).days >= MASTER_DAYS:
        lv = 6
    # 7 needs reviewer-verified and community rows, which no evidence type carries yet (see ceiling()).
    return min(lv, top)


def skill_evidence(profile: dict[str, Any], cat: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Every skill's counted rows, in date order: the row id, when, its type and how it counts."""
    out: dict[str, list[dict[str, Any]]] = {sid: [] for sid in cat["skills"]}
    for row in progress.included(profile):
        for sid, skill in cat["skills"].items():
            c = counts(row, skill)
            if c:
                out[sid].append({"evidence": row["id"], "at": row.get("at"), "type": row["type"], **c})
    return out


def evaluate(profile: dict[str, Any], cat: dict[str, Any] | None = None) -> dict[str, Any]:
    """Skills, and every class with its talents' levels and states and the class's mastery level."""
    cat = cat or catalog.load()
    ev = skill_evidence(profile, cat)
    skills = {}
    for sid, skill in cat["skills"].items():
        lv = level(skill, ev[sid])
        top, why = ceiling(skill, ev[sid])
        skills[sid] = {"id": sid, "title": skill["title"], "competency": skill["competency"], "level": lv,
                       "level_name": LEVELS[lv], "ceiling": top, "ceiling_reason": why, "evidence": ev[sid]}
    classes = {cid: class_view(c, skills) for cid, c in cat["classes"].items()}
    return {"rules_version": RULES_VERSION, "catalog_version": cat["version"], "skills": skills, "classes": classes}


def class_view(cls: dict[str, Any], skills: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One class: each talent's level and state, and the class's mastery level.

    A talent is locked until every prerequisite is at Apprentice or higher. A locked talent keeps its
    level (``banked``): evidence is never lost and counts as soon as the talent opens, but until then it
    adds nothing to the class."""
    state: dict[str, dict[str, Any]] = {}
    for n in catalog.nodes(cls):
        lv = skills[n["skill"]]["level"]
        open_ = all(state[r]["level"] >= UNLOCK_LEVEL and not state[r]["locked"] for r in n["requires"])
        shown = lv if open_ else 0
        state[n["id"]] = {"id": n["id"], "skill": n["skill"], "specialization": n["specialization"], "tier": n["tier"],
                          "requires": list(n["requires"]), "level": shown, "banked": 0 if open_ else lv, "locked": not open_,
                          "state": "locked" if not open_ else "mastered" if shown >= MASTERED_LEVEL
                          else "in_progress" if shown >= 1 else "available"}
    specs = [sp["id"] for sp in cls["specializations"]]
    mastery = 0
    for lv in range(1, len(LEVELS)):
        at = [s for s in state.values() if s["level"] >= lv]
        if len(at) >= CLASS_MIN_TALENTS and all(sum(s["specialization"] == sp for s in at) >= CLASS_MIN_PER_SPEC for sp in specs):
            mastery = lv
    return {"id": cls["id"], "version": cls["version"], "mastery": mastery, "mastery_name": LEVELS[mastery],
            "talents": [state[n["id"]] for sp in cls["specializations"] for n in sp["nodes"]],
            "mastered": sum(s["state"] == "mastered" for s in state.values()),
            "at_journeyman": sum(s["level"] >= 3 for s in state.values())}


def xp_breakdown(profile: dict[str, Any]) -> dict[str, int]:
    """The character's XP (progress.progression) sorted into the five categories. The total is unchanged."""
    pr = progress.progression(profile)
    rows = {r["id"]: r for r in progress.included(profile)}
    out = dict.fromkeys(CATEGORIES, 0)
    for rid, xp in pr["xp_by_row"].items():
        out[_category(rows.get(rid))] += xp
    for a in pr["achievements"]:
        out[_category(rows.get(a["evidence"]))] += a["xp"]
    return out


def _category(row: dict[str, Any] | None) -> str:
    t = (row or {}).get("type")
    if t == "academy.exercise" and ((row or {}).get("detail") or {}).get("class") == "knowledge_only":
        return "knowledge"
    if t == "module.complete":
        return "practice" if str((row or {}).get("ref", {}).get("id", "")).startswith("LAB-") else "knowledge"
    return {"lab.pass": "practice", "validate.pass": "engineering", "fix.checked": "engineering",
            "prove.match": "mastery", "academy.assessment": "knowledge"}.get(str(t), "practice")
