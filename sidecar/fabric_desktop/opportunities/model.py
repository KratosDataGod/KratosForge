"""The opportunity record every analyzer produces (brief section 10).

An opportunity is a plain dict so any view, RPC or table can carry it. :func:`make` fills every
field the brief requires and refuses one without evidence: a finding nobody can check is not an
opportunity.

Actions follow the order the brief asks every workload to be questioned in (section 1): first
whether it needs to exist, then whether it's done elsewhere, then whether it can be done better,
then where it should run, then capacity. :data:`ACTION_ORDER` is that order, and it decides which
opportunity counts when two touch the same workload.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# What kind of waste was found.
CATEGORIES = {
    "duplicate_processing": "Duplicate processing",
    "redundant_data_movement": "Redundant data movement",
    "full_refresh": "Full refresh that could be incremental",
    "inefficient_code": "Inefficient code or query",
    "storage_layout": "Storage and data layout",
    "scheduling": "Scheduling",
    "placement": "Where the workload runs",
    "capacity": "Capacity size and count",
}

# What to do about it (section 7), in the six-question order: an earlier action makes the later
# ones on the same workload moot, so it is preferred when they overlap.
ACTIONS = {
    "eliminate": "Eliminate",
    "consolidate": "Consolidate",
    "optimize_in_place": "Optimize in place",
    "redesign": "Redesign",
    "reschedule": "Reschedule",
    "run_locally": "Run locally",
    "run_hybrid": "Run hybrid",
    "keep_in_fabric": "Keep in Fabric",
}
ACTION_ORDER = tuple(ACTIONS)

# Lifecycle (section 13). Discover and Analyze happen before a record exists.
STATUSES = ("proposed", "validating", "approved", "implemented", "verified", "rejected", "superseded")

# How sure the engine is that the problem is real.
#   proven      the evidence shows it exactly (identical normalized code reading the same data)
#   high        very likely; one thing the engine can't see should be checked
#   medium      a known pattern that is usually waste, but often has a reason
#   low         a hint worth a look
CONFIDENCE = ("proven", "high", "medium", "low")
LEVELS = ("low", "medium", "high")  # effort and risk

REQUIRED = ("id", "category", "action", "title", "artifacts", "current", "problem", "evidence", "recommendation",
            "alternatives", "impact", "effort", "risk", "confidence", "dependencies", "validation", "rollback",
            "owner", "status", "outcome", "next_step", "source", "rule")


def opportunity_id(rule: str, *keys: str) -> str:
    """Stable for the same rule on the same artifacts, so a rerun finds the same opportunity
    (and a rejected one stays rejected until its evidence changes)."""
    raw = json.dumps([rule, sorted(keys)], separators=(",", ":"))
    return "opp-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def evidence(kind: str, text: str, source: str, **detail: Any) -> dict[str, Any]:
    """One piece of evidence: what was seen, where it came from, and the details to check it."""
    return {"kind": kind, "text": text, "source": source, "detail": detail}


def artifact(workload: dict[str, Any], role: str) -> dict[str, Any]:
    """An artifact the opportunity touches, and its part in it (``keep``, ``remove``, ``change``, ``reads``)."""
    return {"workspace_id": workload.get("workspace_id"), "item_id": workload["item_id"], "type": workload["type"],
            "name": workload["name"], "role": role}


def make(*, rule: str, category: str, action: str, title: str, artifacts: list[dict[str, Any]], current: str,
         problem: str, evidence: list[dict[str, Any]], recommendation: str, alternatives: list[str],
         effort: str, risk: str, confidence: str, validation: list[str], rollback: str, source: str,
         next_step: dict[str, Any], dependencies: list[str] | None = None, impact: dict[str, Any] | None = None,
         affects: list[str] | None = None, key: list[str] | None = None) -> dict[str, Any]:
    """A complete opportunity, or ValueError naming what's wrong with it.

    ``affects`` lists the item ids whose cost this opportunity would remove or reduce; overlap
    between opportunities is worked out on it (assess.combine). ``key`` overrides what the id is
    built from (default: the artifacts' item ids)."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    if confidence not in CONFIDENCE:
        raise ValueError(f"unknown confidence {confidence!r}")
    if effort not in LEVELS or risk not in LEVELS:
        raise ValueError("effort and risk are low, medium or high")
    if not evidence:
        raise ValueError("an opportunity needs evidence")
    if not artifacts:
        raise ValueError("an opportunity needs the artifacts it is about")
    if not validation or not rollback:
        raise ValueError("an opportunity needs validation steps and a rollback")
    ids = key if key is not None else [a["item_id"] for a in artifacts]
    return {"id": opportunity_id(rule, *ids), "rule": rule, "category": category,
            "category_label": CATEGORIES[category], "action": action, "action_label": ACTIONS[action],
            "title": title, "artifacts": artifacts, "current": current, "problem": problem, "evidence": evidence,
            "recommendation": recommendation, "alternatives": alternatives, "impact": impact or {},
            "effort": effort, "risk": risk, "confidence": confidence, "dependencies": list(dependencies or []),
            "validation": validation, "rollback": rollback, "owner": None, "status": "proposed",
            "outcome": None, "next_step": next_step, "source": source,
            "affects": list(affects if affects is not None else [a["item_id"] for a in artifacts])}


def problems(o: dict[str, Any]) -> list[str]:
    """Everything wrong with an opportunity record; empty when it's complete."""
    out = [f"missing {k}" for k in REQUIRED if k not in o]
    if o.get("status") not in STATUSES:
        out.append(f"unknown status {o.get('status')!r}")
    if not o.get("evidence"):
        out.append("no evidence")
    for e in o.get("evidence") or []:
        if not e.get("source"):
            out.append("evidence without a source")
    return out
