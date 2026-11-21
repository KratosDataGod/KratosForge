"""An assessment as rows for the shared Optimize lakehouse (OE-004).

``analysis_rows`` turns what ``optimization_assess`` returned into the input of
``optimize.shared.Shared.record_analysis``: one run, the items it looked at, one finding and one
recommendation per opportunity. The Optimize lakehouse owns the writer and its consent step; this
module only shapes rows, reads nothing and writes nothing.

- Each opportunity becomes one finding on its anchor item (the one to keep, else the first by id) with
  the opportunity id as its target. The opportunity id is stable for the same rule on the same items,
  so the finding's fingerprint is too: one shared list across PCs, and a dismissed one stays dismissed
  when it's found again. If the set of items changes (a third copy appears), it's a new opportunity.
- Every free-text field and the evidence and impact JSON go through ``redact``.
- A demonstration fixture is never shared: its items don't exist and nothing in it is a finding.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .. import redact

RULE_SET_VERSION = "opportunities-1"
RULE_PREFIX = "oe."
# How sure and whether it counts in the totals, as the shared findings' severity (high, medium, low, info).
_SEVERITY = {"proven": "high", "high": "high", "medium": "medium", "low": "low"}


def rule_id(rule: str) -> str:
    """OE-DUP-NB -> oe.dup-nb: the engine's rules, kept apart from Optimize's own."""
    name = str(rule).strip().lower()
    return RULE_PREFIX + (name[3:] if name.startswith("oe-") else name)


def _json(value: Any) -> str:
    return json.dumps(redact.walk(value, stringify=True), sort_keys=True, separators=(",", ":"), default=str)


def _text(value: Any) -> str:
    return redact.secrets(str(value or ""))


def _anchor(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    keep = [a for a in artifacts if a.get("role") == "keep"]
    return min(keep or artifacts, key=lambda a: (str(a.get("workspace_id")), str(a.get("item_id"))))


def _severity(o: dict[str, Any]) -> str:
    if o.get("counted") is False:
        return "info"
    return _SEVERITY.get(str(o.get("confidence")), "info")


def _detail(o: dict[str, Any]) -> str:
    parts = [o.get("current"), o.get("problem")]
    items = ", ".join(f"{a.get('name') or a.get('item_id')} ({a.get('role')})" for a in o.get("artifacts") or [])
    if items:
        parts.append(f"Items: {items}.")
    if o.get("counted") is False and o.get("overlaps"):
        parts.append(f"Not counted in the totals: overlaps {', '.join(map(str, o['overlaps']))}.")
    for label, key in (("Check before you change it", "validation"), ("How to undo it", "rollback")):
        steps = o.get(key) or []
        if steps:
            parts.append(f"{label}: " + " ".join(map(str, steps)))
    return _text(" ".join(str(p) for p in parts if p))


def run_id(result: dict[str, Any]) -> str:
    """The same assessment gives the same run id, so recording it twice adds nothing."""
    raw = json.dumps([result.get("assessed_at"), result.get("scope"), result.get("days"),
                      sorted(str(w.get("id")) for w in result.get("workspaces") or [])],
                     sort_keys=True, separators=(",", ":"), default=str)
    return "oe-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def analysis_rows(result: dict[str, Any]) -> dict[str, Any]:
    """``record_analysis``'s arguments (run, artifacts, findings, recommendations) for one assessment."""
    if result.get("fixture"):
        raise ValueError("A demonstration fixture isn't shared: its items don't exist and nothing in it is a finding.")
    scope = result.get("scope") or {}
    coverage = result.get("coverage") or {}
    partial = bool((scope.get("budget") or {}).get("reached")) or bool(coverage.get("not_read")) \
        or bool(coverage.get("history_not_read"))
    run = {"run_id": run_id(result), "started_at": result.get("assessed_at"), "finished_at": result.get("assessed_at"),
           "status": "partial" if partial else "complete", "rule_set_version": RULE_SET_VERSION,
           "scope": _json({"scope": scope, "days": result.get("days"),
                           "workspaces": [w.get("id") for w in result.get("workspaces") or []]})}
    artifacts: dict[tuple[str, str], dict[str, Any]] = {}
    for w in result.get("workloads") or []:
        artifacts[(w["workspace_id"], w["item_id"])] = {
            "workspace_id": w["workspace_id"], "item_id": w["item_id"], "item_type": w.get("type"),
            "display_name": _text(w.get("name"))}
    findings, recommendations = [], []
    for o in result.get("opportunities") or []:
        arts = [a for a in o.get("artifacts") or [] if a.get("workspace_id") and a.get("item_id")]
        if not arts:
            continue
        for a in arts:
            artifacts.setdefault((a["workspace_id"], a["item_id"]), {
                "workspace_id": a["workspace_id"], "item_id": a["item_id"], "item_type": a.get("type"),
                "display_name": _text(a.get("name"))})
        anchor = _anchor(arts)
        where = {"workspace_id": anchor["workspace_id"], "item_id": anchor["item_id"],
                 "rule_id": rule_id(o["rule"]), "target": o["id"]}
        findings.append(where | {"rule_version": RULE_SET_VERSION, "severity": _severity(o),
                                 "title": _text(o.get("title")), "detail": _detail(o),
                                 "evidence": _json(o.get("evidence") or [])})
        recommendations.append(where | {"kind": o["action"], "reason": _text(o.get("recommendation")),
                                        "impact": _json(o.get("impact") or {}), "effort": o.get("effort"),
                                        "confidence": o.get("confidence")})
    return {"run": run, "artifacts": list(artifacts.values()), "findings": findings,
            "recommendations": recommendations}
