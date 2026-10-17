"""Schedules shared by several agents: each due time becomes one request in the environment's mailbox.

A schedule with ``shared = true`` in agent.toml, given to several agents, doesn't run on whichever
agent noticed it. When it is due, each agent tries to create ``requests/sched-<id>-<slot>.json``
with ``If-None-Match: *``; only the first create lands, and the mailbox claim (mailbox.py) then
gives the run to exactly one agent. See docs/workloads/multi-worker.md.

The slot is named from the due time in UTC, never from when an agent noticed it, so agents whose
clocks differ by seconds name the same file. Every-N-minutes schedules are aligned to multiples of
the interval since the epoch, so every agent computes the same due times. Daily and weekly times
are each agent's local time, as for any schedule: agents sharing one must use the same time zone.

A slot's request carries a deadline (the next slot, at most 6 hours on), and an agent doesn't
create a slot past its deadline, so a run that already happened, or expired, never comes back.
Untested on a Fabric tenant until the pipeline mailbox has run there (roadmap 7.1).
"""

from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any

from .mailbox import CONTRACT

PREFIX = "sched-"
MAX_ID = 40  # sched-<id>-<slot> must fit the mailbox's 64-character request ids
WINDOW = 6 * 3600


def _utc(t: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc)


def interval(when: dict[str, Any]) -> float | None:
    """Seconds between runs of an every-N-minutes schedule; None for daily and weekly."""
    if when.get("kind") == "cron" and when.get("every_minutes"):
        return float(when["every_minutes"]) * 60
    return None


def next_aligned(when: dict[str, Any], now: float) -> float | None:
    """The next due time of a shared every-N-minutes schedule, the same on every agent."""
    step = interval(when)
    return None if step is None else math.floor(now / step) * step + step


def slot(when: dict[str, Any], due: float) -> tuple[str, float]:
    """``(slot name, deadline)`` for the run due at ``due``."""
    step = interval(when)
    start = math.floor(due / step) * step if step else due
    deadline = start + min(step, WINDOW) if step else start + WINDOW
    return _utc(start).strftime("%Y%m%dT%H%MZ"), deadline


def request_id(schedule_id: str, slot_name: str) -> str:
    return f"{PREFIX}{schedule_id}-{slot_name}"


def iso(t: float) -> str:
    return _utc(t).isoformat(timespec="seconds").replace("+00:00", "Z")


def submit(backend: Any, schedule: Any, due: float, now: float, agent: str) -> tuple[str, str]:
    """Create this slot's request unless another agent already did. Returns ``(request id, outcome)``:
    ``created``, ``exists`` (another agent was first) or ``late`` (past its deadline: not created)."""
    name, deadline = slot(schedule.when, due)
    rid = request_id(schedule.id, name)
    if now > deadline:
        return rid, "late"
    body = {"contract": CONTRACT, "request_id": rid, "env": schedule.env, "entities": list(schedule.entities),
            "backfill": schedule.backfill, "cascade": False, "deadline": iso(deadline),
            "submitted_by": f"shared schedule {schedule.id}, due {iso(due)}, written by agent {agent}"}
    created = backend.create(f"requests/{rid}.json", json.dumps(body, indent=2).encode())
    return rid, "created" if created else "exists"
