"""Ranks (PB-125): thirteen titles people earn with verified evidence, from the forge floor through the
wolf pack into Greek myth. progress.py keeps the evidence and the XP; this module only reads them.

A rank is held when its criteria are met and every rank below it is held. Criteria count only what
the profile already records on this PC: Learn exercises passed and lessons finished (published
lessons only), notebooks that passed Validate cleanly, fixes checked with Compare, and achievements.
Proof Forged runs in Fabric, so no rank needs it. Nothing counts time spent, runs or lines of code.

Requirements name lesson ids and the planned lessons of each path (``PATHS``), fixed for this
``LADDER_VERSION``, so shipping a new lesson never takes a rank away. A rank whose requirements
name lessons that haven't shipped yet shows as coming soon instead of unreachable.

Every rank has an image prompt people can copy into their own image tool; the app generates no images.
No rank, title or prompt is or resembles a god (tests/test_ranks.py): those names are not ours to give.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import Any

from . import progress

LADDER_VERSION = 1

# Each path's lessons as planned for this ladder version (the design doc, 2026-11-02). content/paths.json
# lists only what has shipped; every id it lists must be here under the same path (tests/test_ranks.py).
PATHS: dict[str, dict[str, Any]] = {
    "data-fundamentals": {"title": "Data Fundamentals",
                          "modules": ("LM-01", "LM-02", "LM-03", "LM-04", "LM-05", "LM-13", "LM-14", "LAB-01")},
    "sql-development": {"title": "SQL Development",
                        "modules": ("LM-15", "LM-16", "LM-17", "LM-18", "LAB-02", "LAB-05")},
    "python-data-engineering": {"title": "Python & Data Engineering",
                                "modules": ("LM-12", "LM-19", "LM-20", "LM-21", "LM-22", "LAB-06")},
    "microsoft-fabric": {"title": "Microsoft Fabric",
                         "modules": ("LM-06", "LM-07", "LM-08", "LM-09", "LM-23", "LM-24", "LM-25", "LAB-03", "LAB-04",
                                     "LAB-07")},
    "databricks": {"title": "Databricks", "modules": ("LM-11", "LM-26", "LM-27", "LAB-08")},
    "snowflake": {"title": "Snowflake", "modules": ("LM-10", "LM-28", "LM-29", "LAB-09")},
    "enterprise-architecture": {"title": "Enterprise Architecture",
                                "modules": ("LM-30", "LM-31", "LM-32", "LM-33", "LM-34", "LM-35", "LAB-10", "LAB-11")},
}
# Lessons a Learn path gained after its ranks were set, by the path they're in. They aren't needed for a
# rank, so nobody who finished the path before loses a rank it gave them.
ADDED_AFTER = {"LM-36": "enterprise-architecture"}

TIERS = ("The Forge", "The Pack", "The Myth")

# Every prompt ends with this, so the set looks like one family.
PROMPT_STYLE = ("Square profile picture, centered emblem composition, painterly digital illustration, dark charcoal "
                "background, forge-ember orange and cool steel-blue light, crisp silhouette readable at 64 px, no text, "
                "no letters, no logos, original design not based on any existing video game, film or comic character.")

# Requirement kinds: modules (all of these lessons finished), any_path (one of these paths finished),
# paths (this many paths finished), exercises / labs / fixes / notebooks (at least this many different
# ones), achievements (all earned), days (evidence spread over at least this many days).
RANKS: tuple[dict[str, Any], ...] = (
    {"id": "apprentice", "name": "Apprentice", "tier": "The Forge", "needs": [],
     "prompt": "A single glowing ember resting on a soot-dark anvil, the bright eyes of a young grey wolf pup reflected "
               "in its light, a few sparks drifting upward."},
    {"id": "forge-initiate", "name": "Forge Initiate", "tier": "The Forge", "needs": [{"exercises": 1}],
     "prompt": "A wolf pup sitting beside a leather bellows, a small orange flame rising from fresh coals, simple iron "
               "tongs crossed behind it."},
    {"id": "iron-forger", "name": "Iron Forger", "tier": "The Forge",
     "needs": [{"modules": ["LM-01", "LM-02", "LM-03", "LM-04", "LM-05", "LAB-01"]}],
     "prompt": "A young wolf wearing a plain hammered iron collar, standing before a glowing iron bar on an anvil, a "
               "smith's hammer resting at its paws."},
    {"id": "steel-forger", "name": "Steel Forger", "tier": "The Forge",
     "needs": [{"modules": ["LAB-02"]}, {"achievements": ["first-flame"]}],
     "prompt": "A lean wolf with a blue-steel sheen in its fur, a freshly quenched steel blade steaming in a stone "
               "trough behind it."},
    {"id": "master-forger", "name": "Master Forger", "tier": "The Forge",
     "needs": [{"paths": 3}, {"achievements": ["query-forger"]}],
     "prompt": "A mature wolf in a scorched leather smith's apron, a great hammer mid-strike, a ring of sparks "
               "circling the anvil."},
    {"id": "pack-runner", "name": "Pack Runner", "tier": "The Pack", "needs": [{"exercises": 25}, {"labs": 4}],
     "prompt": "Three wolves running in a line along a moonlit ridge, the lead wolf's trail glowing like molten metal."},
    {"id": "trailfinder", "name": "Trailfinder", "tier": "The Pack",
     "needs": [{"achievements": ["guardian"]}, {"fixes": 3}],
     "prompt": "A wolf with its nose to the ground following a trail of glowing amber pawprints through a dark pine "
               "forest, one print bright as a coal."},
    {"id": "wolf-guardian", "name": "Wolf Guardian", "tier": "The Pack",
     "needs": [{"any_path": ["microsoft-fabric"]}, {"achievements": ["architects-eye", "steady-hand"]}],
     "prompt": "A wolf keeping watch on a rock above a forge village at night, a round bronze shield strapped across "
               "its back, forge glow rising from below."},
    {"id": "pack-leader", "name": "Pack Leader", "tier": "The Pack", "needs": [{"paths": 5}, {"labs": 8}, {"fixes": 10}],
     "prompt": "A large silver wolf at the front of its pack, howling at a full moon rimmed in forge-orange light."},
    {"id": "argonaut", "name": "Argonaut", "tier": "The Myth", "needs": [{"any_path": ["databricks", "snowflake"]}],
     "prompt": "A wolf standing at the prow of an ancient Greek ship with one square sail and a row of oars, a "
               "wine-dark sea, a ship-shaped constellation overhead."},
    {"id": "cyclops-smith", "name": "Cyclops Smith", "tier": "The Myth",
     "needs": [{"modules": ["LAB-11"]}, {"notebooks": 15}],
     "prompt": "A wolf at the mouth of a volcanic cave forge, a great bronze anvil inside where a jagged lightning "
               "bolt is being hammered into shape, sparks lighting the cave walls."},
    {"id": "forge-architect", "name": "Forge Architect", "tier": "The Myth",
     "needs": [{"any_path": ["enterprise-architecture"]}],
     "prompt": "A wolf standing on a bronze tablet engraved with a labyrinth plan, a drafting compass and square beside "
               "it, Greek columns rising behind in glowing outline."},
    {"id": "legend-of-the-forge", "name": "Legend of the Forge", "tier": "The Myth",
     "needs": [{"paths": len(PATHS)}, {"modules": ["LAB-12"]}, {"days": 90}],
     "prompt": "A wolf of glowing molten bronze and starlight, a hammered gold laurel wreath at its neck, standing "
               "before an anvil set on a Greek column capital, a wolf constellation in the sky above."},
)


def facts(profile: dict[str, Any]) -> dict[str, Any]:
    """What the included evidence adds up to, in the terms the criteria use."""
    rows = progress.included(profile)
    finished = {str((r.get("ref") or {}).get("id")) for r in rows if r["type"] == "module.complete"}
    stamps = [d for d in (_parse(r.get("at")) for r in rows) if d]
    return {"finished": finished,
            "exercises": len({(r.get("ref") or {}).get("id") for r in rows if r["type"] == "lab.pass"}),
            "fixes": len({r["id"] for r in rows if r["type"] == "fix.checked"}),
            "notebooks": len({r["artifact"]["name"] for r in rows if r["type"] == "validate.pass"}),
            "achievements": {a["id"] for a in progress.progression(profile)["achievements"]},
            "days": (max(stamps) - min(stamps)).days if stamps else 0}


def standing(profile: dict[str, Any], live: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """The rank held, the next one and the whole ladder with each requirement's progress.

    ``live`` is the published Learn modules and labs (content.modules(include_drafts=False)): it decides
    what can be earned today. A rank needing lessons that haven't shipped is ``coming_soon``."""
    live = list(live)
    shipped = {m["id"] for m in live}
    available = {"exercises": sum(len(m.get("exercises") or []) for m in live),
                 "labs": sum(1 for m in live if m.get("kind") == "lab")}
    f = facts(profile)
    ladder, held = [], True
    for rank in RANKS:
        checks = [_check(need, f, shipped, available) for need in rank["needs"]]
        met = all(c["met"] for c in checks)
        held = held and met
        ladder.append({"id": rank["id"], "name": rank["name"], "tier": rank["tier"], "held": held,
                       "coming_soon": not met and any(c["coming_soon"] for c in checks),
                       "checks": checks, "prompt": f"{rank['prompt']} {PROMPT_STYLE}"})
    current = max(i for i, r in enumerate(ladder) if r["held"])
    nxt = ladder[current + 1] if current + 1 < len(ladder) else None
    return {"rank": ladder[current]["name"], "rank_id": ladder[current]["id"], "number": current + 1,
            "of": len(ladder), "next": nxt, "ladder": ladder, "ladder_version": LADDER_VERSION}


def _check(need: dict[str, Any], f: dict[str, Any], shipped: set[str], available: dict[str, int]) -> dict[str, Any]:
    (kind, want), = need.items()
    if kind == "modules":
        have = sum(m in f["finished"] for m in want)
        return _row(f"Finish {_names(want)}", have, len(want), any(m not in shipped for m in want))
    if kind == "any_path":
        best = max(want, key=lambda p: _path_done(p, f["finished"]))
        done = [p for p in want if _path_done(p, f["finished"]) == len(PATHS[p]["modules"])]
        title = " or ".join(f"the {PATHS[p]['title']} path" for p in want)
        unshipped = all(any(m not in shipped for m in PATHS[p]["modules"]) for p in want)
        return {**_row(f"Finish {title}", _path_done(best, f["finished"]), len(PATHS[best]["modules"]), unshipped),
                "met": bool(done)}
    if kind == "paths":
        have = sum(_path_done(p, f["finished"]) == len(v["modules"]) for p, v in PATHS.items())
        ready = sum(all(m in shipped for m in v["modules"]) for v in PATHS.values())
        return _row(f"Finish {want} learning path{'s' if want != 1 else ''}", have, want, ready < want)
    if kind == "achievements":
        names = [str(progress.ACHIEVEMENTS[a]["name"]) for a in want]
        return _row(f"Earn {' and '.join(names)}", sum(a in f["achievements"] for a in want), len(want), False)
    if kind == "labs":
        have = sum(m.startswith("LAB-") for m in f["finished"])
        return _row(f"Finish {want} different labs", have, want, available["labs"] < want)
    if kind == "exercises":
        return _row(f"Pass {want} different exercise{'s' if want != 1 else ''}", f["exercises"], want,
                    available["exercises"] < want)
    if kind == "fixes":
        return _row(f"Check {want} best-practice fixes with Compare", f["fixes"], want, False)
    if kind == "notebooks":
        text = "Validate a notebook with a clean run" if want == 1 else f"Validate {want} different notebooks with a clean run"
        return _row(text, f["notebooks"], want, False)
    if kind == "days":
        return _row(f"Show this evidence over at least {want} days", f["days"], want, False)
    raise ValueError(f"unknown rank requirement {kind}")


def _row(text: str, have: int, need: int, coming_soon: bool) -> dict[str, Any]:
    met = have >= need
    return {"text": text, "have": min(have, need), "need": need, "met": met, "coming_soon": coming_soon and not met}


def _path_done(path: str, finished: set[str]) -> int:
    return sum(m in finished for m in PATHS[path]["modules"])


def _names(ids: list[str]) -> str:
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + f" and {ids[-1]}"


def _parse(stamp: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(stamp)) if stamp else None
    except ValueError:
        return None
