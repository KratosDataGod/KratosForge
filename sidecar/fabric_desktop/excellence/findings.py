"""The finding record (FEA-102, brief section 4), built from a failed control and its template.

A finding is a plain dict so any view, RPC or table can carry it. Every field the brief asks for is
filled from the control (severity, dimension, why it matters, how to fix it, validation, rollback,
references, rule version) and from the outcome (affected resource, what was discovered, the evidence
ids and their confidence). Expected benefit comes from the control's template or the outcome, and is
always qualified: a finding never promises a saving.

Advisory findings (a fail resting only on inferred evidence) carry ``advisory: true`` and are not
counted in scores; views show them apart.

``templates`` holds the change proposals for a scored finding (FEA-402, excellence.templates): text a
person can copy and apply, never run by the app. Empty for an advisory finding or a control without one.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .controls import DIMENSIONS, REQUIREMENTS, SEVERITIES, Control
from .evidence import canonical
from .templates import for_outcome

_SEV = {s: i for i, s in enumerate(SEVERITIES)}
_LVL = {"low": 0, "medium": 1, "high": 2}
_BASIS = {"measured": 0, "verified_configuration": 1, "inferred": 2, "insufficient": 3}
CONFIDENCE_LABELS = {"measured": "Measured", "verified_configuration": "Verified configuration",
                     "inferred": "Inferred", "insufficient": "Insufficient evidence"}


def finding_id(control_id: str, subject_id: str) -> str:
    """Stable for the same control on the same resource, so a rerun finds the same finding and
    history can tell new, resolved and recurring ones apart."""
    return "fx-" + hashlib.sha256(canonical([control_id, subject_id]).encode()).hexdigest()[:16]


def make(c: Control, outcome: dict[str, Any], severity: str) -> dict[str, Any]:
    t = c.remediation
    advisory = outcome["outcome"] != "fail"
    seen = outcome["text"] if not advisory else (outcome.get("advisory") or {}).get("text", "")
    ids = outcome["evidence_ids"] if not advisory else (outcome.get("advisory") or {}).get("evidence_ids", [])
    return {"id": finding_id(c.id, outcome["subject"]["id"]), "title": c.title, "resource": outcome["subject"],
            "severity": severity, "dimension": c.dimension, "category": DIMENSIONS[c.dimension],
            "requirement": c.requirement, "requirement_label": REQUIREMENTS[c.requirement],
            "discovered": seen, "evidence_ids": list(ids), "why": t["why"], "solution": t["solution"],
            "steps": list(t["steps"]), "benefit": outcome.get("benefit") or t["benefit"], "effort": t["effort"],
            "risk": t["risk"], "validation": t["validation"], "rollback": t["rollback"],
            "references": [dict(r) for r in c.references], "confidence": outcome["basis"],
            "confidence_label": CONFIDENCE_LABELS[outcome["basis"]], "rule": c.rule, "academy": c.academy,
            "advisory": advisory, "detail": outcome.get("detail") or {}, "status": "open",
            "templates": [] if advisory else for_outcome(outcome)}


def rank(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scored before advisory; then worse severity first, so a critical security finding is never
    under a minor one with a bigger estimate; then stronger evidence, easier, safer, by id."""
    return sorted(findings, key=lambda f: (f["advisory"], _SEV[f["severity"]], _BASIS[f["confidence"]],
                                           _LVL[f["effort"]], _LVL[f["risk"]], f["rule"], f["id"]))


def next_actions(findings: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """The few most valuable next steps: the top scored findings, one per control so one rule on
    many items doesn't fill the list."""
    out, seen = [], set()
    for f in rank(findings):
        if f["advisory"] or f["rule"] in seen:
            continue
        seen.add(f["rule"])
        out.append({"finding_id": f["id"], "title": f["title"], "severity": f["severity"],
                    "resource": f["resource"], "solution": f["solution"], "effort": f["effort"], "risk": f["risk"],
                    "confidence": f["confidence"]})
        if len(out) == limit:
            break
    return out
