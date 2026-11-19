"""What a notebook's code or a pipeline's activities show, without keeping the code (FEA-301, FEA-302).

:func:`inspect` is the hook the optimization engine calls once per workload while the definition it
already read is still in memory (opportunities.assess.assess ``inspect=``). It returns only:

- for a notebook, the notebook best-practice rules (notebook_practices) that matched: per rule id,
  its severity, how many times it matched, and the cell and line of the first ones. Never the cell
  text, and for a secret (NB001) never what was found, only that it was found and where;
- for a pipeline, counts and activity names: which activities are deactivated and which name a
  fixed workspace id; of the activities that call out (CALLOUT_TYPES), which have no retry and which
  have no timeout below the 12-hour default; and whether any activity runs on another's failure.
  Never the activity JSON, the ids or the policy values themselves.

No Fabric call is made here, and nothing runs.
"""

from __future__ import annotations

import re
from typing import Any

from .. import notebook_practices

MAX_PLACES = 20  # cell and line kept per rule; ``count`` has them all
MAX_NAMES = 50  # activity names kept per list
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_WORKSPACE_KEYS = {"workspaceid"}

# Activities that call out to another service or compute, where a transient failure (throttling, a
# busy Spark pool, a dropped connection) is ordinary and a hang holds the pipeline: data movement,
# notebooks and Spark jobs, queries and procedures, HTTP calls, and Fabric item refreshes. Left out:
# control flow (ForEach, If, Switch, Until, Wait, variables, Filter, Fail: nothing to retry), child
# pipelines (Invoke/Execute pipeline: the child's own activities carry the policy), WebHook (its
# timeout is a typeProperty), and Outlook and Teams (a retry can send the message twice).
CALLOUT_TYPES = frozenset({
    "Copy", "TridentNotebook", "Notebook", "SparkJobDefinition", "DatabricksNotebook", "Lookup", "GetMetadata",
    "Script", "SqlServerStoredProcedure", "WebActivity", "AzureFunctionActivity", "RefreshDataflow",
    "PBISemanticModelRefresh", "AzureDataExplorerCommand",
})
DEFAULT_TIMEOUT_SECONDS = 12 * 3600  # Fabric and Data Factory's activity timeout when none is set (0.12:00:00)
_TIMESPAN = re.compile(r"^(?:(\d+)\.)?(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?$")
_FAILURE_CONDITIONS = {"failed", "completed"}
MAX_NAME = 256  # characters kept of an activity name


def inspect(w: dict[str, Any]) -> dict[str, Any]:
    """The inspection of one workload (see the module docstring). ``{"read": False}`` when its
    definition wasn't read, and ``{}`` for a type that isn't inspected."""
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


def _expression(value: Any) -> bool:
    """A setting given by an expression: set on purpose, its value only known at run time."""
    return ((isinstance(value, str) and value.lstrip().startswith("@"))
            or (isinstance(value, dict) and str(value.get("type", "")).lower() == "expression"))


def _retry_set(policy: dict[str, Any]) -> bool | None:
    """True when a retry count above 0 (or an expression) is set, False when none is, None when unreadable."""
    value = policy.get("retry")
    if value is None:
        return False
    if _expression(value):
        return True
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip()) > 0
    except ValueError:
        return None


def _timeout_set(policy: dict[str, Any]) -> bool | None:
    """True when a timeout under the 12-hour default (or an expression) is set, False when none is or
    it is 12 hours or more, None when it can't be read."""
    value = policy.get("timeout")
    if value is None:
        return False
    if _expression(value):
        return True
    m = _TIMESPAN.match(str(value).strip())
    if not m:
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in m.groups())
    if minutes > 59 or seconds > 59:
        return None
    return 0 < ((days * 24 + hours) * 60 + minutes) * 60 + seconds < DEFAULT_TIMEOUT_SECONDS


def pipeline(w: dict[str, Any]) -> dict[str, Any]:
    acts = w.get("activities")
    if not isinstance(acts, list):
        return {"read": True, "error": "The pipeline's activities weren't available."}
    inactive, fixed, no_state, no_props = [], [], [], []
    no_retry, no_timeout, no_policy, no_depends = [], [], [], []
    callouts, failure_path = 0, False
    for a in acts:
        name = str(a.get("name") or a.get("type") or "activity")[:MAX_NAME]
        if not isinstance(a.get("inactive"), bool):
            no_state.append(name)
        elif a["inactive"]:
            inactive.append(name)
        tp = a.get("type_properties")
        if not isinstance(tp, dict):
            no_props.append(name)
        elif _names_fixed_workspace(tp):
            fixed.append(name)
        deps = a.get("depends_on")
        if not isinstance(deps, list):
            no_depends.append(name)
        elif any(str(c).lower() in _FAILURE_CONDITIONS for d in deps if isinstance(d, dict)
                 for c in (d.get("conditions") or [])):
            failure_path = True
        if str(a.get("type")) not in CALLOUT_TYPES:
            continue
        callouts += 1
        policy = a.get("policy")
        retry = _retry_set(policy) if isinstance(policy, dict) else None
        timeout = _timeout_set(policy) if isinstance(policy, dict) else None
        if retry is None or timeout is None:  # an older engine, or a policy that can't be read: never a pass
            no_policy.append(name)
            continue
        if not retry:
            no_retry.append(name)
        if not timeout:
            no_timeout.append(name)
    return {"read": True, "kind": "pipeline", "activities": len(acts),
            "inactive": sorted(inactive)[:MAX_NAMES], "inactive_count": len(inactive),
            "fixed_workspace": sorted(fixed)[:MAX_NAMES], "fixed_workspace_count": len(fixed),
            "state_unknown": sorted(no_state)[:MAX_NAMES], "properties_unknown": sorted(no_props)[:MAX_NAMES],
            "calls_out": callouts, "no_retry": sorted(no_retry)[:MAX_NAMES], "no_retry_count": len(no_retry),
            "no_timeout": sorted(no_timeout)[:MAX_NAMES], "no_timeout_count": len(no_timeout),
            "policy_unknown": sorted(no_policy)[:MAX_NAMES], "failure_path": failure_path,
            "depends_unknown": sorted(no_depends)[:MAX_NAMES]}
