"""Tables scheduled in two places: an agent and this PC, or two agents, running the same tables.

Each place keeps its own queue, so nothing stops both from running the same entity: table
leases only stop them writing at the same moment. This reads what each place says it will run
(agent status records, this PC's schedules) and reports every pair that shares a table. It never
changes a schedule.

An agent schedule's ``entities`` arrived after the first agents shipped. A record that leaves it
out can't be compared, so it is reported as unknown rather than read as "every table".
"""

from __future__ import annotations

from typing import Any

from ..fabric_run import describe_schedule
from ..local_schedules import local_configuration
from .agents import agent_state


def _text(when: Any) -> str:
    if not isinstance(when, dict) or not when.get("kind"):
        return ""
    try:
        return describe_schedule({"configuration": local_configuration(when)}).replace(" Local", "")
    except (ValueError, TypeError, KeyError):
        return ""


def placements(by_env: dict[str, list[dict[str, Any]]], pc: list[dict[str, Any]], now: Any
               ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(known, unknown)``: one row per schedule that runs metadata tables, with its place and
    the entities it runs (``None`` for every table). A cleanly stopped agent runs nothing, so its
    schedules are left out; an offline one is kept, because it runs a missed schedule when it is back."""
    known, unknown = [], []
    for env, records in by_env.items():
        newest: dict[str, dict[str, Any]] = {}
        for r in records:
            name = str(r.get("agent") or "")
            if name and str(r.get("heartbeat_at") or "") >= str(newest.get(name, {}).get("heartbeat_at") or ""):
                newest[name] = r
        for name, r in sorted(newest.items()):
            state = agent_state(r, now)
            if state == "stopped":
                continue
            for s in r.get("schedules") or []:
                row = {"env": env, "place": "agent", "agent": name, "state": state, "schedule": s.get("id"),
                       "text": _text(s.get("when")), "next": s.get("next"), "shared": s.get("shared") is True}
                if not isinstance(s.get("entities"), list):
                    unknown.append(row)
                    continue
                known.append(row | {"entities": sorted(map(str, s["entities"])) or None})
    for s in pc:
        target = s.get("target") or {}
        if target.get("kind") != "meta" or not s.get("enabled", True) or target.get("env") not in by_env:
            continue
        known.append({"env": target["env"], "place": "pc", "agent": None, "state": "live", "schedule": s.get("id"),
                      "text": s.get("text") or "", "next": s.get("next_run"),
                      "entities": sorted(map(str, target.get("entities") or [])) or None})
    return known, unknown


def _shared(a: list[str] | None, b: list[str] | None) -> list[str] | None | bool:
    """The entities both run: ``None`` when both run every table, ``False`` when they share none."""
    if a is None:
        return b if b is not None else None
    if b is None:
        return a
    both = sorted(set(a) & set(b))
    return both or False


def overlaps(by_env: dict[str, list[dict[str, Any]]], pc: list[dict[str, Any]], now: Any) -> dict[str, Any]:
    """Every pair of schedules in different places that run a table in common, per environment.
    Two schedules on the same agent, or both on this PC, share one queue, so they aren't reported;
    nor is one schedule that agents share through the mailbox (shared_schedules.py)."""
    known, unknown = placements(by_env, pc, now)
    found = []
    for i, a in enumerate(known):
        for b in known[i + 1:]:
            if a["env"] != b["env"] or (a["place"], a["agent"]) == (b["place"], b["agent"]):
                continue
            if a.get("shared") and b.get("shared") and a["schedule"] == b["schedule"]:
                continue  # one schedule shared through the mailbox: each due time runs once
            shared = _shared(a["entities"], b["entities"])
            if shared is False:
                continue
            found.append({"env": a["env"], "entities": shared, "schedules": [a, b]})
    return {"overlaps": found, "unknown": unknown}
