"""Telling Teams channels about platform requests and broken shortcuts, once each.

Governance sends one event per change (a request waiting on someone, decided or withdrawn; a
shortcut broken or fixed). Each event goes to the routes whose kinds and domains match it, or to
the default route when none does. An event is posted once per ``dedupe`` key: a shortcut that
stays broken isn't posted again, and ``shortcut.fixed`` is posted only when its break was, then
closes it, so the next break is news again. Quiet hours and Teams being unreachable work as for
incidents (notify.py): messages wait in a SQLite outbox on this machine.

People are named by UPN only. A request's reason and detail never go into a Teams message, which
anyone in the channel can read: the message names the request and says where to open it in the app.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ContextManager

from .. import redact
from .notify import DEFAULT_ROUTE, KEEP_TRYING, Outbox, Quiet, failure, transaction

KINDS = ("request.waiting", "request.decided", "request.withdrawn", "shortcut.broken", "shortcut.fixed",
         "incident.impact", "product.changed")
OPENS = {"request.waiting": "request", "shortcut.broken": "shortcut"}  # kinds that open a dedupe key
CLOSES = {"request.decided": "request.waiting", "request.withdrawn": "request.waiting", "shortcut.fixed": "shortcut.broken"}
VIEWS = {"platform": ("requests",), "shortcuts": ("health",), "incident": ("detail",), "onelake": ("products",)}
SEVERITIES = ("info", "warn")
_UPN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ROLE = re.compile(r"^(platform_admin|requester|(domain_owner|product_owner):[A-Za-z0-9._-]+)$")
_TITLES = {"request.waiting": "Waiting on you", "request.decided": "Decided", "request.withdrawn": "Withdrawn",
           "shortcut.broken": "Shortcut broken", "shortcut.fixed": "Shortcut fixed",
           "incident.impact": "Incident upstream of your data", "product.changed": "Data product changed"}
_WHERE = {"requests": "Platform > Requests", "health": "Shortcuts > Health", "detail": "Monitor > Incidents",
          "products": "OneLake > Data products"}


def _strs(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name} is a list of strings")
    return [v.strip() for v in value if v.strip()]


def validate(event: dict[str, Any]) -> dict[str, Any]:
    """The event in its checked form, or ValueError naming what's wrong with it."""
    if not isinstance(event, dict):
        raise ValueError("an event is an object")
    kind = event.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    out: dict[str, Any] = {"kind": kind}
    for key in ("id", "title"):
        value = str(event.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} is required")
        out[key] = (redact.secrets(value) if key == "title" else value)[:200]
    try:
        out["at"] = dt.datetime.fromisoformat(str(event.get("at") or "")).isoformat()
    except ValueError:
        raise ValueError("at is an ISO 8601 time") from None
    # Title and detail go to Teams, the bell and the notice record: a token or SAS address in them never does.
    out["detail"] = redact.secrets(str(event.get("detail") or ""))[:500]
    out["domain"] = str(event.get("domain") or "")
    out["env"] = str(event.get("env") or "")
    out["severity"] = str(event.get("severity") or "info")
    if out["severity"] not in SEVERITIES:
        raise ValueError(f"severity is one of {', '.join(SEVERITIES)}")
    to = event.get("to") or {}
    if not isinstance(to, dict):
        raise ValueError("to is an object with people and roles")
    people, roles = _strs(to.get("people"), "to.people"), _strs(to.get("roles"), "to.roles")
    if bad := [p for p in people if not _UPN.match(p)]:
        raise ValueError(f"to.people are UPNs (name@domain), not {bad[0]!r}")
    if bad := [r for r in roles if not _ROLE.match(r)]:
        raise ValueError(f"to.roles are platform_admin, requester, domain_owner:<id> or product_owner:<id>, not {bad[0]!r}")
    if not people and not roles:
        raise ValueError("to names at least one person or role")
    out["to"] = {"people": people, "roles": roles}
    opened = event.get("open") or {}
    if not isinstance(opened, dict) or opened.get("view") not in VIEWS or opened.get("tab") not in VIEWS[opened.get("view")]:
        raise ValueError("open is {view: platform, tab: requests}, {view: shortcuts, tab: health}, "
                         "{view: incident, tab: detail} or {view: onelake, tab: products}")
    out["open"] = {"view": opened["view"], "tab": opened["tab"], "target": str(opened.get("target") or "")}
    out["dedupe"] = str(event.get("dedupe") or f"{kind}:{out['id']}")
    return out


def recipients(to: dict[str, list[str]]) -> str:
    """Who a message is for, in words: UPNs as given, roles by name."""
    names = list(to["people"])
    for role in to["roles"]:
        kind, _, target = role.partition(":")
        names.append({"platform_admin": "platform admins", "requester": "the requester",
                      "domain_owner": f"owners of domain {target}", "product_owner": f"owners of data product {target}"}[kind])
    return ", ".join(names)


def message(event: dict[str, Any]) -> tuple[str, list[str]]:
    """(title, lines) for one event. A request's detail stays out; a shortcut's says what broke."""
    lines = [f"For {recipients(event['to'])}."]
    scope = " · ".join(x for x in (f"Domain {event['domain']}" if event["domain"] else "",
                                   event["env"].title() if event["env"] else "") if x)
    if scope:
        lines.append(scope)
    if event["kind"].startswith(("shortcut.", "incident.", "product.")) and event["detail"]:
        lines.append(event["detail"])
    lines.append(f"Open it in KratosForge > {_WHERE[event['open']['tab']]}.")
    return f"{_TITLES[event['kind']]}: {event['title']}", lines


@dataclass(frozen=True)
class PlatformRoute:
    """A channel for some platform events. An empty filter matches everything."""
    name: str
    url: str
    kinds: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()

    def matches(self, event: dict[str, Any]) -> bool:
        def ok(allowed: tuple[str, ...], value: str) -> bool:
            return not allowed or value.lower() in {a.lower() for a in allowed}
        return ok(self.kinds, event["kind"]) and ok(self.domains, event["domain"])

    @staticmethod
    def parse(raw: dict[str, Any]) -> "PlatformRoute":
        name = str(raw.get("name") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not name:
            raise ValueError("every route has a name")
        if url and not url.startswith("https://"):
            raise ValueError(f"route {name}: the Teams webhook address starts with https://")
        kinds = tuple(_strs(raw.get("kinds"), f"route {name}: kinds"))
        if bad := [k for k in kinds if k not in KINDS]:
            raise ValueError(f"route {name}: {bad[0]!r} isn't a kind; kinds are {', '.join(KINDS)}")
        return PlatformRoute(name, url, kinds, tuple(_strs(raw.get("domains"), f"route {name}: domains")))


@dataclass
class PlatformRouting:
    routes: list[PlatformRoute] = field(default_factory=list)
    quiet: Quiet | None = None

    def by_name(self, name: str) -> PlatformRoute | None:
        return next((r for r in self.routes if r.name == name), None)

    def for_event(self, event: dict[str, Any]) -> list[PlatformRoute]:
        named = [r for r in self.routes if r.name != DEFAULT_ROUTE and r.url and r.matches(event)]
        if named:
            return named
        default = self.by_name(DEFAULT_ROUTE)
        return [default] if default and default.url else []

    @staticmethod
    def parse(raw: dict[str, Any]) -> "PlatformRouting":
        routes = [PlatformRoute.parse(r) for r in raw.get("routes") or []]
        names = [r.name for r in routes]
        if len(set(names)) != len(names):
            raise ValueError("route names are unique")
        quiet_text = str(raw.get("quiet") or "").strip()
        try:
            quiet = Quiet.parse(quiet_text, tuple(_strs(raw.get("quiet_exempt"), "quiet_exempt"))) if quiet_text else None
        except ValueError:
            raise ValueError("quiet hours look like 22:00-07:00") from None
        return PlatformRouting(routes, quiet)


class Ledger:
    """Which dedupe keys were posted and which are still open, next to the outbox."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, kind TEXT, at REAL, open INTEGER)")

    def _db(self) -> ContextManager[sqlite3.Connection]:
        return transaction(self.path)

    def get(self, key: str) -> tuple[str, bool] | None:
        with self._db() as db:
            row = db.execute("SELECT kind, open FROM sent WHERE key = ?", (key,)).fetchone()
        return (row[0], bool(row[1])) if row else None

    def mark(self, key: str, kind: str, is_open: bool) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO sent (key, kind, at, open) VALUES (?, ?, ?, ?)",
                       (key, kind, self.clock(), int(is_open)))

    def forget(self, key: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM sent WHERE key = ?", (key,))

    def open_keys(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT key, kind, at FROM sent WHERE open = 1 ORDER BY at").fetchall()
        return [{"key": r[0], "kind": r[1], "at": r[2]} for r in rows]


def _opened_key(event: dict[str, Any]) -> str:
    """The key of the event a closing event closes: ``shortcut.fixed:<id>`` closes ``shortcut.broken:<id>``."""
    opener = CLOSES[event["kind"]]
    key = event["dedupe"]
    return opener + key[len(event["kind"]):] if key.startswith(event["kind"]) else f"{opener}:{event['id']}"


def decide(event: dict[str, Any], ledger: Ledger) -> str | None:
    """Why this event isn't posted, or None when it should be."""
    seen = ledger.get(event["dedupe"])
    if event["kind"] in OPENS:
        return "already posted and still open" if seen and seen[1] else None
    if seen:
        return "already posted"
    if event["kind"] == "shortcut.fixed":
        opened = ledger.get(_opened_key(event))
        if not (opened and opened[1]):
            return "its break wasn't posted"
    return None


def send(events: list[dict[str, Any]], routing: PlatformRouting, outbox: Outbox, ledger: Ledger,
         post: Callable[[str, str, list[str]], None], now: dt.datetime | None = None) -> dict[str, Any]:
    """Post what these (validated) events need, after anything still waiting. Never raises.

    Returns counts plus one result per event: ``sent``, ``queued`` (Teams unreachable, retried
    on the next call), ``held`` (quiet hours), or ``skipped`` with the reason."""
    now = now or dt.datetime.fromtimestamp(outbox.clock()).astimezone()
    t = outbox.clock()
    sent = queued = held = 0
    down: dict[str, str] = {}
    for item in outbox.pending():
        if t - item["created"] > KEEP_TRYING:
            outbox.done(item["id"])
            continue
        route = routing.by_name(item["route"]) or routing.by_name(DEFAULT_ROUTE)
        if item["not_before"] > t or route is None or not route.url or route.name in down:
            continue
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(item["created"]))
        first = f"From {when}; Teams couldn't be reached then." if item["attempts"] else f"Held during quiet hours since {when}."
        try:
            post(route.url, f"Delayed: {item['title']}" if item["attempts"] else item["title"], [first] + item["lines"])
        except Exception as exc:  # noqa: BLE001 - still unreachable: keep it
            down[route.name] = failure(exc)
            outbox.failed(item["id"], down[route.name])
            continue
        outbox.done(item["id"])
        sent += 1
    results: list[dict[str, Any]] = []
    for event in events:
        why = decide(event, ledger)
        routes = routing.for_event(event) if why is None else []
        if why is None and not routes:
            why = "no route matches and no default route is set"
        if why:
            results.append({"dedupe": event["dedupe"], "result": "skipped", "reason": why})
            continue
        title, lines = message(event)
        hold = routing.quiet.until(now, event["severity"]) if routing.quiet else None
        outcome = "sent"
        for r in routes:
            if hold:
                outbox.add(title, lines, "", route=r.name, not_before=hold.timestamp())
                held += 1
                outcome = "held"
                continue
            if r.name not in down:
                try:
                    post(r.url, title, lines)
                    sent += 1
                    continue
                except Exception as exc:  # noqa: BLE001
                    down[r.name] = failure(exc)
            outbox.add(title, lines, down[r.name], route=r.name)
            queued += 1
            outcome = "queued" if outcome == "sent" else outcome
        # Queued and held messages are in the outbox and will go out: the key counts as posted.
        ledger.mark(event["dedupe"], event["kind"], event["kind"] in OPENS)
        if event["kind"] in OPENS:  # waiting again after a decision: the next decision is news
            for closer in (k for k, v in CLOSES.items() if v == event["kind"]):
                ledger.forget(closer + event["dedupe"][len(event["kind"]):] if event["dedupe"].startswith(event["kind"])
                              else f"{closer}:{event['id']}")
        if event["kind"] in CLOSES:
            opened = _opened_key(event)
            if ledger.get(opened):
                ledger.mark(opened, CLOSES[event["kind"]], False)
            if event["kind"] == "shortcut.fixed":
                ledger.forget(event["dedupe"])  # the next fix of the next break is news again
                ledger.forget(opened)
        results.append({"dedupe": event["dedupe"], "result": outcome, "routes": [r.name for r in routes]})
    return {"sent": sent, "queued": queued, "held": held, "events": results,
            "error": next(iter(down.values()), None)}
