"""Developer profile: a private record of work KratosForge verified, and what it adds up to (KF-DX-001).

The profile stores **evidence rows**, one per verified accomplishment, each a reference to a record
the app already keeps: a Validate result (its id and the notebook's code revision), a proof's run
receipt, or a Learn exercise whose checks all passed on this PC (the lesson's id and content version;
rules v2). It
never copies code, cell outputs, table names or rows, tokens or connection strings. Everything else
(XP, level, achievements, skill coverage, the professional report) is worked out from the rows and
the rules below every time it is shown, so excluding a row takes it out of all of them.

Rules are versioned (``RULES_VERSION``); each row keeps the version it was recorded under. XP is
for fun and is not a skill rating; skills show evidence coverage, never a score. A row's id is the
SHA-256 of its type, notebook and code revision, so validating unchanged code again adds nothing.

One file per signed-in account: ``<data>/profile/<sha256(username)[:16]>/profile.json``, written
with jsonfile. Nothing here is sent anywhere.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Any

from . import jsonfile

SCHEMA = "kratosforge/profile@1"
RULES_VERSION = 2   # v2 (LX-07): Learn exercises and lessons, and five Learn achievements
LEVEL_CURVE = "v1"

PASSED = ("ready", "ready_with_warnings")
DAILY_NOTEBOOK_XP_CAP = 3   # new notebooks a day that earn XP after the first
NEW_NOTEBOOK_XP = 10

DIMENSIONS = ("Data Engineering", "Engineering Quality", "Performance & Optimization", "Architecture & Reusability",
              "Reliability & Operations", "Security & Governance", "Learning & Growth", "Delivery & Impact")
EVENT_DIMENSIONS = {"validate.pass": ("Data Engineering", "Engineering Quality"),
                    "prove.match": ("Engineering Quality", "Performance & Optimization"),
                    "lab.pass": ("Learning & Growth",),
                    "module.complete": ("Learning & Growth",)}
# A Learn exercise also counts toward the skill its checks show (rules v2).
CHECK_DIMENSIONS = {"faster_than": "Performance & Optimization", "table_shape": "Architecture & Reusability",
                    "integrity": "Architecture & Reusability", "defects_found": "Engineering Quality"}
TRACKED = {d for dims in EVENT_DIMENSIONS.values() for d in dims} | set(CHECK_DIMENSIONS.values())

ACHIEVEMENTS = {
    "first-flame": {"name": "First Flame", "xp": 50, "badge": "flame",
                    "description": "Your first notebook validated with a clean run.",
                    "criteria": "A notebook that reads or writes a lakehouse table passes Validate with a clean run "
                                "(Ready or Ready with warnings).",
                    "evidence": "One Validate result and the notebook's code revision."},
    "steady-hand": {"name": "Steady Hand", "xp": 100, "badge": "check",
                    "description": "Five different notebooks validated with clean runs.",
                    "criteria": "Five notebooks with different names have each passed Validate with a clean run.",
                    "evidence": "Five Validate results, one per notebook."},
    "proof-forged": {"name": "Proof Forged", "xp": 150, "badge": "proof",
                     "description": "A notebook proven to give the same results on this PC and in Fabric.",
                     "criteria": "A Prove run finishes and its verification finds every table the same on this PC and "
                                 "in Fabric. It runs the notebook in Fabric too, so it is never needed to level up.",
                     "evidence": "One run receipt (its SHA-256 id) with the verification."},
    # Learn (rules v2). Each needs an exercise whose checks all passed on this PC, in a published lesson.
    "first-spark": {"name": "The First Spark", "xp": 50, "badge": "spark",
                    "description": "Your first Learn exercise passed on this PC.",
                    "criteria": "Every check of one exercise in a published Learn lesson or lab passes on this PC.",
                    "evidence": "One passed exercise: the lesson's id and content version."},
    "data-smith": {"name": "The Data Smith", "xp": 100, "badge": "anvil",
                   "description": "Five different Learn exercises passed.",
                   "criteria": "Five different exercises in published Learn lessons or labs have each passed every check. "
                               "Passing the same one again doesn't count.",
                   "evidence": "Five passed exercises, one per exercise."},
    "query-forger": {"name": "The Query Forger", "xp": 100, "badge": "bolt",
                     "description": "A query made measurably faster, with the same answer.",
                     "criteria": "A Learn exercise with a speed check passes: the same result as the reference, and at "
                                 "least as many times faster as the exercise asks, measured on this PC.",
                     "evidence": "One passed exercise with its measured times."},
    "architects-eye": {"name": "The Architect's Eye", "xp": 100, "badge": "eye",
                       "description": "A data model built right: its keys, types and relationships check out.",
                       "criteria": "A Learn exercise with a table shape or referential integrity check passes on this PC.",
                       "evidence": "One passed exercise."},
    "guardian": {"name": "The Guardian", "xp": 100, "badge": "shield",
                 "description": "Every planted data quality problem in a lab found.",
                 "criteria": "A Learn exercise that plants data quality problems passes: your query finds every one.",
                 "evidence": "One passed exercise."},
}
LEARN_ACHIEVEMENTS = (("first-spark", None), ("query-forger", "faster_than"), ("architects-eye", "table_shape"),
                      ("architects-eye", "integrity"), ("guardian", "defects_found"))
DATA_SMITH_EXERCISES = 5
MODULE_COMPLETE_XP = 25     # each lesson or lab finished for the first time, within the daily cap
STEADY_HAND_NOTEBOOKS = 5
NEW_PROOF_XP = 20           # each further notebook proven, after Proof Forged

TITLES = ((50, "Legend of the Forge"), (40, "Wolf Guardian"), (30, "Forge Architect"), (20, "Master Forger"),
          (15, "Steel Forger"), (10, "Iron Forger"), (5, "Forge Initiate"), (1, "Apprentice"))

DEFAULT_SETTINGS = {"record": True, "progression": True, "notifications": True, "backfilled": False}
IDENTITY_KEYS = ("display_name", "role", "technologies")
MAX_TEXT = 2000


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def account_key(username: str | None) -> str:
    """The profile folder for an account: a hash, so the sign-in name isn't a folder name."""
    if not username:
        return "signed-out"
    return hashlib.sha256(username.strip().lower().encode("utf-8")).hexdigest()[:16]


def evidence_id(kind: str, artifact: str, revision: str) -> str:
    return hashlib.sha256(f"{kind}|{artifact}|{revision}".encode("utf-8")).hexdigest()


# ---- the file


def path_for(root: Path, username: str | None) -> Path:
    return Path(root) / "profile" / account_key(username) / "profile.json"


def empty() -> dict[str, Any]:
    return {"schema": SCHEMA, "settings": dict(DEFAULT_SETTINGS), "identity": {k: "" for k in IDENTITY_KEYS},
            "evidence": [], "excluded": [], "report": {"summary": ""}, "notice": ""}


def load(path: Path) -> dict[str, Any]:
    """The profile; a fresh one when there is none. A file that can't be read is set aside (jsonfile)
    and the fresh profile carries a notice saying so, kept until the person dismisses it."""
    aside = set(path.parent.glob(f"{path.name}.corrupt-*")) if path.parent.exists() else set()
    data = jsonfile.read_json(path, None)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        out = empty()
        new = sorted(set(path.parent.glob(f"{path.name}.corrupt-*")) - aside) if path.parent.exists() else []
        if new:
            out["notice"] = (f"Your profile file couldn't be read, so it was set aside as {new[-1].name} in the same folder "
                             "and a new one started. What it held is still in that file.")
            save(path, out)  # the notice stays until it has been dismissed
        return out
    out = empty()
    out["notice"] = str(data.get("notice") or "")[:MAX_TEXT]
    out["settings"].update({k: bool(v) for k, v in (data.get("settings") or {}).items() if k in DEFAULT_SETTINGS})
    out["identity"].update({k: str(v)[:MAX_TEXT] for k, v in (data.get("identity") or {}).items() if k in IDENTITY_KEYS})
    out["evidence"] = [r for r in data.get("evidence") or [] if isinstance(r, dict) and r.get("id")]
    out["excluded"] = [str(i) for i in data.get("excluded") or []]
    out["report"]["summary"] = str((data.get("report") or {}).get("summary") or "")[:MAX_TEXT]
    return out


def save(path: Path, profile: dict[str, Any]) -> None:
    jsonfile.write_json(path, profile)


# ---- recognising evidence


def from_validation(record: dict[str, Any], origin: str = "live") -> dict[str, Any] | None:
    """The evidence row for a finished validation, or None when it isn't a verified accomplishment:
    the clean run must have finished, the verdict passed, and the notebook read or write a lakehouse table."""
    run = record.get("run") or {}
    tables = record.get("tables") or {}
    revision = (record.get("code") or {}).get("revision")
    name = record.get("name")
    if run.get("status") != "ok" or record.get("verdict") not in PASSED or not revision or not name:
        return None
    reads, writes = len(tables.get("reads") or []), len(tables.get("writes") or [])
    if not reads and not writes:
        return None  # a notebook that touches no table passes Validate but isn't data engineering evidence
    return {"id": evidence_id("validate.pass", name, revision), "type": "validate.pass",
            "at": record.get("finished_at") or _now(),
            "artifact": {"kind": "notebook", "name": name},
            "ref": {"source": "validate", "id": record.get("id"), "revision": revision},
            "status": "verified", "verdict": record["verdict"],
            "detail": {"reads": reads, "writes": writes, "runs_on": "pc"},
            "measurement": None, "dimensions": list(EVENT_DIMENSIONS["validate.pass"]),
            "rules_version": RULES_VERSION, "recorded_at": _now(), "origin": origin, "contribution": None}


def from_proof(record: dict[str, Any], receipt_id: str | None, origin: str = "live") -> dict[str, Any] | None:
    """The evidence row for a finished proof, or None unless every table compared the same on both
    sides. Times are the measured ones from its scorecard; nothing estimated is kept."""
    verdict = record.get("verdict") or {}
    revision = (record.get("code") or {}).get("revision")
    name = record.get("name")
    if record.get("state") != "done" or verdict.get("verdict") != "same" or not receipt_id or not revision or not name:
        return None
    time = (record.get("scorecard") or {}).get("time") or {}
    pc, fabric = time.get("pc") or {}, time.get("fabric") or {}
    measured = {"pc_seconds": pc.get("seconds") if pc.get("kind") == "measured" else None,
                "fabric_seconds": fabric.get("total_seconds") if fabric.get("kind") == "measured" else None}
    return {"id": evidence_id("prove.match", name, revision), "type": "prove.match",
            "at": record.get("finished_at") or _now(),
            "artifact": {"kind": "notebook", "name": name},
            "ref": {"source": "receipt", "id": receipt_id, "revision": revision},
            "status": "verified", "verdict": "same",
            "detail": {"level": verdict.get("level"), "tables": len(record.get("tables") or [])},
            "measurement": {**measured, "kind": "measured", "source": "proof scorecard"}
            if any(v is not None for v in measured.values()) else None,
            "dimensions": list(EVENT_DIMENSIONS["prove.match"]),
            "rules_version": RULES_VERSION, "recorded_at": _now(), "origin": origin, "contribution": None}


def from_lab(result: dict[str, Any], module: dict[str, Any], origin: str = "live") -> dict[str, Any] | None:
    """The evidence row for a Learn exercise whose checks all passed, or None. Only published lessons
    count: a draft may still change. The row keeps the lesson's id, exercise and content version, the
    check kinds and a speed check's measured times; never the answer or the lab's data."""
    if not result.get("passed") or module.get("status") != "live" or not result.get("results"):
        return None
    mid, exercise, version = module["id"], result["exercise"], int(result.get("version") or module.get("version") or 1)
    number = next((i for i, ex in enumerate(module.get("exercises") or [], 1) if ex.get("id") == exercise), None)
    kinds = list(dict.fromkeys(str(r.get("kind")) for r in result["results"]))   # in the exercise's order
    timed = next((r.get("measured") for r in result["results"] if r.get("kind") == "faster_than" and r.get("measured")), None)
    dims = list(EVENT_DIMENSIONS["lab.pass"]) + sorted({CHECK_DIMENSIONS[k] for k in kinds if k in CHECK_DIMENSIONS})
    return {"id": evidence_id("lab.pass", f"{mid}/{exercise}", f"v{version}|{','.join(sorted(kinds))}"), "type": "lab.pass",
            "at": _now(), "artifact": {"kind": module.get("kind") or "module", "name": mid, "exercise": exercise,
                                       "number": number, "title": str(module.get("title") or "")[:200]},
            "ref": {"source": "learn", "id": f"{mid}/{exercise}", "revision": f"v{version}"},
            "status": "verified", "verdict": "passed", "detail": {"checks": kinds, "level": module.get("level")},
            "measurement": {**timed, "kind": "measured", "source": "Learn speed check on this PC"} if timed else None,
            "dimensions": dims, "rules_version": RULES_VERSION, "recorded_at": _now(), "origin": origin, "contribution": None}


def module_complete(profile: dict[str, Any], module: dict[str, Any]) -> dict[str, Any] | None:
    """The row for finishing a lesson or lab: every exercise of its current version has a lab.pass row."""
    if module.get("status") != "live" or not module.get("exercises"):
        return None
    mid, version = module["id"], f"v{int(module.get('version') or 1)}"
    passed = {(r.get("ref") or {}).get("id") for r in profile["evidence"]
              if r.get("type") == "lab.pass" and (r.get("ref") or {}).get("revision") == version}
    if any(f"{mid}/{ex['id']}" not in passed for ex in module["exercises"]):
        return None
    return {"id": evidence_id("module.complete", mid, version), "type": "module.complete", "at": _now(),
            "artifact": {"kind": module.get("kind") or "module", "name": mid, "title": str(module.get("title") or "")[:200]},
            "ref": {"source": "learn", "id": mid, "revision": version},
            "status": "verified", "verdict": "passed",
            "detail": {"exercises": len(module["exercises"]), "path": module.get("path"), "level": module.get("level")},
            "measurement": None, "dimensions": list(EVENT_DIMENSIONS["module.complete"]),
            "rules_version": RULES_VERSION, "recorded_at": _now(), "origin": "live", "contribution": None}


def add(profile: dict[str, Any], row: dict[str, Any]) -> bool:
    """Add a row unless one with its id is there already. Returns whether it was added."""
    if any(r["id"] == row["id"] for r in profile["evidence"]):
        return False
    profile["evidence"].append(row)
    return True


# ---- what the evidence adds up to


def xp_for_level(level: int) -> int:
    return round(100 * (level - 1) ** 1.5)


def level_for(xp: int) -> int:
    level = 1
    while xp >= xp_for_level(level + 1):
        level += 1
    return level


def title_for(level: int) -> str:
    return next(t for floor, t in TITLES if level >= floor)


def included(profile: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = set(profile["excluded"])
    rows = [r for r in profile["evidence"] if r["id"] not in excluded and r.get("status") == "verified"]
    return sorted(rows, key=lambda r: (r.get("at") or "", r["id"]))


def progression(profile: dict[str, Any]) -> dict[str, Any]:
    """XP per row, achievements earned, the level and the milestones on the way (newest first), from the
    included rows in date order."""
    seen: set[str] = set()
    proven: set[str] = set()
    learned: set[str] = set()
    per_day: dict[str, int] = {}
    xp_by_row: dict[str, int] = {}
    earned: list[dict[str, Any]] = []
    milestones: list[dict[str, Any]] = []
    reached = 1
    for r in included(profile):
        before = len(earned)
        _progress_row(r, seen, proven, per_day, xp_by_row, earned, learned)
        milestones += [{"kind": "achievement", "at": a["at"], "id": a["id"], "text": f"Earned {a['name']} (+{a['xp']} XP)"}
                       for a in earned[before:]]
        now = level_for(sum(xp_by_row.values()) + sum(a["xp"] for a in earned))
        milestones += [{"kind": "level", "at": r.get("at"), "level": lv, "text": f"Reached level {lv}, {title_for(lv)}"}
                       for lv in range(reached + 1, now + 1)]
        reached = now
    xp = sum(xp_by_row.values()) + sum(a["xp"] for a in earned)
    level = level_for(xp)
    got = {a["id"] for a in earned}
    notebooks = len(seen)
    upcoming = [{"id": "first-flame", "name": ACHIEVEMENTS["first-flame"]["name"], "xp": ACHIEVEMENTS["first-flame"]["xp"],
                 "progress": "Validate a notebook that reads or writes a lakehouse table"}] if "first-flame" not in got else []
    if "steady-hand" not in got:
        upcoming.append({"id": "steady-hand", "name": ACHIEVEMENTS["steady-hand"]["name"], "xp": ACHIEVEMENTS["steady-hand"]["xp"],
                         "progress": f"{notebooks} of {STEADY_HAND_NOTEBOOKS} different notebooks"})
    if "proof-forged" not in got:
        upcoming.append({"id": "proof-forged", "name": ACHIEVEMENTS["proof-forged"]["name"], "xp": ACHIEVEMENTS["proof-forged"]["xp"],
                         "progress": "Prove a notebook gives the same results on this PC and in Fabric (optional: it runs in Fabric)"})
    exercises = sum(k.startswith("lab.pass|") for k in learned)
    if "first-spark" not in got:
        upcoming.append({"id": "first-spark", "name": ACHIEVEMENTS["first-spark"]["name"], "xp": ACHIEVEMENTS["first-spark"]["xp"],
                         "progress": "Pass an exercise in a Learn lesson or lab"})
    elif "data-smith" not in got:
        upcoming.append({"id": "data-smith", "name": ACHIEVEMENTS["data-smith"]["name"], "xp": ACHIEVEMENTS["data-smith"]["xp"],
                         "progress": f"{exercises} of {DATA_SMITH_EXERCISES} different Learn exercises"})
    return {"xp": xp, "level": level, "title": title_for(level), "level_xp": xp_for_level(level),
            "next_level_xp": xp_for_level(level + 1), "achievements": earned, "next": upcoming,
            "xp_by_row": xp_by_row, "milestones": milestones[::-1], "rules_version": RULES_VERSION, "level_curve": LEVEL_CURVE}


def _progress_row(r: dict[str, Any], seen: set[str], proven: set[str], per_day: dict[str, int],
                  xp_by_row: dict[str, int], earned: list[dict[str, Any]], learned: set[str]) -> None:
    """One included row's XP and any achievement it completes (rules v2)."""
    xp_by_row[r["id"]] = 0
    name = r["artifact"]["name"]
    day = (r.get("at") or "")[:10]
    if r["type"] == "prove.match":
        if name in proven:
            return  # the same notebook proven again: evidence, no XP
        proven.add(name)
        if len(proven) == 1:
            earned.append(_award("proof-forged", r))
        elif per_day.get(day, 0) < DAILY_NOTEBOOK_XP_CAP:
            per_day[day] = per_day.get(day, 0) + 1
            xp_by_row[r["id"]] = NEW_PROOF_XP
        return
    if r["type"] in ("lab.pass", "module.complete"):
        _learn_row(r, learned, per_day, xp_by_row, earned)
        return
    if r["type"] != "validate.pass":
        return
    if name in seen:
        return  # a new revision of a notebook already counted: evidence, no XP
    seen.add(name)
    if len(seen) == 1:
        earned.append(_award("first-flame", r))
    else:
        if per_day.get(day, 0) < DAILY_NOTEBOOK_XP_CAP:
            per_day[day] = per_day.get(day, 0) + 1
            xp_by_row[r["id"]] = NEW_NOTEBOOK_XP
    if len(seen) == STEADY_HAND_NOTEBOOKS:
        earned.append(_award("steady-hand", r))


def _learn_row(r: dict[str, Any], learned: set[str], per_day: dict[str, int], xp_by_row: dict[str, int],
               earned: list[dict[str, Any]]) -> None:
    """A Learn row (rules v2). An exercise counts once whatever its content version, so passing it again
    or after an update adds evidence but nothing else; finishing a lesson earns a little XP within the
    same daily cap as notebooks. Nothing here grows with the number of runs."""
    got = {a["id"] for a in earned}
    key = f"{r['type']}|{(r.get('ref') or {}).get('id')}"
    if key in learned:
        return
    learned.add(key)
    if r["type"] == "module.complete":
        day = (r.get("at") or "")[:10]
        if per_day.get(day, 0) < DAILY_NOTEBOOK_XP_CAP:
            per_day[day] = per_day.get(day, 0) + 1
            xp_by_row[r["id"]] = MODULE_COMPLETE_XP
        return
    kinds = set((r.get("detail") or {}).get("checks") or [])
    for aid, kind in LEARN_ACHIEVEMENTS:
        if aid not in got and (kind is None or kind in kinds):
            earned.append(_award(aid, r))
            got.add(aid)
    if "data-smith" not in got and sum(k.startswith("lab.pass|") for k in learned) == DATA_SMITH_EXERCISES:
        earned.append(_award("data-smith", r))


def _award(aid: str, row: dict[str, Any]) -> dict[str, Any]:
    a = ACHIEVEMENTS[aid]
    return {"id": aid, "name": a["name"], "xp": a["xp"], "badge": a["badge"], "description": a["description"],
            "criteria": a["criteria"], "at": row.get("at"), "evidence": row["id"], "rules_version": RULES_VERSION}


def coverage(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Each skill dimension's evidence coverage (rules v2). Missing evidence is never a low score."""
    rows = included(profile)
    out = []
    for d in DIMENSIONS:
        mine = [r for r in rows if d in (r.get("dimensions") or [])]
        artifacts = {r["artifact"]["name"] for r in mine}
        kinds = {r["type"] for r in mine}
        days = _span_days([r.get("at") for r in mine])
        strong = any(r.get("measurement") or (r.get("ref") or {}).get("source") == "receipt" for r in mine)
        n = len(mine)
        if d not in TRACKED:
            state = "Not tracked yet"
        elif not n:
            state = "Not seen yet"
        elif n >= 12 and len(artifacts) >= 5 and days >= 60 and len(kinds) >= 2:
            state = "Established"
        elif n >= 6 and len(artifacts) >= 3 and days >= 14 and strong:
            state = "Demonstrated"
        elif n >= 3 and len(artifacts) >= 2 and days >= 7:
            state = "Emerging"
        else:
            state = "Insufficient evidence"
        out.append({"name": d, "state": state, "rows": n, "artifacts": len(artifacts), "days": days})
    return out


def _span_days(stamps: list[str | None]) -> int:
    times = sorted(t for t in (_parse(s) for s in stamps) if t)
    return (times[-1] - times[0]).days if len(times) > 1 else 0


def _parse(stamp: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return None


# ---- the professional report


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+|!~-])")


def md(text: str) -> str:
    """``text`` as literal Markdown: a notebook called ``*x*`` or ``[a](b)`` stays text."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(text).split()))


def report(profile: dict[str, Any], days: int = 90, include_achievements: bool = False,
           now: dt.datetime | None = None) -> dict[str, Any]:
    """A review-ready summary of the included rows in the last ``days`` days, as Markdown and plain
    text. Every line says where it came from; nothing is inferred beyond the evidence. No XP, levels
    or badges unless ``include_achievements``."""
    now = now or dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=days)
    rows = [r for r in included(profile) if (_parse(r.get("at")) or now) >= start]
    refs = {r["id"]: f"E{i}" for i, r in enumerate(rows, 1)}
    validated = [r for r in rows if r["type"] == "validate.pass"]
    proved = [r for r in rows if r["type"] == "prove.match"]
    latest: dict[str, dict[str, Any]] = {r["artifact"]["name"]: r for r in validated}
    latest_proof: dict[str, dict[str, Any]] = {r["artifact"]["name"]: r for r in proved}
    who = profile["identity"]
    name = who.get("display_name") or "Developer"
    period = f"{start:%d %B %Y} to {now:%d %B %Y}"
    entered = profile["report"]["summary"].strip()
    n, m = len(latest), len(latest_proof)
    generated = " ".join(x for x in (
        (f"Validated {n} notebooks that read or write lakehouse tables; each finished a clean run in a separate "
         "process on a workstation." if n > 1 else
         "Validated 1 notebook that reads or writes lakehouse tables; it finished a clean run in a separate "
         "process on a workstation." if n else ""),
        (f"Proved {m} notebook{'s' if m != 1 else ''} gave the same results on a workstation and in Microsoft Fabric."
         if m else "")) if x)
    lines = ["# Engineering accomplishments", "", f"{md(name)}{' · ' + md(who['role']) if who.get('role') else ''} · {period}", "",
          "Each statement is marked Verified (with its evidence reference), Entered by you, Generated summary "
          "(worded from verified evidence) or Not measured.", "", "## Executive summary", ""]
    if entered:
        lines.append(f"{entered} *(Entered by you)*")
    elif generated:
        lines.append(f"{generated} *(Generated summary)*")
    else:
        lines.append("No verified accomplishments in this period. *(Not measured)*")
    lines += ["", "## Key contributions", ""]
    contributions = []
    for nb, r in latest.items():
        ids = ", ".join(refs[x["id"]] for x in validated if x["artifact"]["name"] == nb)
        verdict = "Ready" if r["verdict"] == "ready" else "Ready with warnings"
        d = r.get("detail") or {}
        rest = (f": clean run finished {verdict} (reads {d.get('reads', 0)} lakehouse "
                f"table{'s' if d.get('reads', 0) != 1 else ''}, writes {d.get('writes', 0)}).")
        contributions.append(f"Validated notebook {nb}{rest}")
        lines.append(f"- Validated notebook {md(nb)}{rest} *(Verified, {ids})*")
    for nb, r in latest_proof.items():
        ids = ", ".join(refs[x["id"]] for x in proved if x["artifact"]["name"] == nb)
        t = (r.get("detail") or {}).get("tables") or 0
        rest = (f" gives the same results on a workstation and in Microsoft Fabric ({t} table{'s' if t != 1 else ''} "
                "compared).")
        contributions.append(f"Proved notebook {nb}{rest}")
        lines.append(f"- Proved notebook {md(nb)}{rest} *(Verified, {ids})*")
    if not latest and not latest_proof:
        lines.append("- None recorded in this period. *(Not measured)*")
    lines += ["", "## Engineering quality", ""]
    if validated:
        lines.append(f"{len(validated)} validated revision{'s' if len(validated) != 1 else ''} of {n} notebook{'s' if n != 1 else ''}, "
                     "each run from the top in a fresh process with table writes kept in a sandbox. *(Verified)*")
    if proved:
        lines.append(f"{len(proved)} proof{'s' if len(proved) != 1 else ''} whose results matched in Microsoft Fabric, "
                     "each with a run receipt. *(Verified)*")
    if not rows:
        lines.append("No validations in this period. *(Not measured)*")
    lines += ["", "## Performance and efficiency", ""]
    timed = [r for r in proved if r.get("measurement")]
    for r in timed:
        mm = r["measurement"]
        parts = [f"{label} {mm[k]:g} s" for k, label in (("pc_seconds", "workstation"), ("fabric_seconds", "Microsoft Fabric"))
                 if isinstance(mm.get(k), (int, float))]
        lines.append(f"- {md(r['artifact']['name'])}: {', '.join(parts)} end to end, measured in the same proof. "
                     f"*(Verified, {refs[r['id']]})*")
    if not timed:
        lines.append("No before and after measurements in this period. *(Not measured)*")
    lines += ["", "## Learning", ""]
    finished = {r["ref"]["id"]: r for r in rows if r["type"] == "module.complete"}
    passed = [r for r in rows if r["type"] == "lab.pass"]
    for mid, r in finished.items():
        ids = ", ".join(refs[x["id"]] for x in rows if x["type"] == "module.complete" and x["ref"]["id"] == mid)
        what = "lab" if r["artifact"].get("kind") == "lab" else "lesson"
        title = r["artifact"].get("title") or mid
        lines.append(f"- Finished KratosForge Learn {what} {md(mid)}, {md(title)}: every exercise passed its checks "
                     f"on a workstation. *(Verified, {ids})*")
    if passed:
        n_ex = len({r["ref"]["id"] for r in passed})
        lines.append(f"{n_ex} Learn exercise{'s' if n_ex != 1 else ''} passed, each checked against a reference answer "
                     "on a workstation. *(Verified)*")
    if not finished and not passed:
        lines.append("No Learn lessons or labs in this period. *(Not measured)*")
    lines += ["", "## Technical competencies", ""]
    shown = [c for c in coverage(profile) if c["state"] in ("Emerging", "Demonstrated", "Established")]
    lines.append("; ".join(f"{c['name']}: {c['state'].lower()} ({c['rows']} verified items across {c['artifacts']} "
                        f"artifacts)" for c in shown) + ". *(Generated summary)*" if shown
              else "Not enough evidence yet to name a competency. *(Not measured)*")
    if include_achievements:
        p = progression(profile)
        lines += ["", "## KratosForge achievements", "",
               f"Level {p['level']} ({p['title']}), {p['xp']} XP. Levels and achievements are motivational and are "
               "not a skill rating."]
        lines += [f"- {a['name']}: {a['description']}" for a in p["achievements"]]
    lines += ["", "## Evidence appendix", ""]
    for r in rows:
        ref = r.get("ref") or {}
        if ref.get("source") == "learn":
            lines.append(f"- {refs[r['id']]} · {r.get('at', '')[:10]} · Learn {md(ref.get('id') or '')} · version "
                         f"{md((ref.get('revision') or '').removeprefix('v'))} · {r['verdict']}")
            continue
        source = (f"Run receipt {ref.get('id')} (SHA-256)" if ref.get("source") == "receipt"
                  else f"Validate {ref.get('id')}")
        lines.append(f"- {refs[r['id']]} · {r.get('at', '')[:10]} · {source} · code revision "
                  f"{(ref.get('revision') or '')[:12]} · {r['verdict']}")
    if not rows:
        lines.append("- None.")
    lines.append("")
    summary = "\n".join([entered or generated or "No verified accomplishments in this period.", "",
                         *[f"- {c}" for c in contributions]]).strip()
    return {"markdown": "\n".join(lines), "summary": summary, "period": {"from": start.isoformat(timespec="seconds"),
            "to": now.isoformat(timespec="seconds"), "days": days}, "rows": len(rows)}
