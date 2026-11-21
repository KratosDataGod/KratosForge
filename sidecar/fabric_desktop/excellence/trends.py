"""Trends and regressions across assessment snapshots (FEA-603). Engine only: nothing on screen yet.

:func:`trend` takes the snapshots :func:`.assess.assess` returns (the ``excellence_assess`` result) for one
scope, in any order, and gives, step by step from the oldest to the newest:

- each control's transition on each resource (key: control id and subject id), classified against the
  last *decided* outcome (pass or fail) in the earlier snapshots:

  ``new_failure``          fails now, never decided before
  ``regressed``            fails now, last decided outcome was a pass
  ``recurring``            fails now, last decided outcome was a fail
  ``resolved``             passes now, last decided outcome was a fail
  ``newly_passing``        passes now, never decided before
  ``newly_not_evaluated``  not evaluated (or error) now, and wasn't in the previous snapshot
  ``no_longer_applicable`` not applicable now, applicable in the previous snapshot
  ``no_longer_assessed``   in the previous snapshot, not in this one (resource or control gone)

  A fail that is now not evaluated, not applicable or no longer assessed is **never** resolved: missing
  evidence can't look like a fix. It stays in ``unverified_failures`` until a pass is seen.
- the overall and dimension score, assured score and coverage deltas;
- whether the score moved only because coverage did: no control decided in both snapshots changed its
  result, but some were decided in only one of them. The assured score is the number to compare then;
- whether the two snapshots are comparable: same schema, profile, rule versions, scope and fixture flag.
  A step that isn't still gets its transitions, flagged with the reasons, because the change may come
  from the policy or the scope, not the environment.

Inputs are checked before anything is worked out: a malformed snapshot, a non-finite score, two
snapshots with the same timestamp or more than :data:`MAX_SNAPSHOTS` are refused with ``ValueError``.
The output depends only on the snapshots' content, never on the order they were given in or the clock.
Nothing here reads Fabric or writes anything, and nothing here makes a finding or a saving.
"""

from __future__ import annotations

import datetime as dt
import math
from itertools import pairwise
from typing import Any

from .assess import SCHEMA
from .controls import DIMENSIONS, OUTCOMES

MAX_SNAPSHOTS = 100
DECIDED = ("pass", "fail")
UNKNOWN = ("not_evaluated", "error")
TRANSITIONS = ("new_failure", "regressed", "recurring", "resolved", "newly_passing", "newly_not_evaluated",
               "no_longer_applicable", "no_longer_assessed")
NOTES = {
    "coverage_only": "The score moved only because a different set of controls could be checked; no control "
                     "checked both times changed its result. Compare the assured score.",
    "score_up_coverage_down": "The score rose while coverage fell: fewer controls were checked. Compare the "
                              "assured score.",
    "unverified_failures": "Some controls that failed before couldn't be checked this time. They aren't resolved.",
    "not_comparable": "These snapshots used a different profile, rules or scope, so a change may come from "
                      "the policy or the scope, not the environment.",
}


# ---- input checks

def _finite(x: Any) -> bool:
    return x is None or (isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x))


def _when(text: Any) -> dt.datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        t = dt.datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def _block_problems(block: Any, where: str) -> list[str]:
    if not isinstance(block, dict):
        return [f"{where} isn't an object"]
    return [f"{where}.{k} isn't a finite number or null" for k in ("score", "assured", "coverage")
            if not _finite(block.get(k))]


def problems(snap: Any) -> list[str]:
    """Everything that keeps ``snap`` out of a trend; empty when it can be used."""
    if not isinstance(snap, dict):
        return ["isn't an object"]
    out: list[str] = []
    if snap.get("schema") != SCHEMA:
        out.append(f"schema isn't {SCHEMA}")
    if _when(snap.get("assessed_at")) is None:
        out.append("assessed_at isn't an ISO timestamp with a time zone")
    if not isinstance(snap.get("policy_version"), str) or not snap["policy_version"]:
        out.append("no policy_version")
    rv = snap.get("rule_versions")
    if not isinstance(rv, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in rv.items()):
        out.append("rule_versions isn't a map of control id to version")
    prof = snap.get("profile")
    if not isinstance(prof, dict) or not isinstance(prof.get("id"), str) or not isinstance(prof.get("version"), str):
        out.append("profile has no id and version")
    scope = snap.get("scope")
    if not isinstance(scope, dict) or any(not isinstance(scope.get(k, []), list) for k in ("workspaces", "capacities")):
        out.append("scope isn't {workspaces: [], capacities: []}")
    else:
        for k in ("workspaces", "capacities"):
            if any(not isinstance(s, dict) or not isinstance(s.get("id"), str) for s in scope.get(k, [])):
                out.append(f"scope.{k} has an entry without an id")
    scores = snap.get("scores")
    if not isinstance(scores, dict):
        out.append("no scores")
    else:
        out += _block_problems(scores.get("overall"), "scores.overall")
        dims = scores.get("dimensions")
        if not isinstance(dims, dict):
            out.append("scores.dimensions isn't an object")
        else:
            for d, x in sorted(dims.items(), key=lambda kv: str(kv[0])):
                out += _block_problems(x, f"scores.dimensions.{d}")
    outs = snap.get("outcomes")
    if not isinstance(outs, list):
        out.append("outcomes isn't a list")
    else:
        seen: set[tuple[str, str]] = set()
        for i, o in enumerate(outs):
            if (not isinstance(o, dict) or not isinstance(o.get("control_id"), str) or not o["control_id"]
                    or not isinstance(o.get("subject"), dict) or not isinstance(o["subject"].get("id"), str)
                    or not o["subject"]["id"] or o.get("outcome") not in OUTCOMES):
                out.append(f"outcome {i} is malformed")
                continue
            key = (o["control_id"], o["subject"]["id"])
            if key in seen:
                out.append(f"outcome {i} repeats {key[0]} on {key[1]}")
            seen.add(key)
    return out


def _checked(snapshots: Any) -> list[dict[str, Any]]:
    if not isinstance(snapshots, (list, tuple)):
        raise ValueError("snapshots are a list")
    if len(snapshots) > MAX_SNAPSHOTS:
        raise ValueError(f"at most {MAX_SNAPSHOTS} snapshots in one trend, got {len(snapshots)}")
    bad = [f"snapshot {i}: {p}" for i, s in enumerate(snapshots) for p in problems(s)]
    if bad:
        raise ValueError("; ".join(bad))
    ordered = sorted(snapshots, key=lambda s: _when(s["assessed_at"]))
    for a, b in pairwise(ordered):
        if _when(a["assessed_at"]) == _when(b["assessed_at"]):
            raise ValueError(f"two snapshots are both at {b['assessed_at']}; can't tell which came first")
    return ordered


# ---- comparisons

def _scope_ids(snap: dict[str, Any]) -> dict[str, list[str]]:
    return {k: sorted(s["id"] for s in snap["scope"].get(k, [])) for k in ("workspaces", "capacities")}


def comparability(prev: dict[str, Any], cur: dict[str, Any]) -> dict[str, Any]:
    """Whether a change between ``prev`` and ``cur`` can be read as a change in the environment."""
    reasons: list[str] = []
    if prev["profile"]["id"] != cur["profile"]["id"] or prev["profile"]["version"] != cur["profile"]["version"]:
        reasons.append("profile")
    elif prev["policy_version"] != cur["policy_version"] and prev["rule_versions"] == cur["rule_versions"]:
        reasons.append("profile")  # same id and version, different settings
    a, b = prev["rule_versions"], cur["rule_versions"]
    changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    if changed:
        reasons.append("rules")
    if _scope_ids(prev) != _scope_ids(cur):
        reasons.append("scope")
    if bool(prev.get("fixture")) != bool(cur.get("fixture")):
        reasons.append("fixture")
    return {"comparable": not reasons, "reasons": reasons, "rules_changed": changed}


def _delta(a: Any, b: Any) -> float | None:
    return None if a is None or b is None else round(b - a + 0.0, 1)


def _deltas(prev: dict[str, Any], cur: dict[str, Any]) -> dict[str, Any]:
    def block(p: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
        return {k: {"from": p.get(k), "to": c.get(k), "delta": _delta(p.get(k), c.get(k))}
                for k in ("score", "assured", "coverage")}

    pd, cd = prev["scores"]["dimensions"], cur["scores"]["dimensions"]
    dims = {d: block(pd.get(d) or {}, cd.get(d) or {}) for d in DIMENSIONS if d in pd or d in cd}
    return {"overall": block(prev["scores"]["overall"], cur["scores"]["overall"]), "dimensions": dims}


def _by_key(snap: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(o["control_id"], o["subject"]["id"]): o for o in snap["outcomes"]}


def _classify(prev: str | None, cur: str | None, last_decided: str | None) -> str | None:
    if cur == "fail":
        return {"fail": "recurring", "pass": "regressed"}.get(last_decided or "", "new_failure")
    if cur == "pass":
        if last_decided == "fail":
            return "resolved"
        return "newly_passing" if last_decided is None else None
    if cur in UNKNOWN:
        return None if prev in UNKNOWN else "newly_not_evaluated"
    if cur == "not_applicable":
        return None if prev in (None, "not_applicable") else "no_longer_applicable"
    return "no_longer_assessed" if prev is not None else None  # cur is None: gone


def _subject(o: dict[str, Any]) -> dict[str, Any]:
    s = o["subject"]
    return {k: s[k] for k in ("type", "id", "name", "workspace_id", "item_type") if k in s}


def _step(prev: dict[str, Any], cur: dict[str, Any], last: dict[tuple[str, str], str]) -> dict[str, Any]:
    p, c = _by_key(prev), _by_key(cur)
    transitions: list[dict[str, Any]] = []
    outcome_changes = coverage_changes = 0
    for key in sorted(set(p) | set(c)):
        po, co = p.get(key), c.get(key)
        pv, cv = (po or {}).get("outcome"), (co or {}).get("outcome")
        if pv in DECIDED and cv in DECIDED:
            outcome_changes += pv != cv
        elif (pv in DECIDED) != (cv in DECIDED):
            coverage_changes += 1
        kind = _classify(pv, cv, last.get(key))
        if kind:
            ref = co or po
            transitions.append({
                "kind": kind, "control_id": key[0], "subject": _subject(ref), "from": pv, "to": cv,
                "last_decided": last.get(key), "dimension": ref.get("dimension"), "severity": ref.get("severity"),
                "rule_from": (po or {}).get("rule"), "rule_to": (co or {}).get("rule"),
                "rule_changed": bool(po and co and po.get("rule") != co.get("rule")),
                "still_failing_unverified": last.get(key) == "fail" and cv not in DECIDED})
        if cv in DECIDED:
            last[key] = cv
    comp = comparability(prev, cur)
    deltas = _deltas(prev, cur)
    o = deltas["overall"]
    notes: list[str] = []
    score_moved = o["score"]["from"] != o["score"]["to"]
    if score_moved and outcome_changes == 0 and coverage_changes > 0:
        notes.append("coverage_only")
    if (o["score"]["delta"] or 0) > 0 and (o["coverage"]["delta"] or 0) < 0:
        notes.append("score_up_coverage_down")
    if any(t["still_failing_unverified"] for t in transitions):
        notes.append("unverified_failures")
    if not comp["comparable"]:
        notes.append("not_comparable")
    counts = {k: sum(1 for t in transitions if t["kind"] == k) for k in TRANSITIONS}
    return {"from": prev["assessed_at"], "to": cur["assessed_at"], **comp, **deltas,
            "score_change_from_coverage_only": "coverage_only" in notes,
            "outcome_changes": outcome_changes, "coverage_changes": coverage_changes,
            "transitions": transitions, "counts": counts,
            "notes": [{"id": n, "text": NOTES[n]} for n in notes]}


def trend(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """The trend across ``snapshots`` (assess.assess results for one scope), oldest first."""
    ordered = _checked(snapshots)
    last: dict[tuple[str, str], str] = {}
    if ordered:
        last = {k: o["outcome"] for k, o in _by_key(ordered[0]).items() if o["outcome"] in DECIDED}
    steps = [_step(a, b, last) for a, b in pairwise(ordered)]
    latest = _by_key(ordered[-1]) if ordered else {}
    unverified = [{"control_id": k[0], "subject": _subject(o), "now": o["outcome"]}
                  for k, o in sorted(latest.items()) if last.get(k) == "fail" and o["outcome"] not in DECIDED]
    # a failing control that has left the latest snapshot altogether is just as unverified
    gone = sorted(k for k, v in last.items() if v == "fail" and k not in latest)
    unverified += [{"control_id": k[0], "subject": {"id": k[1]}, "now": None} for k in gone]
    totals = {k: sum(s["counts"][k] for s in steps) for k in TRANSITIONS}
    overall = None
    if len(ordered) >= 2:
        span = _deltas(ordered[0], ordered[-1])
        overall = {"from": ordered[0]["assessed_at"], "to": ordered[-1]["assessed_at"], **span,
                   "comparable": all(s["comparable"] for s in steps)}
    return {"schema": "kratosforge/excellence-trend@1", "count": len(ordered),
            "snapshots": [{"assessed_at": s["assessed_at"], "fingerprint": s.get("fingerprint"),
                           "policy_version": s["policy_version"], "profile": s["profile"]["id"],
                           "overall": {k: s["scores"]["overall"].get(k) for k in ("score", "assured", "coverage")}}
                          for s in ordered],
            "comparable": all(s["comparable"] for s in steps), "steps": steps, "totals": totals,
            "span": overall, "unverified_failures": unverified,
            "regressions": [t | {"at": s["to"]} for s in steps for t in s["transitions"] if t["kind"] == "regressed"]}
