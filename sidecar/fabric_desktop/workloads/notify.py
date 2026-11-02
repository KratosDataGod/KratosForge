"""Telling Teams channels when incidents open, recover or escalate, without flooding them or losing messages.

Each change goes to the routes that match it (a team's channel, critical incidents to on-call)
and to the default route when none does. One message per change; when one evaluation sends more
than ``digest_over`` to a route, one digest instead. During a route's quiet hours, messages wait
until the hours end unless the incident's criticality is exempt. A message Teams doesn't take
(no network, Teams down) waits in a small SQLite outbox on this machine and goes out on the next
try, marked as delayed with when it happened.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterator

from .. import redact
from .evaluate import describe

DIGEST_OVER = 5
KEEP_TRYING = 3 * 86400  # an alert older than this is dropped from the outbox, not sent
DEFAULT_ROUTE = "default"
LINE_MAX = 500  # characters in one title or line of a Teams message


@dataclass(frozen=True)
class Route:
    """Where a set of incidents is posted. An empty filter matches everything."""
    name: str
    url: str
    teams: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    criticality: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    envs: tuple[str, ...] = ()

    def matches(self, inc: dict[str, Any]) -> bool:
        def ok(allowed: tuple[str, ...], value: Any) -> bool:
            return not allowed or str(value or "").lower() in {a.lower() for a in allowed}
        return (ok(self.teams, inc.get("team")) and ok(self.owners, inc.get("owner"))
                and ok(self.criticality, inc.get("criticality")) and ok(self.kinds, inc.get("kind"))
                and ok(self.envs, inc.get("env")))


@dataclass(frozen=True)
class Quiet:
    """Hours (this machine's clock) when messages wait, except for the listed criticalities."""
    start: dt.time
    end: dt.time
    exempt: tuple[str, ...] = ("critical",)

    @staticmethod
    def parse(text: str, exempt: tuple[str, ...] = ("critical",)) -> "Quiet":
        a, b = (dt.time.fromisoformat(x.strip()) for x in text.split("-"))
        return Quiet(a, b, exempt)

    def until(self, now: dt.datetime, criticality: str) -> dt.datetime | None:
        """When quiet hours end, if ``now`` is inside them and ``criticality`` isn't exempt."""
        if criticality.lower() in {e.lower() for e in self.exempt}:
            return None
        t = now.time()
        inside = (self.start <= t < self.end) if self.start < self.end else (t >= self.start or t < self.end)
        if not inside:
            return None
        end = now.replace(hour=self.end.hour, minute=self.end.minute, second=0, microsecond=0)
        return end if end > now else end + dt.timedelta(days=1)


@dataclass
class Routing:
    routes: list[Route] = field(default_factory=list)
    quiet: Quiet | None = None
    escalate_after: float = 0.0  # seconds; 0 = never
    escalate_to: str = ""        # a route name

    def by_name(self, name: str) -> Route | None:
        return next((r for r in self.routes if r.name == name), None)

    def for_change(self, change: dict[str, Any]) -> list[Route]:
        """The routes a change goes to: an escalation to its escalation route, anything else to
        every matching team route, or to the default one when none matches."""
        inc = change["incident"]
        if change["change"] == "escalated":
            r = self.by_name(inc.get("escalated_to") or self.escalate_to)
            return [r] if r and r.url else []
        named = [r for r in self.routes if r.name not in (DEFAULT_ROUTE, self.escalate_to) and r.url and r.matches(inc)]
        if named:
            return named
        default = self.by_name(DEFAULT_ROUTE)
        return [default] if default and default.url else []


def failure(exc: Exception) -> str:
    """A failed post as text, without the webhook's signature: requests puts the URL in its errors."""
    return redact.secrets(f"{type(exc).__name__}: {exc}")


def single(url: str) -> Routing:
    """Every change to one webhook: the app's Teams setting, or an agent without routes."""
    return Routing([Route(DEFAULT_ROUTE, url)] if url else [])


@contextmanager
def transaction(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection to the SQLite file at path: commits on success, rolls back on error, then closes.

    ``with sqlite3.connect(...)`` alone commits or rolls back but leaves the connection open, and an
    open handle keeps the file locked on Windows."""
    db = sqlite3.connect(path, timeout=30)
    try:
        with db:
            yield db
    finally:
        db.close()


class Outbox:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, created REAL, title TEXT, "
                       "lines TEXT, attempts INTEGER DEFAULT 0, error TEXT)")
            cols = {r[1] for r in db.execute("PRAGMA table_info(outbox)")}
            if "route" not in cols:  # outboxes written before routes held one route's messages
                db.execute(f"ALTER TABLE outbox ADD COLUMN route TEXT DEFAULT '{DEFAULT_ROUTE}'")
            if "not_before" not in cols:
                db.execute("ALTER TABLE outbox ADD COLUMN not_before REAL DEFAULT 0")

    def _db(self) -> ContextManager[sqlite3.Connection]:
        return transaction(self.path)

    def add(self, title: str, lines: list[str], error: str, route: str = DEFAULT_ROUTE,
            not_before: float = 0.0) -> None:
        with self._db() as db:
            db.execute("INSERT INTO outbox (created, title, lines, attempts, error, route, not_before) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (self.clock(), title, json.dumps(lines), 1 if error else 0, error[:500], route, not_before))

    def pending(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT id, created, title, lines, attempts, error, route, not_before "
                              "FROM outbox ORDER BY id").fetchall()
        return [{"id": r[0], "created": r[1], "title": r[2], "lines": json.loads(r[3]), "attempts": r[4],
                 "error": r[5], "route": r[6] or DEFAULT_ROUTE, "not_before": r[7] or 0.0} for r in rows]

    def done(self, item_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM outbox WHERE id = ?", (item_id,))

    def failed(self, item_id: int, error: str) -> None:
        with self._db() as db:
            db.execute("UPDATE outbox SET attempts = attempts + 1, error = ? WHERE id = ?", (error[:500], item_id))


def messages(changes: list[dict[str, Any]], source: str, digest_over: int = DIGEST_OVER) -> list[tuple[str, list[str]]]:
    """(title, lines) for each Teams message these changes need, each one line with anything
    credential-shaped removed (a run's message is the error as it came), so neither the channel
    nor the outbox ever holds a secret."""
    return [(redact.one_line(title, LINE_MAX), [redact.one_line(line, LINE_MAX) for line in lines])
            for title, lines in _messages(changes, source, digest_over)]


def _messages(changes: list[dict[str, Any]], source: str, digest_over: int) -> list[tuple[str, list[str]]]:
    if not changes:
        return []
    opened = [c for c in changes if c["change"] in ("opened", "reopened")]
    escalated = [c for c in changes if c["change"] == "escalated"]
    recovered = [c for c in changes if c["change"] == "recovered"]
    if len(changes) > digest_over:
        parts = [f"{len(opened)} new problem{'s' if len(opened) != 1 else ''}"]
        if escalated:
            parts.append(f"{len(escalated)} escalated")
        if recovered:
            parts.append(f"{len(recovered)} recovered")
        return [(", ".join(parts) + f" ({source})", [describe(c) for c in escalated + opened + recovered])]
    out = []
    for c in changes:
        inc = c["incident"]
        if c["change"] == "recovered":
            title = f"Recovered: {inc.get('name') or inc['env'].title()}"
        elif c["change"] == "escalated":
            title = f"Escalated: {inc['title']}"
        else:
            title = f"{'Again' if c['change'] == 'reopened' else 'New'}: {inc['title']}"
        out.append((f"{title} ({source})", [describe(c)]))
    return out


def send(changes: list[dict[str, Any]], routing: Routing, outbox: Outbox, source: str,
         post: Callable[[str, str, list[str]], None], digest_over: int = DIGEST_OVER,
         now: dt.datetime | None = None) -> dict[str, Any]:
    """Post what these changes need, after anything still waiting. Never raises."""
    if not routing.routes:
        return {"sent": 0, "queued": 0, "held": 0, "skipped": "no Teams webhook set"}
    now = now or dt.datetime.fromtimestamp(outbox.clock()).astimezone()
    t = outbox.clock()
    sent = queued = held = 0
    down: dict[str, str] = {}  # route name -> the error it gave this time
    for item in outbox.pending():
        if t - item["created"] > KEEP_TRYING:
            outbox.done(item["id"])
            continue
        if item["not_before"] > t:
            continue
        route = routing.by_name(item["route"]) or routing.by_name(DEFAULT_ROUTE)
        if route is None or not route.url:
            continue  # its route was removed and there is no default: keep it until one exists
        if route.name in down:
            continue
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(item["created"]))
        if item["attempts"]:
            title, first = f"Delayed: {item['title']}", f"From {when}; Teams couldn't be reached then."
        else:
            title, first = item["title"], f"Held during quiet hours since {when}."
        try:
            post(route.url, title, [first] + item["lines"])
        except Exception as exc:  # noqa: BLE001 - still unreachable: keep it
            down[route.name] = failure(exc)
            outbox.failed(item["id"], down[route.name])
            continue
        outbox.done(item["id"])
        sent += 1
    by_route: dict[str, list[dict[str, Any]]] = {}
    later: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for c in changes:
        hold = routing.quiet.until(now, str(c["incident"].get("criticality") or "")) if routing.quiet else None
        for r in routing.for_change(c):
            if hold and c["change"] != "escalated":
                later.setdefault((r.name, hold.timestamp()), []).append(c)
            else:
                by_route.setdefault(r.name, []).append(c)
    for (name, until), cs in later.items():
        for title, lines in messages(cs, source, digest_over):
            outbox.add(title, lines, "", route=name, not_before=until)
            held += 1
    error = None
    for name, cs in by_route.items():
        route = routing.by_name(name)
        assert route is not None
        for title, lines in messages(cs, source, digest_over):
            if name in down:
                outbox.add(title, lines, down[name], route=name)
                queued += 1
                continue
            try:
                post(route.url, title, lines)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                down[name] = error = failure(exc)
                outbox.add(title, lines, error, route=name)
                queued += 1
    return {"sent": sent, "queued": queued, "held": held, "error": error or next(iter(down.values()), None)}
