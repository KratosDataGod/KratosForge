"""Academy certification registry (ACD-001): each certification's published objectives, read and checked.

A certification is a content pack under ``packs/<id>/``: ``pack.json`` (schema ``kratosforge/academy-pack@1``)
and ``certification.json`` (schema ``kratosforge/academy-certification@1``), which holds the vendor's
objective hierarchy word for word (domains with their published weight ranges, skill areas, objectives)
and, for each objective, what Academy plans or has for it: the competency in plain words, whether it can
only be shown in Fabric, the best verification class this PC can give, exercises, capstones, Learn
lessons, planned questions, validators, documentation links, coverage-gap records and status.

``source.snapshot_sha256`` is the hash of the published part only (``snapshot()``), so a change to the
vendor's list can't slip in unnoticed: editing an objective's words without updating the hash is a
problem, and the study guide check (ACD-020) compares the live page with the same hash.

Content is data. Nothing here runs code, reads the network or writes anything. ``problems()`` lists
everything wrong with the bundled packs; CI fails while it has any (tests/test_academy_registry.py).
No published objective may be left out, and an objective that needs Fabric must say why in a gap record.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..learn import content as learn_content

PACK_SCHEMA = "kratosforge/academy-pack@1"
CERT_SCHEMA = "kratosforge/academy-certification@1"
ROOT = Path(__file__).resolve().parent / "packs"

# Verification classes (docs/academy/architecture.md). A local exercise can reach any but FABRIC VERIFIED.
CLASSES = ("local_verified", "simulated", "fabric_verified", "knowledge_only")
LOCAL_CLASSES = ("local_verified", "simulated", "knowledge_only")
VALIDATORS = ("result_matches", "schema_matches", "data_quality", "delta_table", "notebook_ran", "pipeline_outcome",
              "config_matches", "artifact_present", "faster_than", "fabric_check", "assessment")
STATUSES = ("planned", "in_progress", "covered", "retired")
PACK_STATUSES = ("draft", "live", "retired")
DOC_HOSTS = ("learn.microsoft.com",)

_PACK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXERCISE = re.compile(r"^E\d{2}$")
_CAPSTONE = re.compile(r"^C\d$")
_GAP = re.compile(r"^G-\d{2}$")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def pack_ids(root: Path = ROOT) -> list[str]:
    """Every pack's id, sorted."""
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "pack.json").is_file())


def pack(pack_id: str, root: Path = ROOT) -> dict[str, Any]:
    if not _PACK_ID.match(str(pack_id)) or not (root / pack_id / "pack.json").is_file():
        raise ValueError(f"Not a certification pack: {pack_id}")
    return dict(_read_json(root / pack_id / "pack.json"))


def certification(pack_id: str, root: Path = ROOT) -> dict[str, Any]:
    """A pack's certification: the published hierarchy and Academy's plan for each objective."""
    p = pack(pack_id, root)
    return dict(_read_json(root / pack_id / p["certification"]))


def objectives(cert: dict[str, Any]) -> list[dict[str, Any]]:
    """Every objective in published order, each with its ``domain`` and ``skill`` ids added."""
    return [{**o, "domain": d["id"], "skill": s["id"]}
            for d in cert.get("domains") or [] for s in d.get("skills") or [] for o in s.get("objectives") or []]


def objective(cert: dict[str, Any], objective_id: str) -> dict[str, Any]:
    for o in objectives(cert):
        if o["id"] == objective_id:
            return o
    raise ValueError(f"{cert.get('id')} has no objective {objective_id}")


def snapshot(cert: dict[str, Any]) -> str:
    """SHA-256 of what the vendor publishes, one line per entry: domain titles and weight ranges, skill
    area titles, objective ids and words. Academy's own fields don't change it."""
    lines = []
    for d in cert.get("domains") or []:
        w = d.get("weight") or {}
        lines.append(f"D|{d.get('id')}|{d.get('title')}|{w.get('min')}-{w.get('max')}")
        for s in d.get("skills") or []:
            lines.append(f"S|{s.get('id')}|{s.get('title')}")
            lines += [f"O|{o.get('id')}|{o.get('text')}" for o in s.get("objectives") or []]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def coverage(cert: dict[str, Any]) -> dict[str, Any]:
    """Counts for the coverage summary: objectives, by best local class, needing Fabric, with exercises."""
    objs = objectives(cert)
    by_class = {c: sum(o.get("best_local") == c for o in objs) for c in LOCAL_CLASSES}
    return {"objectives": len(objs), "with_exercises": sum(bool(o.get("exercises")) for o in objs),
            "best_local": by_class, "fabric_required": sum(bool(o.get("fabric_required")) for o in objs),
            "gaps": len(cert.get("gaps") or []), "status": {s: sum(o.get("status") == s for o in objs) for s in STATUSES}}


def _doc_ok(link: Any) -> bool:
    m = re.match(r"^https://([^/]+)/\S+$", str(link))
    return bool(m and m.group(1) in DOC_HOSTS)


def _lesson_ids() -> set[str]:
    root = learn_content.ROOT
    return {p.name for p in root.iterdir() if p.is_dir() and learn_content.is_id(p.name)}


def _objective_problems(o: dict[str, Any], where: str, gap_ids: set[str], lessons: set[str]) -> list[str]:
    out = [f"{where}: needs {f}" for f in ("text", "competency", "best_local", "status") if not o.get(f)]
    if o.get("best_local") and o["best_local"] not in LOCAL_CLASSES:
        out.append(f"{where}: best_local is one of {', '.join(LOCAL_CLASSES)} (FABRIC VERIFIED never runs on this PC)")
    if o.get("status") and o["status"] not in STATUSES:
        out.append(f"{where}: status is one of {', '.join(STATUSES)}")
    if not isinstance(o.get("fabric_required"), bool):
        out.append(f"{where}: fabric_required is true or false")
    if not o.get("exercises"):
        out.append(f"{where}: needs at least one exercise; a published objective is never left out")
    out += [f"{where}: {e!r} isn't an exercise id like E01" for e in o.get("exercises") or [] if not _EXERCISE.match(str(e))]
    out += [f"{where}: {c!r} isn't a capstone id like C1" for c in o.get("capstones") or [] if not _CAPSTONE.match(str(c))]
    out += [f"{where}: unknown Learn lesson {m}" for m in o.get("lessons") or [] if m not in lessons]
    out += [f"{where}: unknown validator {v}" for v in o.get("validation") or [] if v not in VALIDATORS]
    if not o.get("validation"):
        out.append(f"{where}: needs the validators that will check it")
    if not isinstance(o.get("questions_planned"), int) or o["questions_planned"] < 1:
        out.append(f"{where}: questions_planned is a whole number from 1")
    if not o.get("docs"):
        out.append(f"{where}: needs at least one documentation link")
    out += [f"{where}: docs link {d} isn't the vendor's documentation" for d in o.get("docs") or [] if not _doc_ok(d)]
    out += [f"{where}: unknown gap record {g}" for g in o.get("gaps") or [] if g not in gap_ids]
    if o.get("fabric_required") is True and not o.get("gaps"):
        out.append(f"{where}: needs Fabric, so it needs a gap record saying what this PC can't show")
    if o.get("fabric_required") is False and o.get("gaps"):
        out.append(f"{where}: has gap records but fabric_required is false")
    if o.get("fabric_required") is True and "fabric_check" not in (o.get("validation") or []):
        out.append(f"{where}: needs Fabric, so its validation includes fabric_check")
    return out


def _cert_problems(pid: str, cert: dict[str, Any]) -> list[str]:
    out = []
    if cert.get("schema") != CERT_SCHEMA:
        out.append(f"{pid}: certification schema is {CERT_SCHEMA}")
    if cert.get("id") != pid:
        out.append(f"{pid}: certification id {cert.get('id')} doesn't match its pack")
    for f in ("title", "vendor", "version", "reviewed", "domains"):
        if not cert.get(f):
            out.append(f"{pid}: certification needs {f}")
    if cert.get("reviewed") and not _DATE.match(str(cert["reviewed"])):
        out.append(f"{pid}: reviewed is YYYY-MM-DD")
    src = cert.get("source") or {}
    if not _doc_ok(src.get("url")):
        out.append(f"{pid}: source.url is the vendor's study guide")
    if not _DATE.match(str(src.get("read") or "")):
        out.append(f"{pid}: source.read is the date the study guide was read (YYYY-MM-DD)")
    if src.get("effective") is None and not src.get("effective_note"):
        out.append(f"{pid}: with no effective date, source.effective_note says why")
    if src.get("snapshot_sha256") != snapshot(cert):
        out.append(f"{pid}: the published objectives changed; check them against the study guide, then set "
                   f"source.snapshot_sha256 to {snapshot(cert)}")
    gaps = cert.get("gaps") or []
    gap_ids = [g.get("id") for g in gaps]
    out += [f"{pid}: gap id {g} is like G-01" for g in gap_ids if not _GAP.match(str(g))]
    out += [f"{pid}: gap {g} is listed twice" for g in sorted({g for g in gap_ids if gap_ids.count(g) > 1})]
    for g in gaps:
        out += [f"{pid} {g.get('id')}: needs {f}" for f in ("title", "why", "closes_with", "objectives") if not g.get(f)]
    lessons = _lesson_ids()
    seen: set[str] = set()
    mins = maxes = 0
    for dn, d in enumerate(cert.get("domains") or [], 1):
        if d.get("id") != str(dn):
            out.append(f"{pid}: domain {dn} has id {d.get('id')}")
        w = d.get("weight") or {}
        if not (isinstance(w.get("min"), int) and isinstance(w.get("max"), int) and 0 < w["min"] <= w["max"] <= 100):
            out.append(f"{pid} domain {d.get('id')}: weight is the published range, whole percents, min to max")
        else:
            mins, maxes = mins + w["min"], maxes + w["max"]
        if not d.get("title") or not d.get("skills"):
            out.append(f"{pid} domain {d.get('id')}: needs a title and skill areas")
        for sn, s in enumerate(d.get("skills") or [], 1):
            if s.get("id") != f"{d.get('id')}.{sn}":
                out.append(f"{pid}: skill area {d.get('id')}.{sn} has id {s.get('id')}")
            if not s.get("title") or not s.get("objectives"):
                out.append(f"{pid} skill area {s.get('id')}: needs a title and objectives")
            for on, o in enumerate(s.get("objectives") or [], 1):
                oid = f"{s.get('id')}.{on}"
                if o.get("id") != oid:
                    out.append(f"{pid}: objective {oid} has id {o.get('id')}")
                seen.add(str(o.get("id")))
                out += _objective_problems(o, f"{pid} {o.get('id')}", set(gap_ids), lessons)
    if cert.get("domains") and not mins <= 100 <= maxes:
        out.append(f"{pid}: the domains' weight ranges ({mins}-{maxes}%) can't add up to 100%")
    for g in gaps:
        listed = set(g.get("objectives") or [])
        out += [f"{pid} {g.get('id')}: unknown objective {o}" for o in sorted(listed - seen)]
        named_by = {o["id"] for o in objectives(cert) if g.get("id") in (o.get("gaps") or [])}
        out += [f"{pid} {g.get('id')}: objective {o} doesn't name this gap" for o in sorted(listed & seen - named_by)]
        out += [f"{pid} {g.get('id')}: objective {o} names this gap but the gap doesn't list it" for o in sorted(named_by - listed)]
    return out


def problems(root: Path = ROOT) -> list[str]:
    """Everything wrong with the packs under ``root``, in words; empty when they can ship."""
    out: list[str] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        pid = folder.name
        if not _PACK_ID.match(pid):
            out.append(f"{pid}: a pack folder is named by its id, like dp-700")
            continue
        try:
            p = _read_json(folder / "pack.json")
        except (OSError, ValueError) as exc:
            out.append(f"{pid}: pack.json can't be read ({exc})")
            continue
        if p.get("schema") != PACK_SCHEMA:
            out.append(f"{pid}: pack schema is {PACK_SCHEMA}")
        if p.get("id") != pid:
            out.append(f"{pid}: pack id {p.get('id')} doesn't match its folder")
        if p.get("status") not in PACK_STATUSES:
            out.append(f"{pid}: pack status is one of {', '.join(PACK_STATUSES)}")
        name = p.get("certification")
        if not isinstance(name, str) or "/" in name or "\\" in name or ".." in name or not (folder / name).is_file():
            out.append(f"{pid}: certification names a file in the pack's folder")
            continue
        try:
            cert = _read_json(folder / name)
        except (OSError, ValueError) as exc:
            out.append(f"{pid}: {name} can't be read ({exc})")
            continue
        out += _cert_problems(pid, cert)
    return out
