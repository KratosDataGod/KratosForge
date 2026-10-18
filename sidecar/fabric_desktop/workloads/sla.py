"""SLA targets for incidents: how soon each priority should be acknowledged and resolved.

The targets come from the organization's policy (the ``incident_sla`` key), else the defaults
below. They are applied when incidents are read, never stored on an incident, so changing the
policy changes every open incident's due times at once. Clocks start when the incident opened and
run all day; a priority with no target (P4 by default) has no due time.

Policy shape (minutes; 0 or null means no target)::

    "incident_sla": {"P1": {"acknowledge_minutes": 15, "resolve_minutes": 240}, ...}
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .incidents import ACTIVE, PRIORITIES, default_priority

DEFAULT_TARGETS: dict[str, dict[str, int | None]] = {
    "P1": {"acknowledge_minutes": 15, "resolve_minutes": 240},
    "P2": {"acknowledge_minutes": 30, "resolve_minutes": 240},
    "P3": {"acknowledge_minutes": 240, "resolve_minutes": 2880},
    "P4": {"acknowledge_minutes": None, "resolve_minutes": None},
}
FIELDS = ("acknowledge_minutes", "resolve_minutes")
AT_RISK = 0.25  # due soon: less than this share of the window is left
LONGEST = 365 * 24 * 60


def targets(policy: Any) -> tuple[dict[str, dict[str, int | None]], list[str]]:
    """The targets in effect, and what in the policy was ignored and why."""
    out = {p: dict(t) for p, t in DEFAULT_TARGETS.items()}
    problems: list[str] = []
    if not policy:
        return out, problems
    if not isinstance(policy, dict):
        return out, ["incident_sla isn't an object; the default SLAs apply."]
    for prio, spec in policy.items():
        if prio not in PRIORITIES:
            problems.append(f"incident_sla.{prio}: priorities are {', '.join(PRIORITIES)}; ignored.")
            continue
        if not isinstance(spec, dict):
            problems.append(f"incident_sla.{prio} isn't an object; ignored.")
            continue
        for field, value in spec.items():
            if field not in FIELDS:
                problems.append(f"incident_sla.{prio}.{field}: use {' or '.join(FIELDS)}; ignored.")
            elif value is None or value == 0:
                out[prio][field] = None
            elif isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= LONGEST:
                problems.append(f"incident_sla.{prio}.{field}: a whole number of minutes up to {LONGEST}; ignored.")
            else:
                out[prio][field] = value
    return out, problems


def _when(text: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clock(start: dt.datetime, minutes: int | None, done: dt.datetime | None, now: dt.datetime) -> dict[str, Any]:
    """One SLA clock: none, met, missed (done late), due, at_risk or breached (still open, late)."""
    if not minutes:
        return {"state": "none", "due": None}
    due = start + dt.timedelta(minutes=minutes)
    if done:
        return {"state": "met" if done <= due else "missed", "due": _iso(due)}
    if now > due:
        return {"state": "breached", "due": _iso(due), "over_minutes": int((now - due).total_seconds() // 60)}
    left = (due - now).total_seconds()
    return {"state": "at_risk" if left < AT_RISK * minutes * 60 else "due", "due": _iso(due),
            "left_minutes": int(left // 60)}


def status(inc: dict[str, Any], table: dict[str, dict[str, int | None]], now: dt.datetime) -> dict[str, Any]:
    """Where an incident stands against its priority's targets."""
    prio = inc.get("priority") or default_priority(inc)
    target = table.get(prio) or {}
    start = _when(inc.get("opened_at"))
    if start is None:
        return {"priority": prio, "acknowledge": {"state": "none", "due": None}, "resolve": {"state": "none", "due": None},
                "state": "none"}
    resolved = _when(inc.get("resolved_at")) if inc.get("state") not in ACTIVE else None
    acked = _when(inc.get("acknowledged_at")) or resolved
    ack = _clock(start, target.get("acknowledge_minutes"), acked, now)
    res = _clock(start, target.get("resolve_minutes"), resolved, now)
    order = ("breached", "missed", "at_risk", "due", "met", "none")
    worst = min((ack["state"], res["state"]), key=order.index)
    return {"priority": prio, "acknowledge": ack, "resolve": res, "state": worst}
