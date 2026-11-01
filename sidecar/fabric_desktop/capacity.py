"""Which Fabric capacity each environment's workspace is on, and whether it can run work (FINOPS-002).

Read only, with the developer's own sign-in. Two documented Fabric REST calls are used:

- ``GET /workspaces/{id}`` gives the workspace's ``capacityId`` (and, when Fabric sends them,
  ``capacityRegion`` and ``capacityAssignmentProgress``). Workspace access is enough.
- ``GET /capacities`` lists the capacities the signed-in user can see: those they administer or
  may assign workspaces to (Capacity.Read.All). A workspace's capacity can be missing from it
  without anything being wrong; that is shown as "not visible to you", not as "not found".

A capacity's ``state`` says whether it is running, not how busy it is: an Active capacity can
still be throttling. Utilization isn't read here (no public per-capacity metrics API for
non-admins), so it is always shown as unknown. Nothing here changes a capacity, and nothing asks
for consent: when the scope or a sign-in is missing, the environment says so.

Each observation has the time it was read. Observations are cached on this PC so Monitor works
offline; a cached one older than ``MAX_AGE_S`` is marked stale.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable

from . import costs, jsonfile
from . import measurement as ms
from .retrying import TRANSIENT_STATUS

MAX_AGE_S = 3600
SOURCE = "Fabric REST API (GET /workspaces/{id}, GET /capacities)"

# Why an environment's capacity couldn't be read, in the user's words. "ok" means it was.
ACCESS = {
    "ok": "Read",
    "no_workspace": "No workspace assigned",
    "no_capacity": "The workspace isn't on a capacity",
    "capacity_not_visible": "The capacity isn't visible to you",
    "scope_not_granted": "Capacity access not granted",
    "workspace_no_access": "You can't open the workspace",
    "workspace_not_found": "The workspace doesn't exist any more",
    "signed_out": "Signed out",
    "offline": "Couldn't reach Fabric",
    "error": "Couldn't read",
}
ACTIONS = {
    "no_workspace": "Assign a workspace in Data mesh > Environments, or keep running on this PC.",
    "no_capacity": "Ask a workspace admin to assign the workspace to a capacity in Fabric (Workspace settings > License info). "
                   "Lakehouses and Fabric runs need one; runs on this PC don't.",
    "capacity_not_visible": "Fabric only lists capacities you administer or may assign workspaces to. The workspace "
                            "still runs on it; ask your capacity admin for its status, or check the Capacity Metrics app.",
    "scope_not_granted": "Sign in again and accept capacity access (Capacity.Read.All), or ask IT to grant it. "
                         "docs/permissions.md explains it. Everything else keeps working.",
    "workspace_no_access": "Ask a workspace admin for access, or assign a workspace you can open.",
    "workspace_not_found": "Assign another workspace in Data mesh > Environments.",
    "signed_out": "Sign in to read it. Work on this PC carries on.",
    "offline": "Shown from the last time it was read. Work on this PC carries on.",
    "error": "Try Refresh. Work on this PC carries on.",
}

# Capacity states, as the capacity list returns them, and what they mean for a run.
STATES = {
    "Active": ("running", "Running. Work can start, but this doesn't say how busy it is: a running capacity can still "
                          "slow down or delay jobs when it is overloaded."),
    "Inactive": ("not_running", "Not running: paused, suspended or an expired trial. Fabric runs, schedules and new "
                                "lakehouses fail until an admin resumes it. Runs on this PC still work."),
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def classify_error(exc: BaseException) -> str:
    """Which ACCESS reason a failed call means. Permission is told apart from absence by status code."""
    status = getattr(exc, "status", None)
    code = str(getattr(exc, "code", "") or "")
    text = str(exc)
    if isinstance(exc, PermissionError) or "InsufficientScopes" in text or "AADSTS65001" in text:
        return "scope_not_granted"
    if status in (401, 403) or code in ("Unauthorized", "InsufficientPrivileges", "PrincipalTypeNotSupported"):
        return "workspace_no_access"
    if status == 404 or code in ("WorkspaceNotFound", "EntityNotFound", "ItemNotFound"):
        return "workspace_not_found"
    if status in TRANSIENT_STATUS or isinstance(exc, (ConnectionError, TimeoutError, OSError)) \
            or "ConnectionError" in type(exc).__name__ or "Timeout" in type(exc).__name__:
        return "offline"
    return "error"


def _capacity_view(cap: dict[str, Any]) -> dict[str, Any]:
    """Only fields the capacity list actually returned; nothing filled in."""
    out = {k: cap[src] for k, src in (("id", "id"), ("name", "displayName"), ("sku", "sku"), ("region", "region"),
                                      ("state", "state")) if cap.get(src) not in (None, "")}
    if cus := costs.sku_cu(cap.get("sku")):
        out["cu"] = cus
    return out


def _status(state: str | None) -> tuple[str, str]:
    if not state:
        return "unknown", "Its state isn't known."
    return STATES.get(state, ("unknown", f"Fabric reports its state as {state!r}, which this app doesn't recognize."))


def observe_env(env: str, workspace_id: str | None, workspace_name: str | None,
                get_workspace: Callable[[str], dict[str, Any]] | None,
                capacities: dict[str, dict[str, Any]] | None, capacities_error: str | None,
                now: dt.datetime | None = None) -> dict[str, Any]:
    """One environment's capacity, as read now.

    ``capacities`` is the visible capacity list by lower-case id (None when it couldn't be read,
    with ``capacities_error`` the ACCESS reason). ``get_workspace`` is None when signed out."""
    now = now or _now()
    base: dict[str, Any] = {"env": env, "workspace_id": workspace_id, "workspace_name": workspace_name,
                            "capacity_id": None, "capacity": None, "status": "unknown", "status_text": "",
                            "region": None, "assignment": None, "observed_at": _iso(now), "source": SOURCE}

    def done(access: str, **extra: Any) -> dict[str, Any]:
        out = base | extra | {"access": access, "access_text": ACCESS[access], "action": ACTIONS.get(access, "")}
        out["utilization"] = ms.unknown("capacity_utilization", ms.PERCENT,
                                        "Fabric has no public per-capacity utilization API for non-admins; the "
                                        "Capacity Metrics app shows it to capacity admins.",
                                        workspace_id=workspace_id, capacity_id=out["capacity_id"])
        return out

    if not workspace_id:
        return done("no_workspace", observed_at=None)
    if get_workspace is None:
        return done("signed_out", observed_at=None)
    try:
        ws = get_workspace(workspace_id)
    except Exception as exc:  # noqa: BLE001 - each environment stands alone
        return done(classify_error(exc), observed_at=None, error=str(exc)[:300])
    base["workspace_name"] = ws.get("displayName") or workspace_name
    base["region"] = ws.get("capacityRegion")
    base["assignment"] = ws.get("capacityAssignmentProgress")
    cap_id = ws.get("capacityId")
    if not cap_id:
        return done("no_capacity", status="not_running",
                    status_text="Without a capacity the workspace can't hold lakehouses or run Fabric jobs.")
    base["capacity_id"] = cap_id
    if capacities is None:
        return done(capacities_error or "error")
    cap = capacities.get(str(cap_id).lower())
    if cap is None:
        return done("capacity_not_visible")
    view = _capacity_view(cap)
    status, text = _status(view.get("state"))
    return done("ok", capacity=view, region=view.get("region") or base["region"], status=status, status_text=text)


def observe(environments: dict[str, dict[str, Any]], get_workspace: Callable[[str], dict[str, Any]] | None,
            list_capacities: Callable[[], list[dict[str, Any]]] | None, now: dt.datetime | None = None) -> dict[str, Any]:
    """Every environment's capacity. A failure in one call or one environment never stops the rest."""
    now = now or _now()
    capacities: dict[str, dict[str, Any]] | None = None
    cap_error: str | None = "signed_out" if list_capacities is None else None
    if list_capacities is not None:
        try:
            capacities = {str(c.get("id", "")).lower(): c for c in list_capacities() if c.get("id")}
        except Exception as exc:  # noqa: BLE001
            cap_error = classify_error(exc)
            if cap_error in ("workspace_no_access", "workspace_not_found"):
                cap_error = "scope_not_granted"  # a 401/403 on /capacities is the scope, not a workspace
    envs: list[dict[str, Any]] = []
    for env, cfg in environments.items():
        reader = get_workspace
        if any(e["access"] == "offline" for e in envs):
            def reader(_ws: str) -> dict[str, Any]:  # one unreachable Fabric is enough: don't wait again
                raise ConnectionError("Fabric couldn't be reached for an earlier environment")
        envs.append(observe_env(env, cfg.get("workspace_id"), cfg.get("workspace_name"),
                                reader if get_workspace is not None else None, capacities, cap_error, now))
    return {"observed_at": _iso(now), "environments": envs, "partial": any(e["access"] not in ("ok", "no_workspace")
                                                                           for e in envs)}


def merge_cached(fresh: dict[str, Any], cached: dict[str, Any] | None, now: dt.datetime | None = None,
                 max_age_s: float = MAX_AGE_S) -> dict[str, Any]:
    """Keep the last good reading of an environment whose read failed now (offline, signed out),
    labeled with its age, and mark old readings stale."""
    now = now or _now()
    old = {e["env"]: e for e in (cached or {}).get("environments", [])}
    out = []
    for e in fresh["environments"]:
        prev = old.get(e["env"])
        if e["access"] in ("offline", "signed_out", "error") and prev and prev.get("access") == "ok" \
                and prev.get("workspace_id") == e["workspace_id"]:
            e = prev | {"cached": True, "read_error": e["access_text"], "action": e["action"]}
        out.append(e | freshness(e, now, max_age_s))
    return fresh | {"environments": out}


def freshness(e: dict[str, Any], now: dt.datetime, max_age_s: float = MAX_AGE_S) -> dict[str, Any]:
    t = ms.parse_time(e.get("observed_at"))
    if not t:
        return {"age_seconds": None, "stale": False}
    age = (now - t).total_seconds()
    return {"age_seconds": round(max(age, 0)), "stale": age > max_age_s}


def load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(path: Path, doc: dict[str, Any]) -> None:
    jsonfile.write_text(path, json.dumps(doc, default=str))
