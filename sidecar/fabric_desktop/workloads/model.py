"""Ids, kinds, capabilities and the states a workload and an observation can be in."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..errors import NotSetUp
from ..retrying import TRANSIENT_STATUS, status_of

KINDS = ("meta_entity", "notebook", "fabric_item", "pipeline")
KIND_LABELS = {"meta_entity": "Metadata entity", "notebook": "Notebook", "fabric_item": "Fabric item",
               "pipeline": "Pipeline"}

# What each kind can do in principle; a deployment narrows it to what it can do right now.
CAPABILITIES = ("discoverable", "observable", "runnable", "schedulable", "cancelable", "deployable", "read_only")

# Runs, normalized. The native status is kept next to it.
RUN_STATES = ("ok", "failed", "running", "needs_decision", "skipped", "cancelled", "unknown")
_FABRIC_STATES = {"Completed": "ok", "Failed": "failed", "Cancelled": "cancelled", "Deduped": "skipped",
                  "NotStarted": "running", "InProgress": "running"}
_LOCAL_STATES = {"ok": "ok", "failed": "failed", "running": "running", "needs_decision": "needs_decision",
                 "skipped": "skipped", "submitted": "running", "blocked": "failed", "cancelled": "cancelled",
                 "succeeded": "ok", "pending": "running"}  # the last two: local pipeline runs

# Health of a workload, worst first.
HEALTH = ("failing", "late", "attention", "unknown", "healthy", "idle")
HEALTH_LABELS = {"failing": "Failing", "late": "Late", "attention": "Needs attention", "unknown": "Not observed",
                 "healthy": "Healthy", "idle": "No recorded runs"}

# How fresh an observation is. Collection problems are never workload problems.
OBS_STATES = ("live", "cached", "stale", "unknown", "offline", "unauthorized", "signed_out", "not_set_up",
              "throttled", "partial", "error")
OBS_LABELS = {"live": "Live", "cached": "Cached", "stale": "Stale", "unknown": "Not collected yet", "offline": "Offline",
              "unauthorized": "No access", "signed_out": "Signed out", "not_set_up": "Not set up",
              "throttled": "Fabric asked to slow down", "partial": "Partly read", "error": "Couldn't read"}

ROLES = ("team", "technical_owner", "business_contact", "support_contact")
ROLE_LABELS = {"team": "Team", "technical_owner": "Technical owner", "business_contact": "Business contact",
               "support_contact": "Support contact"}
CRITICALITY = ("critical", "high", "standard", "low")
# Placeholder owners written by templates; they say nobody has taken responsibility yet.
PLACEHOLDER_OWNERS = {"", "me", "owner", "todo", "tbd", "unknown", "someone"}


def meta_id(project: str, entity: str) -> str:
    return f"meta:{project}:{entity}"


def notebook_id(name: str) -> str:
    return f"notebook:{name}"


def fabric_id(workspace_id: str, item_id: str) -> str:
    return f"fabric:{workspace_id}:{item_id}"


def pipeline_id(path: str) -> str:
    """A pipeline on this PC, by its ``<name>.DataPipeline`` folder."""
    return f"pipeline:{path}"


def parse_id(wid: str) -> dict[str, str]:
    kind, _, rest = str(wid).partition(":")
    if kind == "meta":
        project, _, entity = rest.rpartition(":")
        if project and entity:
            return {"kind": "meta_entity", "project": project, "entity": entity}
    elif kind == "notebook" and rest:
        return {"kind": "notebook", "name": rest}
    elif kind == "pipeline" and rest:
        return {"kind": "pipeline", "path": rest}
    elif kind == "fabric":
        ws, _, item = rest.partition(":")
        if ws and item:
            return {"kind": "fabric_item", "workspace_id": ws, "item_id": item}
    raise ValueError(f"{wid!r} isn't a workload id")


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds") if t else None


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc)
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def run_state(status: Any, source: str = "local") -> str:
    """A native run status as one of RUN_STATES."""
    s = str(status or "")
    table = _FABRIC_STATES if source == "fabric" else _LOCAL_STATES
    return table.get(s) or _LOCAL_STATES.get(s.lower()) or "unknown"


def observation(source: str, state: str, collected_at: dt.datetime | str | None = None, *, detail: str = "",
                max_age_s: float | None = None, at: dt.datetime | None = None) -> dict[str, Any]:
    """What was read from one source, when, and whether it can be trusted as current.

    A ``cached`` observation older than ``max_age_s`` becomes ``stale``."""
    if state not in OBS_STATES:
        raise ValueError(f"unknown observation state {state!r}")
    collected = parse_time(collected_at) if not isinstance(collected_at, dt.datetime) else collected_at
    age = (at or now()) - collected if collected else None
    if state == "cached" and age is not None and max_age_s is not None and age.total_seconds() > max_age_s:
        state = "stale"
    return {"source": source, "state": state, "label": OBS_LABELS[state], "collected_at": iso(collected),
            "age_seconds": round(age.total_seconds()) if age is not None else None, "detail": detail}


def classify_error(exc: BaseException) -> tuple[str, str]:
    """(observation state, plain message) for a failure while reading a source."""
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if isinstance(exc, PermissionError) or " 401" in low or " 403" in low or "forbidden" in low \
            or "unauthorized" in low or "insufficientscopes" in low:
        return "unauthorized", "Your sign-in can't read this. Ask for access, or sign in with an account that has it."
    status = status_of(exc)
    if status == 429 or " 429" in low or "too many requests" in low:
        return "throttled", "Fabric asked to slow down. Try again in a few minutes."
    if status in TRANSIENT_STATUS:
        return "offline", "Fabric isn't answering right now. Try again in a few minutes."
    if "sign in" in low or "no account" in low or "interaction_required" in low or "login" in low:
        return "signed_out", "Sign in to read this."
    # No workspace assigned, no metadata project, a lakehouse not promoted yet: a step not taken, not a
    # fault. Decided by type, never by wording; each raise site's own words say what to do.
    if isinstance(exc, NotSetUp):
        return "not_set_up", str(exc)
    if isinstance(exc, (ConnectionError, TimeoutError)) or any(w in low for w in (
            "connectionerror", "timed out", "timeout", "name resolution", "failed to establish", "network")):
        return "offline", "Couldn't reach Fabric. Check the network and try again."
    return "error", text
