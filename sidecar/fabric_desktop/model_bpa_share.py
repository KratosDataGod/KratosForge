"""A model's last best practices check as rows for the shared Optimize lakehouse.

``analysis_rows`` turns what ``model_bpa_status`` returned into the input of
``optimize.shared.Shared.record_analysis``: one run, the model as its one item, and one finding and one
recommendation per Error or Warning finding. Info findings stay on this PC. The Optimize lakehouse owns
the writer and its consent step (``_optimize_share_analysis``); this module only shapes rows, reads
nothing and writes nothing.

- The fingerprint is the rule, the model and the object: ``rule_id`` is "bpa." and the rule's name as a
  slug (a hash of the name when it has no letters a slug can keep), ``target`` the object (for example
  'Sales'[Amount]). Rewording a rule's name makes it a new finding, so an earlier dismissal doesn't carry over. Neither carries the check's request id,
  which changes with every check, so a finding someone dismissed stays dismissed when it's found again.
  The request id is in the evidence.
- The run id is a hash of the model and the request id, so recording the same check twice adds nothing.
- Nothing here estimates a saving: impact is ``{"basis": "not estimated"}``.
- Every free-text field and the evidence go through ``redact``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

from . import redact

RULE_SET_VERSION = "model-bpa-1"
RULE_PREFIX = "bpa."
_SEVERITY = {"Error": "high", "Warning": "medium"}  # Info stays on this PC
_GENERATOR = ("If KratosForge built this model, the next Update model fixes it. Otherwise, change it in the model.")


def rule_id(rule: str) -> str:
    """'Hide foreign keys' -> bpa.hide-foreign-keys."""
    name = str(rule).strip()
    slug = re.sub(r"[\W_]+", "-", name.lower()).strip("-")  # letters of any script are kept
    return RULE_PREFIX + (slug or "r-" + hashlib.sha256(name.encode()).hexdigest()[:12])


def _json(value: Any) -> str:
    return json.dumps(redact.walk(value, stringify=True), sort_keys=True, separators=(",", ":"), default=str)


def _text(value: Any) -> str:
    return redact.secrets(str(value or ""))


def run_id(model_id: str, request_id: str) -> str:
    raw = json.dumps([str(model_id), str(request_id)], separators=(",", ":"))
    return "bpa-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def analysis_rows(status: dict[str, Any]) -> dict[str, Any]:
    """``record_analysis``'s arguments (run, artifacts, findings, recommendations) for the model's last check."""
    last = status.get("last") or {}
    if not last.get("request_id"):
        raise ValueError(f"{status.get('model') or 'The model'} has no finished check to share. Check it in Fabric first.")
    ws, model_id = str(status.get("workspace_id") or ""), str(status.get("model_id") or "")
    if not ws or not model_id:
        raise ValueError("The check's model has no workspace or id, so it can't be shared. Check the model again.")
    request_id, checked_at = str(last["request_id"]), str(last.get("checked_at") or "")
    try:
        zoned = dt.datetime.fromisoformat(checked_at).tzinfo is not None
    except ValueError:
        zoned = False
    if not zoned:
        raise ValueError("This check has no finish time with its time zone, so it can't be shared. Check the model again.")
    run = {"run_id": run_id(model_id, request_id), "started_at": checked_at, "finished_at": checked_at,
           "status": "partial" if last.get("no_tables") else "complete", "rule_set_version": RULE_SET_VERSION,
           "scope": _json({"model": status.get("model"), "env": status.get("env"), "request_id": request_id,
                           "package": last.get("package"), "version": last.get("version")})}
    artifacts = [{"workspace_id": ws, "item_id": model_id, "item_type": "SemanticModel",
                  "display_name": _text(status.get("model"))}]
    findings: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    # Worst first, so when two rule names give the same key (they differ only in case or punctuation),
    # the Error is kept, not the Warning.
    for f in sorted(last.get("findings") or [], key=lambda f: 0 if f.get("severity") == "Error" else 1):
        severity = _SEVERITY.get(str(f.get("severity")))
        if not severity:
            continue
        rid, target = rule_id(f.get("rule") or ""), _text(f.get("object") or "Model")
        if (rid, target) in seen:  # one finding per rule and object, as the fingerprint is
            continue
        seen.add((rid, target))
        where = {"workspace_id": ws, "item_id": model_id, "rule_id": rid, "target": target}
        detail = " ".join(p for p in (_text(f.get("description")),
                                      f"Category: {_text(f.get('category'))}." if f.get("category") else "",
                                      f"Object: {_text(f.get('object_type'))} {target}.".replace("  ", " ")) if p)
        findings.append(where | {"rule_version": RULE_SET_VERSION, "severity": severity,
                                 "title": _text(f.get("rule")), "detail": detail,
                                 "evidence": _json([{"rule": f.get("rule"), "object_type": f.get("object_type"),
                                                     "object": f.get("object"), "severity": f.get("severity"),
                                                     "request_id": request_id, "checked_at": checked_at,
                                                     "version": last.get("version")}])})
        recommendations.append(where | {"kind": "optimize_in_place",
                                        "reason": _text(f"{f.get('description') or f.get('rule')} {_GENERATOR}"),
                                        "impact": _json({"basis": "not estimated"}), "effort": None,  # not measured
                                        "confidence": "high" if severity == "high" else "medium"})
    return {"run": run, "artifacts": artifacts, "findings": findings, "recommendations": recommendations}
