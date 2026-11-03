"""Development roadmap and personal goals (KF-DX-011): where a developer could grow next, checked
against the evidence the profile already keeps. progress.py keeps the evidence; ranks.py reads the
same facts for ranks. This module only reads them too.

Seven tracks, each a short list of milestones. Milestones are suggestions, not a prerequisite
chain: any can be met in any order, and people specialise. Each milestone names the skills it
shows, what to do, how it is verified (the same requirement kinds ranks use, so "met" always means
verified evidence on this PC), related achievements and Learn lessons to start from. Every
milestone can be met on this PC: none needs a Fabric run, so none spends capacity.

A goal is either a milestone the person chose (met when its checks are, never by ticking it) or
their own words (marked done by them, and labelled as entered by them wherever it appears).
Goals earn no XP: they are for the person's own planning and the report's development section.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable
from typing import Any

from . import progress, ranks

ROADMAP_VERSION = 1
MAX_GOALS = 12
MAX_GOAL_TEXT = 300

# requirement kinds: as ranks._check (modules, notebooks, fixes, exercises, labs, achievements)
TRACKS: tuple[dict[str, Any], ...] = (
    {"id": "data-engineering", "title": "Data Engineering", "milestones": (
        {"id": "de-notebook", "title": "Notebook development",
         "skills": ("Data Engineering", "Engineering Quality"),
         "activities": "Write a notebook that reads or writes a lakehouse table and run Validate on it until its clean run "
                       "finishes Ready.",
         "needs": ({"notebooks": 1},), "achievements": ("first-flame",), "learn": ("LM-08",)},
        {"id": "de-reliable-loads", "title": "Loads that are safe to run again",
         "skills": ("Data Engineering", "Reliability & Operations"),
         "activities": "Finish the incremental load and rerun-safe load lessons, then the Bronze to Silver lab.",
         "needs": ({"modules": ["LM-12", "LM-21", "LAB-06"]},), "achievements": (), "learn": ("LM-12", "LM-21", "LAB-06")},
        {"id": "de-many-notebooks", "title": "Notebooks across a project",
         "skills": ("Data Engineering",),
         "activities": "Validate five different notebooks of your own with clean runs.",
         "needs": ({"notebooks": 5},), "achievements": ("steady-hand",), "learn": ("LM-19", "LM-22")},
    )},
    {"id": "sql-warehouse", "title": "SQL & Warehouse", "milestones": (
        {"id": "sql-foundations", "title": "SQL foundations",
         "skills": ("Data Engineering",),
         "activities": "Finish the joins, grouping and window function lessons.",
         "needs": ({"modules": ["LM-03", "LM-04", "LM-15"]},), "achievements": (), "learn": ("LM-03", "LM-04", "LM-15")},
        {"id": "sql-warehouse", "title": "Warehouse engineering",
         "skills": ("Data Engineering", "Architecture & Reusability"),
         "activities": "Learn where T-SQL writes, then build a type 2 slowly changing dimension.",
         "needs": ({"modules": ["LM-23", "LAB-05"]},), "achievements": (), "learn": ("LM-23", "LAB-05")},
        {"id": "sql-tuning", "title": "Query tuning",
         "skills": ("Performance & Optimization",),
         "activities": "Read a query plan, then make a slow query measurably faster with the same answer.",
         "needs": ({"modules": ["LM-18", "LAB-02"]},), "achievements": ("query-forger",), "learn": ("LM-18", "LAB-02")},
    )},
    {"id": "semantic-modeling", "title": "Semantic Modeling", "milestones": (
        {"id": "sm-keys", "title": "Keys and relationships",
         "skills": ("Architecture & Reusability",),
         "activities": "Finish the keys lesson: primary, foreign, business and surrogate keys.",
         "needs": ({"modules": ["LM-05"]},), "achievements": ("architects-eye",), "learn": ("LM-05",)},
        {"id": "sm-star", "title": "Star schema",
         "skills": ("Architecture & Reusability",),
         "activities": "Build a star schema whose keys, types and relationships check out.",
         "needs": ({"modules": ["LAB-03"]},), "achievements": ("architects-eye",), "learn": ("LAB-03",)},
        {"id": "sm-direct-lake", "title": "Direct Lake",
         "skills": ("Architecture & Reusability", "Performance & Optimization"),
         "activities": "Learn what Direct Lake reads, then build a Gold layer that stays in Direct Lake.",
         "needs": ({"modules": ["LM-09", "LAB-07"]},), "achievements": (), "learn": ("LM-09", "LAB-07")},
    )},
    {"id": "data-integration", "title": "Data Integration", "milestones": (
        {"id": "di-incremental", "title": "Incremental loads and MERGE",
         "skills": ("Data Engineering",),
         "activities": "Finish the watermark and MERGE lessons.",
         "needs": ({"modules": ["LM-12", "LM-11"]},), "achievements": (), "learn": ("LM-12", "LM-11")},
        {"id": "di-drift", "title": "Schema drift and broken loads",
         "skills": ("Data Engineering", "Reliability & Operations"),
         "activities": "Handle a source that adds a column, then fix a broken load.",
         "needs": ({"modules": ["LM-22", "LAB-04"]},), "achievements": (), "learn": ("LM-22", "LAB-04")},
        {"id": "di-migration", "title": "Bring work over from another platform",
         "skills": ("Data Engineering", "Learning & Growth"),
         "activities": "Learn three-part names, then port a Databricks notebook to Fabric.",
         "needs": ({"modules": ["LM-26", "LAB-08"]},), "achievements": (), "learn": ("LM-26", "LAB-08")},
    )},
    {"id": "data-governance", "title": "Data Governance", "milestones": (
        {"id": "gov-quality", "title": "Data quality",
         "skills": ("Engineering Quality", "Security & Governance"),
         "activities": "Write data quality checks in code, then find every planted problem in a lab.",
         "needs": ({"modules": ["LM-20", "LAB-01"]},), "achievements": ("guardian",), "learn": ("LM-20", "LAB-01")},
        {"id": "gov-access", "title": "Access and security",
         "skills": ("Security & Governance",),
         "activities": "Finish the shortcuts and OneLake security lesson and the least privilege lesson.",
         "needs": ({"modules": ["LM-24", "LM-32"]},), "achievements": (), "learn": ("LM-24", "LM-32")},
        {"id": "gov-products", "title": "Data contracts and data products",
         "skills": ("Security & Governance", "Architecture & Reusability"),
         "activities": "Learn data contracts and domains, then ship a domain data product.",
         "needs": ({"modules": ["LM-30", "LM-31", "LAB-10"]},), "achievements": (), "learn": ("LM-30", "LM-31", "LAB-10")},
    )},
    {"id": "performance", "title": "Performance Optimization", "milestones": (
        {"id": "perf-measured", "title": "A measured speed-up",
         "skills": ("Performance & Optimization",),
         "activities": "Make a query faster with the same answer, measured on this PC.",
         "needs": ({"achievements": ["query-forger"]},), "achievements": ("query-forger",), "learn": ("LAB-02",)},
        {"id": "perf-fixes", "title": "Fixes checked before they ship",
         "skills": ("Engineering Quality", "Performance & Optimization"),
         "activities": "Apply three best-practice fixes after Compare finds the same rows as written and as fixed.",
         "needs": ({"fixes": 3},), "achievements": (), "learn": ()},
        {"id": "perf-tables", "title": "Table layout and maintenance",
         "skills": ("Performance & Optimization",),
         "activities": "Finish the V-Order, OPTIMIZE and VACUUM lesson and the clustering lesson.",
         "needs": ({"modules": ["LM-25", "LM-27"]},), "achievements": (), "learn": ("LM-25", "LM-27")},
    )},
    {"id": "platform", "title": "Platform Engineering", "milestones": (
        {"id": "plat-deploy", "title": "Environments, Git and deployments",
         "skills": ("Reliability & Operations",),
         "activities": "Finish the environments, Git and deployments lesson.",
         "needs": ({"modules": ["LM-34"]},), "achievements": (), "learn": ("LM-34",)},
        {"id": "plat-operate", "title": "Monitoring and incidents",
         "skills": ("Reliability & Operations",),
         "activities": "Learn monitoring, then work a late and broken data incident.",
         "needs": ({"modules": ["LM-35", "LAB-11"]},), "achievements": (), "learn": ("LM-35", "LAB-11")},
        {"id": "plat-capacity", "title": "Capacity and cost",
         "skills": ("Performance & Optimization", "Security & Governance"),
         "activities": "Finish the capacity and cost lesson.",
         "needs": ({"modules": ["LM-33"]},), "achievements": (), "learn": ("LM-33",)},
    )},
)

MILESTONES: dict[str, dict[str, Any]] = {m["id"]: {**m, "track": t["id"], "track_title": t["title"]}
                                         for t in TRACKS for m in t["milestones"]}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def clean_goals(raw: Any) -> list[dict[str, Any]]:
    """The goals as stored, keeping only well-formed ones (a hand-edited file can't add fields)."""
    out: list[dict[str, Any]] = []
    for g in raw if isinstance(raw, list) else []:
        if not isinstance(g, dict) or not isinstance(g.get("id"), str):
            continue
        milestone = g.get("milestone")
        if milestone is not None and milestone not in MILESTONES:
            continue  # a milestone a later roadmap removed: dropped rather than shown as unmeetable
        text = str(g.get("text") or "")[:MAX_GOAL_TEXT]
        if milestone is None and not text.strip():
            continue
        out.append({"id": g["id"][:80], "milestone": milestone, "text": "" if milestone else text,
                    "set_at": str(g.get("set_at") or "")[:40], "done_at": None if milestone else (str(g["done_at"])[:40] if g.get("done_at") else None)})
    return out[:MAX_GOALS]


def add_goal(profile: dict[str, Any], milestone: str | None = None, text: str | None = None) -> dict[str, Any]:
    """Add a milestone goal or a goal in the person's words. Returns the goal."""
    goals = profile["goals"]
    if len(goals) >= MAX_GOALS:
        raise ValueError(f"You can keep up to {MAX_GOALS} goals. Remove one first.")
    if milestone:
        if milestone not in MILESTONES:
            raise ValueError("That milestone isn't on the roadmap.")
        if any(g["milestone"] == milestone for g in goals):
            raise ValueError("That milestone is already one of your goals.")
        goal = {"id": f"m-{milestone}", "milestone": milestone, "text": "", "set_at": _now(), "done_at": None}
    else:
        words = " ".join(str(text or "").split())[:MAX_GOAL_TEXT]
        if not words:
            raise ValueError("Write the goal first.")
        stamp = _now()
        goal = {"id": "g-" + hashlib.sha256(f"{words}|{stamp}|{len(goals)}".encode("utf-8")).hexdigest()[:12],
                "milestone": None, "text": words, "set_at": stamp, "done_at": None}
    goals.append(goal)
    return goal


def remove_goal(profile: dict[str, Any], goal_id: str) -> None:
    rest = [g for g in profile["goals"] if g["id"] != goal_id]
    if len(rest) == len(profile["goals"]):
        raise LookupError("That goal isn't in your profile.")
    profile["goals"] = rest


def mark_goal(profile: dict[str, Any], goal_id: str, done: bool) -> None:
    """Mark a goal in the person's own words done or not. A milestone goal is met by its evidence only."""
    goal = next((g for g in profile["goals"] if g["id"] == goal_id), None)
    if goal is None:
        raise LookupError("That goal isn't in your profile.")
    if goal["milestone"]:
        raise ValueError("A roadmap goal is met when its checks are, from your verified work. It can't be ticked.")
    goal["done_at"] = _now() if done else None


def roadmap(profile: dict[str, Any], live: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Every track and milestone with each check's progress, the person's goals, and up to three
    suggestions: in a track where something is met, the first milestone that isn't; otherwise none.
    ``live`` is the published Learn modules (content.modules(include_drafts=False))."""
    live = list(live)
    shipped = {m["id"] for m in live}
    titles = {m["id"]: str(m.get("title") or "") for m in live}
    available = {"exercises": sum(len(m.get("exercises") or []) for m in live),
                 "labs": sum(1 for m in live if m.get("kind") == "lab")}
    facts = ranks.facts(profile)
    chosen = {g["milestone"] for g in profile["goals"] if g["milestone"]}
    tracks, state = [], {}
    for t in TRACKS:
        rows = []
        for m in t["milestones"]:
            checks = [ranks._check(need, facts, shipped, available) for need in m["needs"]]
            met = all(c["met"] for c in checks)
            state[m["id"]] = {"met": met, "checks": checks}
            rows.append({"id": m["id"], "title": m["title"], "skills": list(m["skills"]), "activities": m["activities"],
                         "checks": checks, "met": met, "goal": m["id"] in chosen,
                         "coming_soon": not met and any(c["coming_soon"] for c in checks),
                         "achievements": [{"id": a, "name": progress.ACHIEVEMENTS[a]["name"]} for a in m["achievements"]],
                         "learn": [{"id": i, "title": titles[i]} for i in m["learn"] if i in shipped]})
        tracks.append({"id": t["id"], "title": t["title"], "met": sum(r["met"] for r in rows), "of": len(rows),
                       "milestones": rows})
    suggestions = []
    for t in tracks:
        if not t["met"]:
            continue  # suggestions stay next to what the person has shown, never a whole new area
        nxt = next((r for r in t["milestones"] if not r["met"] and not r["coming_soon"]), None)
        if nxt and not nxt["goal"]:
            suggestions.append({"id": nxt["id"], "title": nxt["title"], "track": t["title"]})
    goals = []
    for g in profile["goals"]:
        if g["milestone"]:
            m = MILESTONES[g["milestone"]]
            s = state[g["milestone"]]
            goals.append({**g, "title": m["title"], "track": m["track_title"], "met": s["met"], "checks": s["checks"],
                          "source": "milestone"})
        else:
            goals.append({**g, "title": g["text"], "track": None, "met": bool(g["done_at"]), "checks": [],
                          "source": "entered"})
    return {"version": ROADMAP_VERSION, "tracks": tracks, "goals": goals, "suggestions": suggestions[:3],
            "max_goals": MAX_GOALS}
