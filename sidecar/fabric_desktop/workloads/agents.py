"""Reading the status records agents publish (``agents/<name>.json``, written by agent_status.py)."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any


def read_status(backend: Any) -> list[dict[str, Any]]:
    """Every agent status record in one environment's control area. A record that isn't valid
    JSON is skipped; a listing that fails raises, so the caller can tell "none" from "unreadable"."""
    out = []
    for name in backend.list("agents"):
        stored = backend.read(f"agents/{name}")
        if stored is None:
            continue
        try:
            body = json.loads(stored.data or b"{}")
        except ValueError:
            continue
        if isinstance(body, dict) and body.get("kind") == "agent_status":
            out.append(body)
    return out


def _when(text: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def agent_state(record: dict[str, Any], now: dt.datetime) -> str:
    """``stopped`` (it said so), ``live`` (its promised next report hasn't passed) or ``offline``
    (it has: it stopped without saying so). A record with no readable time is offline."""
    if record.get("state") == "stopped":
        return "stopped"
    stale = _when(record.get("stale_after"))
    return "live" if stale is not None and now < stale else "offline"


def _version(text: Any) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in str(text).split("+")[0].split("-")[0].split("."))
    except ValueError:
        return None


def compare_version(agent: Any, app: str) -> str:
    """``same``, ``older`` or ``newer`` than this app, or ``unknown`` when either can't be read."""
    a, b = _version(agent), _version(app)
    if a is None or b is None:
        return "unknown"
    return "same" if a == b else "older" if a < b else "newer"


ENV_FIELDS = ("counts", "running", "schedules", "recent", "pipeline", "incidents", "concurrency")


def fleet(by_env: dict[str, list[dict[str, Any]]], now: dt.datetime, app_version: str) -> list[dict[str, Any]]:
    """One row per agent across environments. An agent writes one record per environment it
    serves; the newest heartbeat speaks for the agent, each environment's record for its own jobs."""
    agents: dict[str, dict[str, Any]] = {}
    for env, records in by_env.items():
        for r in records:
            name = str(r.get("agent") or "")
            if not name:
                continue
            row = agents.setdefault(name, {"agent": name, "envs": {}, "_beat": None})
            beat = _when(r.get("heartbeat_at"))
            if row["_beat"] is None or (beat is not None and beat > row["_beat"]):
                row.update({k: r.get(k) for k in ("host", "version", "started_at", "heartbeat_at", "stale_after",
                                                  "status_seconds", "environments")})
                for k in ("owner", "identity"):  # left out by older agents: unknown, so not set
                    if k in r:
                        row[k] = r[k]
                    else:
                        row.pop(k, None)
                row["state"], row["_beat"] = agent_state(r, now), beat
            row["envs"][env] = {"state": agent_state(r, now), "heartbeat_at": r.get("heartbeat_at"),
                                **{k: r.get(k) for k in ENV_FIELDS}}
    out = []
    for row in agents.values():
        row.pop("_beat")
        row.setdefault("state", "offline")
        row["version_compare"] = compare_version(row.get("version"), app_version)
        out.append(row)
    return sorted(out, key=lambda a: a["agent"])
