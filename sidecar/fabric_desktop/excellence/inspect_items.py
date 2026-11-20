"""What a notebook's code or a pipeline's activities show, without keeping the code (FEA-301, FEA-302).

:func:`inspect` is the hook the optimization engine calls once per workload while the definition it
already read is still in memory (opportunities.assess.assess ``inspect=``). It returns only:

- for a notebook, the notebook best-practice rules (notebook_practices) that matched: per rule id,
  its severity, how many times it matched, and the cell and line of the first ones. Never the cell
  text, and for a secret (NB001) never what was found, only that it was found and where;
- for a pipeline, counts and activity names: which activities are deactivated and which name a
  fixed workspace id. Never the activity JSON or the ids themselves.

No Fabric call is made here, and nothing runs.
"""

from __future__ import annotations

import re
from typing import Any

from .. import notebook_practices
from .evidence import text

MAX_PLACES = 20  # cell and line kept per rule; ``count`` has them all
MAX_NAMES = 50  # activity names kept per list
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_WORKSPACE_KEYS = {"workspaceid"}


def inspect(w: dict[str, Any]) -> dict[str, Any]:
    """The inspection of one workload (see the module docstring). ``{"read": False}`` when its
    definition wasn't read, and ``{}`` for a type that isn't inspected."""
    if not isinstance(w, dict):
        return {}
    kind = w.get("type")
    if kind not in ("Notebook", "DataPipeline"):
        return {}
    if w.get("definition") != "read":
        return {"read": False}
    try:
        return notebook(w) if kind == "Notebook" else pipeline(w)
    except Exception as exc:  # noqa: BLE001 - an unreadable definition is not evaluated, never a crash or a pass
        return {"read": True, "error": f"The definition couldn't be inspected ({type(exc).__name__})."}


def notebook(w: dict[str, Any]) -> dict[str, Any]:
    cells = w.get("cells")
    if not isinstance(cells, list):
        return {"read": True, "error": "The notebook's cells weren't available."}
    runs = w.get("runs") or {}
    scheduled = bool(runs.get("scheduled")) if runs.get("read") else None
    found = notebook_practices.check([str(c) for c in cells], scheduled=bool(scheduled))["findings"]
    rules: dict[str, dict[str, Any]] = {}
    for f in found:
        r = rules.setdefault(f["rule_id"], {"severity": f["severity"], "count": 0, "where": []})
        r["count"] += 1
        if len(r["where"]) < MAX_PLACES:
            r["where"].append({"cell": int(f["cell"]), "line": int(f["line"])})
    # When the job history wasn't read, whether it's scheduled is unknown: the scheduled-only rules weren't
    # checked, so their absence from ``rules`` is not a pass. ``unchecked`` names them.
    unchecked = sorted(r.id for r in notebook_practices.RULES if r.scheduled_only) if scheduled is None else []
    return {"read": True, "kind": "notebook", "cells": len(cells), "scheduled": scheduled,
            "rules": dict(sorted(rules.items())), "unchecked": unchecked}


def _literal_workspace(value: Any) -> bool:
    """A workspace id written as text, not an expression (``@...`` or ``{"type": "Expression"}``)."""
    return isinstance(value, str) and not value.lstrip().startswith("@") and bool(_GUID.fullmatch(value.strip()))


def _names_fixed_workspace(node: Any) -> bool:
    if isinstance(node, dict):
        return any((str(k).lower() in _WORKSPACE_KEYS and _literal_workspace(v)) or _names_fixed_workspace(v)
                   for k, v in node.items())
    if isinstance(node, list):
        return any(_names_fixed_workspace(v) for v in node)
    return False


def pipeline(w: dict[str, Any]) -> dict[str, Any]:
    acts = w.get("activities")
    if not isinstance(acts, list):
        return {"read": True, "error": "The pipeline's activities weren't available."}
    inactive, fixed, no_state, no_props = [], [], [], []
    for a in acts:
        name = text(a.get("name") or a.get("type")) or "activity"  # cut, so a hostile name can't bloat evidence
        if not isinstance(a.get("inactive"), bool):
            no_state.append(name)
        elif a["inactive"]:
            inactive.append(name)
        tp = a.get("type_properties")
        if not isinstance(tp, dict):
            no_props.append(name)
        elif _names_fixed_workspace(tp):
            fixed.append(name)
    return {"read": True, "kind": "pipeline", "activities": len(acts),
            "inactive": sorted(inactive)[:MAX_NAMES], "inactive_count": len(inactive),
            "fixed_workspace": sorted(fixed)[:MAX_NAMES], "fixed_workspace_count": len(fixed),
            "state_unknown": sorted(no_state)[:MAX_NAMES], "properties_unknown": sorted(no_props)[:MAX_NAMES]}
