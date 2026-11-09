"""The class catalog (CM-02): classes, their specializations and talent trees, and the skills registry.

Two kinds of file under ``catalog/``, both plain JSON that ships with the engine:

``skills.json`` (``kratosforge/mastery-skills@1``)
    Every skill once, with the competency it belongs to and its evidence hooks: the Learn lessons and
    labs, Academy exercises, exam objectives, best-practice rules, app events (``validate.pass``,
    ``fix.checked``, ``prove.match``) and Learn check kinds that can verify it. ``cloud`` marks a skill
    that can only be fully proven in a cloud service. A skill shared by two classes is one entry, so
    progress in one class counts in the other.

``classes/<id>.json`` (``kratosforge/mastery-class@1``)
    One class: its fantasy and professional names and descriptions, platforms, an emblem brief, five or
    more achievements, and three or more specializations. Each specialization is a directed acyclic
    graph of talent nodes; a node names a skill, a tier and the nodes it requires (same class only).

``load()`` reads and checks both and refuses the whole catalog with every problem listed, so a bad edit
never reaches a profile. ``problems()`` is the same check on data in memory, for tests and tools.
Nothing here reads a profile; ``levels.py`` does that.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent / "catalog"
SKILLS_SCHEMA = "kratosforge/mastery-skills@1"
CLASS_SCHEMA = "kratosforge/mastery-class@1"

MIN_SPECIALIZATIONS = 3
MIN_NODES = 10
MIN_ACHIEVEMENTS = 5

HOOKS = ("learn", "academy", "objectives", "rules", "events", "checks")
EVENTS = ("validate.pass", "fix.checked", "prove.match")
CHECKS = ("faster_than", "table_shape", "integrity", "defects_found")
# The names a class, specialization, talent or achievement may not carry or resemble (as tests/test_ranks.py
# holds for ranks): those titles are not ours to give.
BANNED_NAMES = re.compile(r"god|deity|divine|olympian|immortal", re.I)

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_NODE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}\.\d{2}$")
_LEARN = re.compile(r"^(LM|LAB)-\d{2}$")
_ACADEMY = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}/E\d{2}$")
_OBJECTIVE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}:\d{1,2}\.\d{1,2}\.\d{1,2}$")
_RULE = re.compile(r"^[A-Z]{2}\d{3}$")


class CatalogError(ValueError):
    """The catalog can't be used; ``problems`` lists everything wrong with it."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems[:5]) + (f" (and {len(problems) - 5} more)" if len(problems) > 5 else ""))
        self.problems = problems


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read(root: Path = ROOT) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The raw skills document and class documents, unchecked. A file that isn't JSON raises CatalogError."""
    try:
        skills = _read(root / "skills.json")
        classes = [_read(p) for p in sorted((root / "classes").glob("*.json"))]
    except (OSError, ValueError) as e:
        raise CatalogError([f"catalog file can't be read: {e}"]) from e
    return skills, classes


def known_ids() -> dict[str, set[str]]:
    """What evidence hooks may name today: published and draft Learn modules, Academy exercises (by pack),
    exam objectives (by pack) and best-practice rules. Drafts count because a hook may point at content
    before it ships; levels only ever count verified rows of published content."""
    from .. import notebook_practices
    from ..academy import exercises, registry
    from ..learn import content

    out: dict[str, set[str]] = {"learn": {m["id"] for m in content.modules(include_drafts=True)},
                                "academy": set(), "objectives": set(),
                                "rules": {r["id"] for r in notebook_practices.rules()}}
    for pack in registry.pack_ids():
        out["academy"] |= {f"{pack}/{e}" for e in exercises.ids(pack)}
        out["objectives"] |= {f"{pack}:{o['id']}" for o in registry.objectives(registry.certification(pack))}
    return out


def problems(skills_doc: Any, classes: Iterable[Any], known: dict[str, set[str]] | None = None) -> list[str]:
    """Everything wrong with a catalog. ``known`` (from known_ids) also checks that hooks name real content."""
    out: list[str] = []
    skills = _skill_problems(skills_doc, out, known)
    seen: set[str] = set()
    for c in classes:
        if not isinstance(c, dict):
            out.append("a class file isn't an object")
            continue
        cid = str(c.get("id"))
        if cid in seen:
            out.append(f"{cid}: class id used twice")
        seen.add(cid)
        _class_problems(c, skills, out)
    return out


def _skill_problems(doc: Any, out: list[str], known: dict[str, set[str]] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(doc, dict) or doc.get("schema") != SKILLS_SCHEMA:
        out.append(f"skills.json: schema isn't {SKILLS_SCHEMA}")
        return {}
    comps = {c.get("id") for c in doc.get("competencies") or [] if isinstance(c, dict)}
    skills: dict[str, dict[str, Any]] = {}
    for s in doc.get("skills") or []:
        sid = str(s.get("id")) if isinstance(s, dict) else "?"
        if not isinstance(s, dict) or not _ID.match(sid):
            out.append(f"skills.json: bad skill id {sid!r}")
            continue
        if sid in skills:
            out.append(f"skills.json: skill {sid} listed twice")
        skills[sid] = s
        if not str(s.get("title") or "").strip():
            out.append(f"skills.json: {sid} has no title")
        if BANNED_NAMES.search(str(s.get("title") or "")):
            out.append(f"skills.json: {sid} title names a god")
        if s.get("competency") not in comps:
            out.append(f"skills.json: {sid} names unknown competency {s.get('competency')!r}")
        if not isinstance(s.get("cloud"), bool):
            out.append(f"skills.json: {sid} cloud must be true or false")
        ev = s.get("evidence")
        if not isinstance(ev, dict):
            out.append(f"skills.json: {sid} evidence must be an object")
            continue
        for kind, ids in ev.items():
            if kind not in HOOKS or not isinstance(ids, list):
                out.append(f"skills.json: {sid} has unknown evidence kind {kind!r}")
                continue
            for i in ids:
                bad = {"learn": not _LEARN.match(str(i)), "academy": not _ACADEMY.match(str(i)),
                       "objectives": not _OBJECTIVE.match(str(i)), "rules": not _RULE.match(str(i)),
                       "events": i not in EVENTS, "checks": i not in CHECKS}[kind]
                if bad:
                    out.append(f"skills.json: {sid} {kind} hook {i!r} isn't valid")
                elif known is not None and kind in known and i not in known[kind]:
                    out.append(f"skills.json: {sid} {kind} hook {i} names nothing that exists")
    return skills


def _class_problems(c: dict[str, Any], skills: dict[str, dict[str, Any]], out: list[str]) -> None:
    cid = str(c.get("id"))
    where = f"class {cid}"
    if c.get("schema") != CLASS_SCHEMA:
        out.append(f"{where}: schema isn't {CLASS_SCHEMA}")
    if not _ID.match(cid):
        out.append(f"{where}: bad id")
    if not isinstance(c.get("version"), int) or c["version"] < 1:
        out.append(f"{where}: version must be a whole number from 1")
    for key in ("title", "professional", "fantasy", "competency", "emblem"):
        if not str(c.get(key) or "").strip():
            out.append(f"{where}: no {key}")
    names = [str(c.get("title") or "")]
    specs = [s for s in c.get("specializations") or [] if isinstance(s, dict)]
    if len(specs) < MIN_SPECIALIZATIONS:
        out.append(f"{where}: {len(specs)} specializations, needs at least {MIN_SPECIALIZATIONS}")
    achievements = [a for a in c.get("achievements") or [] if isinstance(a, dict)]
    if len(achievements) < MIN_ACHIEVEMENTS:
        out.append(f"{where}: {len(achievements)} achievements, needs at least {MIN_ACHIEVEMENTS}")
    if len({a.get("id") for a in achievements}) != len(achievements):
        out.append(f"{where}: achievement ids repeat")
    names += [str(a.get("name") or "") for a in achievements]
    nodes: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    spec_ids: set[str] = set()
    for sp in specs:
        sid = str(sp.get("id"))
        if not _ID.match(sid) or sid in spec_ids:
            out.append(f"{where}: bad or repeated specialization id {sid!r}")
        spec_ids.add(sid)
        names.append(str(sp.get("title") or ""))
        mine = [n for n in sp.get("nodes") or [] if isinstance(n, dict)]
        if len(mine) < MIN_NODES:
            out.append(f"{where}/{sid}: {len(mine)} talents, needs at least {MIN_NODES}")
        for n in mine:
            nid = str(n.get("id"))
            if not _NODE.match(nid) or not nid.startswith(f"{sid}."):
                out.append(f"{where}/{sid}: node id {nid!r} must be '{sid}.NN'")
            if nid in nodes:
                out.append(f"{where}: node {nid} used twice")
            nodes[nid] = n
            skill = n.get("skill")
            if skill not in skills:
                out.append(f"{where}: node {nid} names unknown skill {skill!r}")
            elif skill in used:
                out.append(f"{where}: skill {skill} appears twice in this class")
            used.add(str(skill))
            if not isinstance(n.get("tier"), int) or n["tier"] < 1:
                out.append(f"{where}: node {nid} tier must be a whole number from 1")
            if not isinstance(n.get("requires"), list):
                out.append(f"{where}: node {nid} requires must be a list")
    for name in names:
        if BANNED_NAMES.search(name):
            out.append(f"{where}: the name {name!r} is or resembles a god")
    _graph_problems(where, nodes, out)


def _graph_problems(where: str, nodes: dict[str, dict[str, Any]], out: list[str]) -> None:
    """Unknown prerequisites, tiers that don't rise, cycles and nodes no root leads to."""
    req: dict[str, list[str]] = {}
    broken: set[str] = set()   # a node whose prerequisite is missing can't be reached however the rest is
    for nid, n in nodes.items():
        req[nid] = []
        declared = n.get("requires")
        for r in declared if isinstance(declared, list) else []:
            if r not in nodes:
                out.append(f"{where}: node {nid} requires unknown node {r!r}")
                broken.add(nid)
            elif r == nid:
                out.append(f"{where}: node {nid} requires itself")
            else:
                req[nid].append(r)
                if isinstance(n.get("tier"), int) and isinstance(nodes[r].get("tier"), int) and nodes[r]["tier"] >= n["tier"]:
                    out.append(f"{where}: node {nid} (tier {n['tier']}) requires {r} at tier {nodes[r]['tier']}; "
                               "a prerequisite must sit on a lower tier")
    order = topological(req)
    if order is None:
        out.append(f"{where}: the talent graph has a cycle")
        return
    reach = {nid for nid, rs in req.items() if not rs and nid not in broken}
    for nid in order:
        if req[nid] and nid not in broken and all(r in reach for r in req[nid]):
            reach.add(nid)
    for nid in sorted(set(nodes) - reach):
        out.append(f"{where}: node {nid} can't be reached from a starting talent")


def topological(req: dict[str, list[str]]) -> list[str] | None:
    """The nodes with every prerequisite before the node (ties by id), or None when there is a cycle."""
    remaining = {n: set(rs) for n, rs in req.items()}
    order: list[str] = []
    while remaining:
        ready = sorted(n for n, rs in remaining.items() if not rs)
        if not ready:
            return None
        for n in ready:
            order.append(n)
            del remaining[n]
        for rs in remaining.values():
            rs.difference_update(ready)
    return order


@lru_cache(maxsize=4)
def load(root: Path = ROOT, check_hooks: bool = True) -> dict[str, Any]:
    """The checked catalog: ``skills`` and ``competencies`` by id, ``classes`` by id (in title order), and
    ``version``. Raises CatalogError listing every problem. Cached: the files ship with the engine."""
    skills_doc, classes = read(root)
    found = problems(skills_doc, classes, known_ids() if check_hooks else None)
    if found:
        raise CatalogError(found)
    return {"version": skills_doc["version"],
            "competencies": {c["id"]: c for c in skills_doc["competencies"]},
            "skills": {s["id"]: s for s in skills_doc["skills"]},
            "classes": {c["id"]: c for c in sorted(classes, key=lambda c: c["title"])}}


def nodes(cls: dict[str, Any]) -> list[dict[str, Any]]:
    """A class's nodes in prerequisite order, each with its specialization id."""
    flat = {n["id"]: {**n, "specialization": sp["id"]} for sp in cls["specializations"] for n in sp["nodes"]}
    order = topological({nid: list(n["requires"]) for nid, n in flat.items()}) or []
    return [flat[nid] for nid in order]
