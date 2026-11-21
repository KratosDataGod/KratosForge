"""Academy mapping (FEA-601): where to learn the fix for each control, and what earns progress.

``MAP`` is static, reviewed data: each control id to the Learn lessons and Academy exercises that teach
its fix, ``{kind: "lesson" | "exercise", id, title}`` (an exercise also names its ``pack``). Lesson ids
are Learn modules and labs (``LM-32``, ``LAB-07``, learn/content); exercise ids are exercises of an
Academy certification pack (``dp-700`` ``E10``, academy/packs). Only ids that exist are named, and the
titles are the content's own: tests/test_excellence_academy_map.py loads both catalogs and fails on an
unknown, retired or renamed one. A control with no lesson that teaches its fix is listed in
``UNMAPPED`` with the reason, never given a loosely related lesson.

``credit_for(event)`` is the progress rule, a pure function with no storage: a fix the assessment
validated (the control assessed again and passed, on configuration or measured evidence) or a passed
exercise earns credit. A dismissal, an accepted risk, a not-evaluated rerun or anything else earns
none, so hiding a finding can never look like learning (plan risk R5).

Nothing here reads files, Fabric or progress. The engine imports this module only for the lookup.
"""

from __future__ import annotations

import re
from typing import Any

from . import findings as fx

# The Academy pack the exercises below belong to.
PACK = "dp-700"


def _lesson(lid: str, title: str) -> dict[str, str]:
    return {"kind": "lesson", "id": lid, "title": title}


def _exercise(eid: str, title: str) -> dict[str, str]:
    return {"kind": "exercise", "pack": PACK, "id": eid, "title": title}


_LEAST_PRIVILEGE = _lesson("LM-32", "Least privilege in workspaces")
_ROLES_EXERCISE = _exercise("E10", "Least-privilege workspace roles for six people")
_CAPACITY = _lesson("LM-33", "Capacity and cost")
_DEPLOY_RULES = _exercise("E09", "Deployment pipeline: stages, deployment rules, and what doesn't deploy")
_SHORTCUTS = _lesson("LM-24", "Shortcuts and OneLake security")
_DIRECT_LAKE = _lesson("LM-09", "Direct Lake: tables a Power BI model reads straight from OneLake")

MAP: dict[str, tuple[dict[str, str], ...]] = {
    "FEA-ARCH-001": (_lesson("LM-06", "Lakehouses and the medallion layers"),),
    "FEA-ARCH-002": (_lesson("LM-26", "Three-part names: Unity Catalog and Fabric"),),
    "FEA-ARCH-003": (_lesson("LM-34", "Environments, Git and deployments"),),
    "FEA-ARCH-004": (_lesson("LM-34", "Environments, Git and deployments"), _DEPLOY_RULES),
    "FEA-CAP-002": (_lesson("LM-35", "Monitoring and incidents"),
                    _exercise("E44", "Monitoring hub: find the failed ingestion and the run that got slower")),
    "FEA-CAP-003": (_CAPACITY,),
    "FEA-CAP-004": (_CAPACITY,),
    "FEA-DE-001": (_lesson("LM-12", "Incremental loads: a watermark that misses nothing"),
                   _exercise("E21", "Full or incremental: load orders with a watermark that misses nothing"),
                   _exercise("E22", "Incremental MERGE into a Delta table")),
    "FEA-DE-002": (_lesson("LM-36", "Enterprise notebook templates"),),
    "FEA-DE-003": (_lesson("LM-25", "V-Order, OPTIMIZE and VACUUM"),
                   _lesson("LM-29", "Time travel and restoring a table")),
    "FEA-DEVOPS-001": (_DEPLOY_RULES,),
    "FEA-DEVOPS-002": (_DEPLOY_RULES,),
    "FEA-PERF-002": (_SHORTCUTS,
                     _exercise("E29", "Shortcuts: a table shortcut, a folder shortcut, and a chain to avoid")),
    "FEA-REL-002": (_exercise("E02", "An environment for a notebook: libraries, resources and the default environment"),),
    "FEA-SEC-001": (_LEAST_PRIVILEGE, _ROLES_EXERCISE),
    "FEA-SEC-002": (_LEAST_PRIVILEGE,),
    "FEA-SEC-003": (_LEAST_PRIVILEGE,),
    "FEA-SEC-004": (_LEAST_PRIVILEGE, _ROLES_EXERCISE),
    "FEA-SM-002": (_exercise("E12", "Row-level security: a predicate that shows each region its rows"), _SHORTCUTS),
    "FEA-SM-003": (_DIRECT_LAKE, _lesson("LAB-07", "A Gold layer that stays in Direct Lake"),
                   _exercise("E45", "Semantic model refresh: read the history, spot a Direct Lake fallback")),
    "FEA-SM-005": (_lesson("LM-05", "Keys: primary, foreign, business and surrogate"), _DIRECT_LAKE),
}

# Controls no lesson or exercise teaches the fix for yet, and why.
UNMAPPED: dict[str, str] = {
    "FEA-CAP-001": "No lesson covers pausing, resuming or moving a capacity.",
    "FEA-GOV-001": "No lesson covers workspace descriptions or ownership.",
    "FEA-PERF-001": "No lesson covers finding and retiring duplicate notebooks.",
    "FEA-REL-001": "No lesson covers error handling in notebook code.",
    "FEA-REL-003": "No lesson covers deactivated pipeline activities.",
    "FEA-SEC-005": "No lesson covers Key Vault secrets in notebooks.",
    "FEA-SM-001": "No lesson covers relationship filter direction in a semantic model.",
    "FEA-SM-004": "No lesson covers relationship column types in a semantic model.",
    "FEA-SM-006": "No lesson covers hiding key columns in a semantic model.",
}


def learn(control_id: str) -> list[dict[str, str]]:
    """Where to learn the fix for ``control_id``: copies of its entries in order, or empty when it has
    none (unmapped or unknown)."""
    return [dict(e) for e in MAP.get(control_id, ())]


def coverage() -> dict[str, int]:
    return {"mapped": len(MAP), "unmapped": len(UNMAPPED)}


# ---- progress

CREDITED = ("fix_validated", "exercise_passed")
NO_CREDIT = ("dismissed", "accepted_risk", "not_evaluated")
# Evidence a validating rerun may rest on; inferred evidence is advice and never passes a control.
_VALID_BASIS = ("measured", "verified_configuration")
# The Academy's own id shapes (academy/registry.py): a pack id and an exercise or capstone id. Anything
# else can't name an exercise, and a "/" in either could mint another exercise's key.
_PACK_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,40}")
_EXERCISE_ID = re.compile(r"E\d{2}|C\d")


def _none(reason: str) -> dict[str, Any]:
    return {"credit": 0, "key": None, "reason": reason}


def credit_for(event: dict[str, Any]) -> dict[str, Any]:
    """Progress for one event: ``{credit: 0 | 1, key, reason}``. ``key`` is stable for the same fix or
    exercise, so a caller can count each once.

    - ``fix_validated``: ``{control_id, subject_id, finding_id, outcome, basis}`` from the rerun. Credit
      only when the rerun passed (``outcome == "pass"``) on measured or verified configuration evidence
      and ``finding_id`` is that control's finding on that resource.
    - ``exercise_passed``: ``{pack, exercise_id}``. Credit for the exercise.
    - Anything else, including ``dismissed``, ``accepted_risk`` and ``not_evaluated``: no credit.
    """
    kind = event.get("kind") if isinstance(event, dict) else None
    if kind in NO_CREDIT:
        return _none(f"{kind} earns no progress; only a validated fix or a passed exercise does.")
    if kind == "fix_validated":
        cid, sid = event.get("control_id"), event.get("subject_id")
        if not (isinstance(cid, str) and cid and isinstance(sid, str) and sid):
            return _none("A validated fix names its control and resource.")
        if cid not in MAP and cid not in UNMAPPED:
            return _none("The control isn't one the assessment has.")
        try:
            expected = fx.finding_id(cid, sid)
        except UnicodeError:
            return _none("The resource id isn't valid text.")
        if event.get("finding_id") != expected:
            return _none("The finding isn't this control's finding on this resource.")
        if event.get("outcome") != "pass":
            return _none("The control didn't pass when assessed again.")
        if event.get("basis") not in _VALID_BASIS:
            return _none("The pass doesn't rest on measured or configuration evidence.")
        return {"credit": 1, "key": "fix:" + event["finding_id"], "reason": "The fix was validated."}
    if kind == "exercise_passed":
        pack, eid = event.get("pack"), event.get("exercise_id")
        if not (isinstance(pack, str) and _PACK_ID.fullmatch(pack) and isinstance(eid, str)
                and _EXERCISE_ID.fullmatch(eid)):
            return _none("A passed exercise names its pack and exercise.")
        return {"credit": 1, "key": f"exercise:{pack}/{eid}", "reason": "The exercise was passed."}
    return _none("Unknown event; only a validated fix or a passed exercise earns progress.")
