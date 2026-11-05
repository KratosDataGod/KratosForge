"""Shortcut health without anyone opening the page: the probe Shortcuts > Health uses, and the
agent's scheduled check (``[shortcut_health]`` in agent.toml, docs/agent.md).

A check asks OneLake for one entry of the folder a shortcut sits in, then one entry of the
shortcut itself, and reads the answer with ``shortcut_center.health_from_probe``. The agent's
check first lists the lakehouses of each environment's workspaces and their shortcuts (Fabric
REST GETs). That is all it does in Fabric: reads only, no job, so no capacity is used, and
nothing is written to Fabric, OneLake or the platform.

A break goes to Teams once. The events are the app's ``shortcut.broken`` and ``shortcut.fixed``
(governance_notices.shortcut_events), sent through platform_notify's ledger, which is keyed by
the shortcut (its lakehouse id and path): a shortcut that stays broken is posted once, seeing it
healthy again posts the fix and closes the break, so a later break is posted again. The ledger
and the outbox for messages Teams didn't take are ``shortcut_alerts.sqlite3`` in the agent's home.

Several agents can serve one environment. Each publishes in its status record whether it checks
shortcuts there; only the first by name among the live ones that do (``poster``) checks and posts,
so the channel gets one message per break, not one per agent.
"""

from __future__ import annotations

import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from . import governance_notices as notices
from . import shortcut_center as center
from . import shortcuts as shortcuts_mod
from .redact import describe
from .workloads.agents import agent_state

LEDGER = "shortcut_alerts.sqlite3"


class Probe(Protocol):
    def probe(self, workspace_id: str, lakehouse_id: str, directory: str) -> tuple[int | None, str]: ...


def probe(files: Probe, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check each shortcut [{id, lakehouse_id, host_workspace_id, path, full_path, target_type}]:
    [{id, health, detail}]. The folder it sits in is listed once for all the shortcuts in it."""
    hosts: dict[tuple[str, str, str], int | None] = {}
    lock = threading.Lock()

    def host_status(s: dict[str, Any]) -> int | None:
        key = (s["host_workspace_id"], s["lakehouse_id"], s["path"])
        with lock:
            if key in hosts:
                return hosts[key]
        status, _ = files.probe(s["host_workspace_id"], s["lakehouse_id"], s["path"])
        with lock:
            hosts[key] = status
        return status

    def check(s: dict[str, Any]) -> dict[str, Any]:
        host = host_status(s)
        status, message = (None, "") if host in (401, 403) else files.probe(s["host_workspace_id"], s["lakehouse_id"],
                                                                            s["full_path"])
        return {"id": s["id"]} | center.health_from_probe(s, host, status, message)

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(check, items))


def env_workspaces(cfg: dict[str, Any]) -> list[str]:
    """The workspaces an environment's lakehouses live in: its own and each designation's."""
    ids = [cfg.get("workspace_id") or ""] + [(w or {}).get("id") or "" for w in (cfg.get("workspaces") or {}).values()]
    return sorted(set(ids) - {""})


def scan(workspaces: list[str], fabric: Any, onelake: Any, files: Probe) -> tuple[list[dict[str, Any]], list[str]]:
    """Every shortcut in the lakehouses of ``workspaces``, checked: the rows ``shortcuts.describe``
    makes, with id, lakehouse_name, health and health_detail. Reads only: ``fabric`` lists
    lakehouses, ``onelake`` lists shortcuts, ``files`` lists one entry of a folder. A workspace or
    lakehouse that can't be listed is a problem, and the rest is still checked."""
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for ws in workspaces:
        try:
            lakehouses = fabric.list_lakehouses(ws)
        except Exception as exc:  # noqa: BLE001 - a paused capacity or no access: check the other workspaces
            problems.append(f"workspace {ws}: {describe(exc)}")
            continue
        for lh in lakehouses:
            try:
                found = onelake.list_shortcuts(ws, lh["id"])
            except Exception as exc:  # noqa: BLE001 - check the other lakehouses
                problems.append(f"lakehouse {lh.get('displayName') or lh['id']}: {describe(exc)}")
                continue
            for s in found:
                r = shortcuts_mod.describe(s) | {"lakehouse_id": lh["id"], "host_workspace_id": ws,
                                                 "lakehouse_name": lh.get("displayName", "")}
                r["id"] = center.identity(r)
                rows.append(r)
    by_id = {r["id"]: r for r in probe(files, rows)}
    for r in rows:
        r["health"], r["health_detail"] = by_id[r["id"]]["health"], by_id[r["id"]]["detail"]
    return rows, problems


def events(rows: list[dict[str, Any]], env: str, at: str) -> list[dict[str, Any]]:
    """``shortcut.broken`` for each broken row and ``shortcut.fixed`` for each healthy one. The ledger
    posts a break once and a fix only after its break was posted, so sending every row each time is
    what makes a new break, and only a new break, news. Unreachable and not checked say nothing."""
    changes = [{"kind": {"broken": "broke", "healthy": "fixed"}[r["health"]], "shortcut_id": r["id"],
                "name": r.get("name"), "lakehouse_name": r.get("lakehouse_name"), "at": at,
                "note": r.get("health_detail") if r["health"] == "broken" else ""}
               for r in rows if r.get("health") in ("broken", "healthy")]
    return [e | {"env": env} for e in notices.shortcut_events(changes)]


def poster(me: str, records: list[dict[str, Any]], now: dt.datetime) -> bool:
    """Whether agent ``me`` checks and posts for an environment, from its agents' status records:
    it does unless a live agent whose name sorts first says it checks shortcuts there too."""
    others = [str(r.get("agent") or "") for r in records
              if r.get("agent") and r.get("agent") != me and (r.get("shortcut_health") or {}).get("enabled")
              and agent_state(r, now) == "live"]
    return not any(o < me for o in others)
