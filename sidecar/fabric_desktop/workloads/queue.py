"""One list of running, queued and next work across this PC, each agent and Fabric.

Each place has its own scheduler and queue: Windows Task Scheduler on this PC, each agent's own
queue, and Fabric's scheduler. This reads what each one already says (agent status records, this
PC's schedules, the Fabric jobs this app started, and Fabric schedules on items in the
environments' workspaces) and labels every row with the scheduler that owns it. It never starts,
stops or reorders work, and it doesn't poll a tenant: Fabric work this app didn't start is in the
Monitoring hub.

Rows: ``{"state": "running" | "queued" | "unknown" | "next" | "missed", "what", "env", "where",
"owner", "at", "text", "started_by", "note"}``; an agent's rows add ``as_of``, its last heartbeat. ``where`` is ``{"kind": "pc" | "agent" | "fabric",
"name"}``. An agent that stopped reporting has its last report shown as it was: what it was running
is ``unknown``, and a schedule it should have run since is ``missed``. A schedule several agents
share through the mailbox (``shared``, shared_schedules.py) is one row, ``where`` ``{"kind": "agents",
"names"}``, since each due time runs once on whichever of them claims it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..fabric_run import TERMINAL
from .agents import agent_state


def _when(text: Any) -> dt.datetime | None:
    if not text:
        return None
    try:
        t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.astimezone()  # this PC's schedules are in local time


def _iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if t else None


def _what(entities: Any, fallback: str) -> str:
    return ", ".join(map(str, entities)) if isinstance(entities, list) and entities else fallback


def _source(source: Any) -> str:
    s = str(source or "")
    return f"schedule {s[len('schedule:'):]}" if s.startswith("schedule:") else s


def agent_rows(by_env: dict[str, list[dict[str, Any]]], now: dt.datetime, until: dt.datetime) -> list[dict[str, Any]]:
    rows = []
    shared: dict[tuple[str, str], list[tuple[str, bool, dt.datetime, Any, Any]]] = {}
    for env, records in by_env.items():
        for r in records:
            name = str(r.get("agent") or "")
            state = agent_state(r, now)
            start = len(rows)
            if not name or state == "stopped":  # a clean stop runs nothing until it is started again
                continue
            where, live = {"kind": "agent", "name": name}, state == "live"
            note = "" if live else "From its last report"
            for j in r.get("running") or []:
                rows.append({"state": "running" if live else "unknown", "what": _what(j.get("entities"), "Every table"),
                             "env": env, "where": where, "owner": f"{name}'s queue", "at": j.get("started_at"),
                             "text": "", "started_by": _source(j.get("source")),
                             "note": note and f"{note}. Picked up again when {name} is back."})
            queued = (r.get("counts") or {}).get("queued") or 0
            if queued:
                rows.append({"state": "queued" if live else "unknown", "what": f"{queued} {'job' if queued == 1 else 'jobs'}",
                             "env": env, "where": where, "owner": f"{name}'s queue", "at": None, "text": "",
                             "started_by": "", "note": note or "The record counts queued jobs; it doesn't list them"})
            for s in r.get("schedules") or []:
                at = _when(s.get("next"))
                if at is None or at > until:
                    continue
                if s.get("shared") is True:
                    shared.setdefault((env, str(s.get("id"))), []).append((name, live, at, s.get("entities"), r.get("heartbeat_at")))
                    continue
                missed = at <= now
                if missed and live:  # it is about to start it
                    missed = False
                rows.append({"state": "missed" if missed else "next",
                             "what": _what(s.get("entities"), "Every table" if isinstance(s.get("entities"), list)
                                           else "Tables not named (an older agent)"),
                             "env": env, "where": where, "owner": f"{name}'s schedule {s.get('id')}", "at": _iso(at),
                             "text": "", "started_by": "",
                             "note": f"Runs once when {name} is back" if missed else ("" if live else note)})
            rows[start:] = _as_of(rows[start:], r.get("heartbeat_at"))
    return rows + [_shared_row(env, sid, held, now) for (env, sid), held in shared.items()]


def _shared_row(env: str, sid: str, held: list[tuple[str, bool, dt.datetime, Any, Any]], now: dt.datetime
                ) -> dict[str, Any]:
    names = sorted({h[0] for h in held})
    live = [h for h in held if h[1]]
    at = min(h[2] for h in (live or held))
    entities = held[0][3]
    missed = not live and at <= now
    note = f"Shared by {', '.join(names)}: runs once, on whichever claims it"
    return {"state": "missed" if missed else "next",
            "what": _what(entities, "Every table" if isinstance(entities, list) else "Tables not named (an older agent)"),
            "env": env, "where": {"kind": "agents", "name": ", ".join(names), "names": names},
            "owner": f"shared schedule {sid}", "at": _iso(at), "text": "", "started_by": "",
            "note": note + (". None of them is reporting" if not live else ""),
            "as_of": max((str(h[4]) for h in held if h[4]), default=None)}


def _as_of(rows: list[dict[str, Any]], beat: Any) -> list[dict[str, Any]]:
    return [r | {"as_of": beat} for r in rows]


def pc_rows(pc: list[dict[str, Any]], now: dt.datetime, until: dt.datetime) -> list[dict[str, Any]]:
    rows = []
    for s in pc:
        at = _when(s.get("next_run"))
        if not s.get("enabled", True) or at is None or at > until:
            continue
        target = s.get("target") or {}
        rows.append({"state": "next", "what": target.get("name") if target.get("kind") == "notebook"
                     else _what(target.get("entities"), "Every table"),
                     "env": target.get("env"), "where": {"kind": "pc", "name": "This PC"},
                     "owner": "Windows Task Scheduler", "at": _iso(at), "text": s.get("text") or "",
                     "started_by": "", "note": "Runs only while you are signed in to Windows"})
    return rows


def fabric_rows(runs: list[dict[str, Any]], schedules: list[dict[str, Any]], env_of: dict[str, str]
                ) -> list[dict[str, Any]]:
    """Fabric jobs this app started that haven't finished, and enabled Fabric schedules. A schedule
    says how often it runs, not when next, so it has no ``at``."""
    rows = []
    for r in runs:
        status = r.get("status")
        if status in TERMINAL:
            continue
        state = {"NotStarted": "queued", "InProgress": "running"}.get(str(status), "unknown")
        rows.append({"state": state, "what": r.get("table") or r.get("name") or r.get("kind") or "Fabric job",
                     "env": r.get("env") or env_of.get(str(r.get("workspace_id"))),
                     "where": {"kind": "fabric", "name": r.get("workspace_name") or "Fabric"}, "owner": "Fabric",
                     "at": r.get("started_at"), "text": "", "started_by": f"{r.get('kind') or 'Run'} from this app",
                     "note": "" if state != "unknown" else f"Fabric last said {status}", "url": r.get("url")})
    for s in schedules:
        if not s.get("enabled"):
            continue
        rows.append({"state": "next", "what": s.get("name") or "Fabric item",
                     "env": env_of.get(str(s.get("workspace_id"))),
                     "where": {"kind": "fabric", "name": s.get("workspace_name") or "Fabric"},
                     "owner": "Fabric scheduler", "at": None, "text": s.get("text") or "", "started_by": "",
                     "note": s.get("type") or ""})
    return rows


_ORDER = {"running": 0, "unknown": 1, "queued": 2, "missed": 3, "next": 4}


def build(by_env: dict[str, list[dict[str, Any]]], pc: list[dict[str, Any]], runs: list[dict[str, Any]],
          schedules: list[dict[str, Any]], env_of: dict[str, str], now: dt.datetime, hours: float = 24
          ) -> list[dict[str, Any]]:
    """Every row, running and queued first, then what runs next within ``hours`` by time; Fabric
    schedules, which have no next time, come last."""
    until = now + dt.timedelta(hours=hours)
    rows = agent_rows(by_env, now, until) + pc_rows(pc, now, until) + fabric_rows(runs, schedules, env_of)
    return sorted(rows, key=lambda r: (_ORDER.get(r["state"], 9), r["at"] is None, str(r["at"] or ""), str(r["what"])))
