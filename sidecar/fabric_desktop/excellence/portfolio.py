"""Capacity scores across the workspaces on a capacity (FEA-503). Engine only: nothing on screen yet.

:func:`capacity_score` scores one capacity from the **control outcomes** that bear on it, pooled and run
through the same formulas as any assessment (:func:`.scoring.score`). It never averages workspace
scores: an average would let a workspace with little evidence count as much as one fully checked, and
would weigh a control twice when two workspaces share it.

The outcomes that bear on a capacity (:func:`relevant`):

- every capacity control on the capacity itself (scope ``capacity``);
- controls in :data:`DIMENSIONS` (capacity and FinOps, performance) on the workspaces given as on the
  capacity, and on their items.

The caller says which workspaces are on the capacity; snapshots don't record it reliably, so it isn't
guessed. For each workspace the latest snapshot that assessed it is used, and for the capacity the
latest that assessed the capacity, so one run is never counted twice.

Coverage carries the gaps. A workspace on the capacity that no snapshot assessed, or a capacity no
snapshot assessed, gets the outcome the engine gives a resource it couldn't read: *not evaluated*,
with the reason, for each relevant control the registry has. So a capacity with little evidence has
low coverage and no rating, never a healthy score. A snapshot that assessed the resource but holds
no outcome of a relevant control for it is a gap the same way. These are gaps, not findings: nothing here makes a
finding or a saving; the findings listed are the snapshots' own.

Snapshots made with a different profile or rule versions than the registry's are refused, as
:func:`.assess.rescore` refuses them, rather than mixing versions in one score.
"""

from __future__ import annotations

import math
from typing import Any

from . import controls as ct
from . import findings as fx
from . import profiles as pf
from . import scoring
from .assess import SCHEMA
from .evidence import canonical
from .trends import _when

DIMENSIONS = ("capacity_finops", "performance")
MAX_SNAPSHOTS = 500
MAX_WORKSPACES = 1000
NOT_ASSESSED = "This workspace on the capacity wasn't assessed."
CAPACITY_NOT_ASSESSED = "The capacity itself wasn't assessed."
NO_OUTCOME = "The snapshot that assessed this resource holds no outcome of this control for it."
METHOD = ("Pooled outcomes of capacity-relevant controls, scored with the assessment's formulas; "
          "not an average of workspace scores.")


def relevant(o: dict[str, Any], capacity_id: str, workspace_ids: set[str],
             dimensions: tuple[str, ...] = DIMENSIONS) -> bool:
    s = o["subject"]
    if s.get("type") == "capacity":
        return s.get("id") == capacity_id
    ws = s.get("id") if s.get("type") == "workspace" else s.get("workspace_id")
    return ws in workspace_ids and o.get("dimension") in dimensions


def _finite_numbers(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite_numbers(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite_numbers(v) for v in value)
    return True


def _problems(snap: Any) -> list[str]:
    if not isinstance(snap, dict):
        return ["isn't an object"]
    out: list[str] = []
    if snap.get("schema") != SCHEMA:
        out.append(f"schema isn't {SCHEMA}")
    if _when(snap.get("assessed_at")) is None:
        out.append("assessed_at isn't an ISO timestamp with a time zone")
    if not isinstance(snap.get("profile"), dict) or not _finite_numbers(snap["profile"]):
        out.append("profile isn't an object of finite numbers")
    if not isinstance(snap.get("rule_versions"), dict):
        out.append("no rule_versions")
    scope = snap.get("scope")
    if not isinstance(scope, dict) or any(
            not isinstance(scope.get(k, []), list)
            or any(not isinstance(s, dict) or not isinstance(s.get("id"), str) for s in scope.get(k, []))
            for k in ("workspaces", "capacities")):
        out.append("scope isn't {workspaces: [{id}], capacities: [{id}]}")
    outs = snap.get("outcomes")
    if not isinstance(outs, list) or any(
            not isinstance(o, dict) or not isinstance(o.get("subject"), dict) or not isinstance(o["subject"].get("id"), str)
            or not isinstance(o.get("control_id"), str) or o.get("outcome") not in ct.OUTCOMES
            or o.get("severity") not in ct.SEVERITIES or o.get("dimension") not in ct.DIMENSIONS for o in outs):
        out.append("outcomes are malformed")
    found = snap.get("findings", [])
    if not isinstance(found, list) or any(
            not isinstance(f, dict) or not isinstance(f.get("id"), str) or not isinstance(f.get("advisory"), bool)
            or f.get("severity") not in ct.SEVERITIES or f.get("confidence") not in fx.CONFIDENCE_LABELS
            or f.get("effort") not in ct.LEVELS or f.get("risk") not in ct.LEVELS or not isinstance(f.get("rule"), str)
            for f in found):
        out.append("findings are malformed")
    return out


def _latest(snaps: list[dict[str, Any]], key: str, sid: str) -> dict[str, Any] | None:
    mine = [s for s in snaps if any(x["id"] == sid for x in s["scope"].get(key, []))]
    # canonical text breaks a tie of time and fingerprint, so the input order never picks the snapshot
    return max(mine, key=lambda s: (_when(s["assessed_at"]), str(s.get("fingerprint") or ""), canonical(s))) \
        if mine else None


def _gap(c: ct.Control, subj: dict[str, Any], profile: pf.Profile, text: str) -> dict[str, Any]:
    if c.id in profile.disabled:
        o = ct._outcome(c, subj, ct.not_applicable(f"Turned off in the {profile.title} profile: {profile.disabled[c.id]}"),
                        "insufficient")
    else:
        o = ct._outcome(c, subj, ct.not_evaluated(text, needs="an assessment of this resource"), "insufficient")
    o["severity"] = profile.severity_overrides.get(c.id, c.severity)
    o["gap"] = True
    return o


def _findings(snap: dict[str, Any], outs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The snapshot's own findings for the failed outcomes taken from it; none is made here."""
    failing = {fx.finding_id(o["control_id"], o["subject"]["id"]) for o in outs
               if o["outcome"] == "fail" or (o.get("advisory") or {}).get("outcome") == "fail"}
    return [f for f in snap.get("findings") or [] if f["id"] in failing]


def capacity_score(snapshots: list[dict[str, Any]], registry: ct.Registry, *, capacity_id: str,
                   workspace_ids: list[str], profile: pf.Profile | None = None,
                   dimensions: tuple[str, ...] = DIMENSIONS) -> dict[str, Any]:
    """The score of ``capacity_id`` from the snapshots (assess.assess results) of it and of the
    workspaces on it (``workspace_ids``, which the caller knows from the capacity assignment)."""
    if not isinstance(capacity_id, str) or not capacity_id:
        raise ValueError("capacity_id is a non-empty string")
    if (not isinstance(workspace_ids, (list, tuple)) or any(not isinstance(w, str) or not w for w in workspace_ids)
            or len(set(workspace_ids)) != len(workspace_ids)):
        raise ValueError("workspace_ids is a list of distinct non-empty strings")
    if len(workspace_ids) > MAX_WORKSPACES:
        raise ValueError(f"at most {MAX_WORKSPACES} workspaces on one capacity, got {len(workspace_ids)}")
    if not isinstance(snapshots, (list, tuple)):
        raise ValueError("snapshots are a list")
    if len(snapshots) > MAX_SNAPSHOTS:
        raise ValueError(f"at most {MAX_SNAPSHOTS} snapshots, got {len(snapshots)}")
    if any(d not in ct.DIMENSIONS for d in dimensions):
        raise ValueError("dimensions names an unknown dimension")
    bad = [f"snapshot {i}: {p}" for i, s in enumerate(snapshots) for p in _problems(s)]
    if bad:
        raise ValueError("; ".join(bad))
    now = registry.versions()
    for i, s in enumerate(snapshots):
        changed = sorted(k for k in set(s["rule_versions"]) | set(now) if s["rule_versions"].get(k) != now.get(k))
        if changed:
            raise ValueError(f"snapshot {i}: rules differ from the registry's: " + ", ".join(changed))
    if profile is None:
        profile = pf.from_dict(snapshots[0]["profile"]) if snapshots else pf.get()
    profile = pf.checked(profile)
    for i, s in enumerate(snapshots):
        if s["profile"] != profile.as_dict():
            raise ValueError(f"snapshot {i} used another profile ({s['profile'].get('id')})")

    ws_set = set(workspace_ids)
    outcomes: list[dict[str, Any]] = []
    used: dict[str, dict[str, Any]] = {}
    sources: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    workspaces = []
    for wid in sorted(ws_set):
        snap = _latest(list(snapshots), "workspaces", wid)
        if snap is None:
            subj = {"type": "workspace", "id": wid, "name": wid}
            outcomes += [_gap(c, subj, profile, NOT_ASSESSED) for c in registry.controls()
                         if c.scope in ("workspace", "item") and c.dimension in dimensions]
            workspaces.append({"id": wid, "assessed_at": None, "state": "not_assessed"})
            continue
        name = next((x.get("name") for x in snap["scope"]["workspaces"] if x["id"] == wid), wid)
        workspaces.append({"id": wid, "name": name, "assessed_at": snap["assessed_at"], "state": "assessed"})
        used[str(snap.get("fingerprint") or snap["assessed_at"])] = snap
        mine = [o for o in snap["outcomes"] if o["subject"].get("type") != "capacity"
                and relevant(o, capacity_id, {wid}, dimensions)]
        sources.append((snap, mine))
        outcomes += mine
        held = {o["control_id"] for o in mine}
        outcomes += [_gap(c, {"type": "workspace", "id": wid, "name": name}, profile, NO_OUTCOME)
                     for c in registry.controls() if c.scope in ("workspace", "item") and c.dimension in dimensions
                     and c.id not in held]
    cap_snap = _latest(list(snapshots), "capacities", capacity_id)
    if cap_snap is None:
        subj = {"type": "capacity", "id": capacity_id, "name": capacity_id}
        outcomes += [_gap(c, subj, profile, CAPACITY_NOT_ASSESSED) for c in registry.controls() if c.scope == "capacity"]
    else:
        used[str(cap_snap.get("fingerprint") or cap_snap["assessed_at"])] = cap_snap
        mine = [o for o in cap_snap["outcomes"] if o["subject"].get("type") == "capacity"
                and o["subject"].get("id") == capacity_id]
        sources.append((cap_snap, mine))
        outcomes += mine
        held = {o["control_id"] for o in mine}
        outcomes += [_gap(c, {"type": "capacity", "id": capacity_id, "name": capacity_id}, profile, NO_OUTCOME)
                     for c in registry.controls() if c.scope == "capacity" and c.id not in held]

    findings = fx.rank([f for snap, outs in sources for f in _findings(snap, outs)])
    times = sorted(_when(s["assessed_at"]) for s in used.values())
    scores = scoring.score(outcomes, profile)
    assessed = sum(1 for w in workspaces if w["state"] == "assessed")
    return {"schema": "kratosforge/excellence-capacity-score@1", "capacity": {"id": capacity_id},
            "method": METHOD, "dimensions": list(dimensions), "profile": profile.as_dict(),
            "rule_versions": now, "scores": scores,
            "evidence": {"workspaces_listed": len(workspaces), "workspaces_assessed": assessed,
                         "capacity_assessed": cap_snap is not None,
                         "gaps": sum(1 for o in outcomes if o.get("gap")),
                         "oldest": times[0].isoformat() if times else None,
                         "newest": times[-1].isoformat() if times else None},
            "workspaces": workspaces, "outcomes": outcomes, "findings": findings}
