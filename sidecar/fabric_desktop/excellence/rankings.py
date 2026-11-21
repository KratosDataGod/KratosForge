"""Workspace rankings by assured score within workload categories (FEA-502).

``rank(snapshots)`` takes assessment snapshots (assess.assess results) and ranks workspaces against
comparable workspaces only. Pure: nothing here reads Fabric, stores anything or draws anything.

Rules:

- **One snapshot per workspace**: the latest by ``assessed_at`` among the snapshots that cover it. Two
  covering it at the same instant: the one with the greater fingerprint, then policy version, so the
  pick never depends on input order. Older ones are counted under ``superseded``.
- **Comparable policy only**: workspaces whose snapshots used a different profile (id, version or
  settings) or different rule versions are never ranked together. Each policy version is its own
  group, and so is a demonstration fixture. ``comparable`` is true only when there is one group.
- **Workload category**: the caller's ``category_of`` when it names one, otherwise derived from the
  workspace's item mix in its snapshot (``item_coverage``). Each item type counts toward one group
  (TYPE_GROUPS); types in no group (a SQL analytics endpoint, which comes with a lakehouse, or a type
  this table doesn't know) are left out. The category is the group holding more than half of the
  counted items; with no such majority it is ``mixed``; with no counted items, ``uncategorized``.
- **Assured score only**: ranks come from the assured score, so missing evidence never lifts a
  workspace. Equal assured scores share a rank (1, 1, 3) and are marked ``tied``; within a tie the
  order is higher coverage, then name, then id.
- **Coverage gates**: a workspace whose status is ``insufficient_evidence`` (coverage under the
  profile's provisional threshold, 50% by default) is listed under ``insufficient_evidence`` and never
  ranked. ``provisional`` ones are ranked and flagged. A status the coverage doesn't support is taken
  down to the one it does, never up.
- **No averages**: there is no category or group score; each row is one workspace's own result.

Input is checked, and a malformed snapshot is refused with ValueError naming it: wrong schema, a
number that is NaN, infinite or out of 0..100, a policy version that doesn't match its profile and
rules, more than MAX_SNAPSHOTS snapshots or MAX_WORKSPACES workspaces. Names lose control and
formatting characters and are cut to MAX_NAME characters.
"""

from __future__ import annotations

import hashlib
import math
import unicodedata
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from . import profiles as pf
from .evidence import canonical

SCHEMA = "kratosforge/excellence-rankings@1"
SNAPSHOT_SCHEMA = "kratosforge/excellence-assessment@1"  # assess.SCHEMA
MAX_SNAPSHOTS = 1000
MAX_WORKSPACES = 1000
MAX_NAME = 256
MAX_ITEMS = 100_000

CATEGORIES = {"data_engineering": "Data engineering", "warehousing": "Warehousing",
              "business_intelligence": "Business intelligence", "real_time": "Real-time intelligence",
              "mixed": "Mixed", "uncategorized": "Uncategorized"}
TYPE_GROUPS = {
    "data_engineering": ("Lakehouse", "Notebook", "DataPipeline", "SparkJobDefinition", "Environment", "Dataflow",
                         "CopyJob", "ApacheAirflowJob", "MLModel", "MLExperiment"),
    "warehousing": ("Warehouse", "MirroredDatabase", "MirroredWarehouse", "MirroredAzureDatabricksCatalog",
                    "SQLDatabase", "Datamart"),
    "business_intelligence": ("SemanticModel", "Report", "Dashboard", "PaginatedReport", "Scorecard"),
    "real_time": ("Eventhouse", "KQLDatabase", "KQLQueryset", "Eventstream", "KQLDashboard", "Reflex",
                  "Activator"),
}
_GROUP_OF = {t: g for g, ts in TYPE_GROUPS.items() for t in ts}
_STATUS = ("insufficient_evidence", "provisional", "rated")
_TOLERANCE = 0.05  # coverage is stored rounded to 0.1


def clean(value: Any, limit: int = MAX_NAME) -> str:
    """Text without control or formatting characters (bidi overrides, zero-width), cut to ``limit``."""
    s = "".join(ch for ch in str(value) if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co"))
    return s.strip()[:limit]


def _number(v: Any, where: str, *, nullable: bool = True) -> float | None:
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{where} is not a finite number")
    if not 0 <= v <= 100:
        raise ValueError(f"{where} is outside 0..100")
    return float(v)


def _finite(value: Any, where: str) -> None:
    """Every number in ``value`` is finite."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{where} holds a number that is NaN or infinite")
    if isinstance(value, dict):
        for v in value.values():
            _finite(v, where)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _finite(v, where)


def _when(v: Any, where: str) -> datetime:
    if not isinstance(v, str) or len(v) > 64:
        raise ValueError(f"{where}: assessed_at is not a timestamp")
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{where}: assessed_at is not a timestamp") from None
    if t.tzinfo is None:
        raise ValueError(f"{where}: assessed_at has no time zone")
    return t.astimezone(timezone.utc)


def category_from_items(item_types: list[str]) -> dict[str, Any]:
    """The workload category of a workspace holding items of ``item_types``, with the counts behind it."""
    counts = {g: 0 for g in TYPE_GROUPS}
    for t in item_types:
        g = _GROUP_OF.get(t)
        if g:
            counts[g] += 1
    total = sum(counts.values())
    if not total:
        cat = "uncategorized"
    else:
        top = [g for g, n in counts.items() if 2 * n > total]
        cat = top[0] if top else "mixed"
    return {"category": cat, "item_counts": counts, "items_counted": total}


def _status(stored: Any, coverage: float | None, assured: float | None, profile: pf.Profile, where: str) -> str:
    if stored not in _STATUS:
        raise ValueError(f"{where}: unknown status {stored!r}")
    if assured is None or coverage is None:
        return "insufficient_evidence"
    if coverage + _TOLERANCE < 100 * profile.provisional_coverage:
        supported = "insufficient_evidence"
    elif coverage + _TOLERANCE < 100 * profile.rated_coverage:
        supported = "provisional"
    else:
        supported = "rated"
    return _STATUS[min(_STATUS.index(stored), _STATUS.index(supported))]


def _checked(snap: Any, i: int) -> dict[str, Any]:
    where = f"snapshot {i}"
    if not isinstance(snap, dict):
        raise ValueError(f"{where} is not an object")
    if snap.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError(f"{where} is not an excellence assessment snapshot")
    when = _when(snap.get("assessed_at"), where)
    prof, rules, pv = snap.get("profile"), snap.get("rule_versions"), snap.get("policy_version")
    if not isinstance(prof, dict) or not isinstance(rules, dict) or not isinstance(pv, str):
        raise ValueError(f"{where}: profile, rule versions or policy version missing")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in rules.items()):
        raise ValueError(f"{where}: rule versions are malformed")
    _finite(prof, where + ": profile")
    try:
        profile = pf.from_dict(prof)
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise ValueError(f"{where}: profile is malformed ({type(e).__name__})") from None
    if hashlib.sha256(canonical([prof, rules]).encode()).hexdigest()[:16] != pv:
        raise ValueError(f"{where}: policy version doesn't match its profile and rules")
    scope, scores = snap.get("scope"), snap.get("workspace_scores")
    wss = scope.get("workspaces") if isinstance(scope, dict) else None
    if not isinstance(wss, list) or not isinstance(scores, dict):
        raise ValueError(f"{where}: workspaces or their scores missing")
    if len(wss) > MAX_WORKSPACES:
        raise ValueError(f"{where}: at most {MAX_WORKSPACES} workspaces")
    cov = snap.get("item_coverage") or []
    if not isinstance(cov, list) or len(cov) > MAX_ITEMS:
        raise ValueError(f"{where}: item coverage is malformed")
    types: dict[str, list[str]] = {}
    for c in cov:
        it = c.get("item") if isinstance(c, dict) else None
        if not isinstance(it, dict) or not isinstance(it.get("workspace_id"), str) or not isinstance(it.get("item_type"), str):
            raise ValueError(f"{where}: an item coverage entry is malformed")
        types.setdefault(it["workspace_id"], []).append(it["item_type"])
    fp = snap.get("fingerprint")
    out = []
    for ws in wss:
        wid = ws.get("id") if isinstance(ws, dict) else None
        if not isinstance(wid, str) or not wid or len(wid) > MAX_NAME or clean(wid) != wid:
            raise ValueError(f"{where}: a workspace id is malformed")
        block = scores.get(wid)
        if not isinstance(block, dict):
            raise ValueError(f"{where}: workspace {wid} has no score")
        assured = _number(block.get("assured"), f"{where}: assured score of {wid}")
        coverage = _number(block.get("coverage"), f"{where}: coverage of {wid}")
        score = _number(block.get("score"), f"{where}: score of {wid}")
        status = _status(block.get("status"), coverage, assured, profile, f"{where}: workspace {wid}")
        rating = block.get("rating")
        out.append({"id": wid, "name": clean(ws.get("name") or wid) or wid, "assured": assured, "coverage": coverage,
                    "score": score, "status": status,
                    "rating": (clean(rating.get("label", ""), 64) or None) if isinstance(rating, dict) and status != "insufficient_evidence" else None,
                    "item_types": sorted(types.get(wid, [])), "when": when, "assessed_at": snap["assessed_at"],
                    "fingerprint": clean(fp, 64) if isinstance(fp, str) else "", "policy_version": pv,
                    "fixture": bool(snap.get("fixture")), "profile": profile})
    return {"rows": out}


def _caller_category(category_of: Any, ws: dict[str, Any]) -> str | None:
    if category_of is None:
        return None
    if isinstance(category_of, Mapping):
        got = category_of.get(ws["id"])
    elif callable(category_of):
        got = category_of({"id": ws["id"], "name": ws["name"]})
    else:
        raise ValueError("category_of is a mapping or a function")
    if got is None:
        return None
    if not isinstance(got, str) or not clean(got, 64):
        raise ValueError(f"category for workspace {ws['id']} is not a name")
    return clean(got, 64)


def _ranked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(rows, key=lambda r: (-r["assured"], -r["coverage"], r["name"].casefold(), r["name"], r["id"]))
    out = []
    for n, r in enumerate(rows):
        rank = out[-1]["rank"] if out and out[-1]["assured"] == r["assured"] else n + 1
        out.append({"rank": rank, **r})
    for r in out:
        r["tied"] = sum(1 for x in out if x["rank"] == r["rank"]) > 1
    return out


def _row(r: dict[str, Any]) -> dict[str, Any]:
    return {"workspace": {"id": r["id"], "name": r["name"]}, "assured": r["assured"], "coverage": r["coverage"],
            "score": r["score"], "status": r["status"], "provisional": r["status"] == "provisional",
            "rating": r["rating"], "assessed_at": r["assessed_at"], "snapshot": r["fingerprint"],
            "category_source": r["category_source"], "item_counts": r["item_counts"]}


def rank(snapshots: list[dict[str, Any]], *,
         category_of: Mapping[str, str] | Callable[[dict[str, str]], str | None] | None = None) -> dict[str, Any]:
    """Workspaces ranked by assured score within each policy group and workload category.

    ``category_of`` maps a workspace id to a category name, or is a function given ``{"id", "name"}``;
    a missing or None answer falls back to the derived category."""
    if not isinstance(snapshots, (list, tuple)):
        raise ValueError("snapshots is a list")
    if len(snapshots) > MAX_SNAPSHOTS:
        raise ValueError(f"at most {MAX_SNAPSHOTS} snapshots")
    latest: dict[str, dict[str, Any]] = {}
    seen = 0
    for i, s in enumerate(snapshots):
        for r in _checked(s, i)["rows"]:
            seen += 1
            key = (r["when"], r["fingerprint"], r["policy_version"], r["fixture"],
                   -1.0 if r["assured"] is None else r["assured"], -1.0 if r["coverage"] is None else r["coverage"])
            cur = latest.get(r["id"])
            if cur is None or key > cur["_key"]:
                latest[r["id"]] = {**r, "_key": key}
            if len(latest) > MAX_WORKSPACES:
                raise ValueError(f"at most {MAX_WORKSPACES} workspaces")
    groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for r in latest.values():
        derived = category_from_items(r["item_types"])
        mine = _caller_category(category_of, r)
        r.update(category=mine or derived["category"], category_source="caller" if mine else "derived",
                 item_counts=derived["item_counts"])
        groups.setdefault((r["policy_version"], r["fixture"]), []).append(r)
    order = list(CATEGORIES)
    out_groups = []
    for (pv, fixture), rows in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        p = rows[0]["profile"]
        cats = []
        names = sorted({r["category"] for r in rows},
                       key=lambda c: (order.index(c) if c in order else order.index("mixed") - 0.5, c.casefold(), c))
        for c in names:
            mine = [r for r in rows if r["category"] == c]
            ranked = _ranked([r for r in mine if r["status"] != "insufficient_evidence"])
            low = sorted((r for r in mine if r["status"] == "insufficient_evidence"),
                         key=lambda r: (r["name"].casefold(), r["name"], r["id"]))
            cats.append({"category": c, "label": CATEGORIES.get(c, c),
                         "ranked": [{"rank": r["rank"], "tied": r["tied"], **_row(r)} for r in ranked],
                         "insufficient_evidence": [_row(r) for r in low]})
        out_groups.append({"policy_version": pv, "fixture": fixture,
                           "profile": {"id": p.id, "version": p.version, "title": clean(p.title),
                                       "provisional_coverage": p.provisional_coverage,
                                       "rated_coverage": p.rated_coverage},
                           "workspaces": len(rows), "categories": cats})
    return {"schema": SCHEMA, "basis": "assured", "comparable": len(out_groups) <= 1,
            "groups": out_groups, "workspaces": len(latest), "superseded": seen - len(latest),
            "note": ("Ranked by assured score within each workload category, among workspaces assessed with the "
                     "same profile and rules. Workspaces under the coverage threshold are listed, not ranked. "
                     "No score is averaged across workspaces.")}
