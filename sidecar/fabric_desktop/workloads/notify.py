"""Telling Teams channels when incidents open, recover or escalate, without flooding them or losing messages.

Each change goes to the routes that match it (a team's channel, critical incidents to on-call)
and to the default route when none does. One message per change; when one evaluation sends more
than ``digest_over`` to a route, one digest instead. During a route's quiet hours, messages wait
until the hours end unless the incident's criticality is exempt. A message Teams doesn't take
(no network, Teams down) waits in a small SQLite outbox on this machine and goes out on the next
try, marked as delayed with when it happened.

When a route keeps failing (Teams down, throttled or slow), it rests between tries: a minute after
the first failure, doubling to at most BACKOFF_MAX. While it rests, its messages wait in the outbox
without a request being sent. An outbox keeps at most MAX_WAITING messages and none older than
KEEP_TRYING; anything removed unsent is counted as ``dropped`` for the caller to log. On
recovery, at most MAX_PER_TRY waiting messages go to a route in one call, the rest with the next.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ContextManager

from .. import redact, sqlitefile
from .evaluate import describe

DIGEST_OVER = 5
KEEP_TRYING = 3 * 86400  # an alert older than this is dropped from the outbox, not sent
MAX_WAITING = 500  # messages an outbox keeps; beyond this the oldest are dropped (and counted)
MAX_PER_TRY = 25  # waiting messages sent to one route in one call, so a recovery doesn't flood the channel
BACKOFF_FIRST = 60.0  # seconds a route rests after its first failure, doubling with each one after
BACKOFF_MAX = 1800.0  # the longest a failing route rests between tries
CLOCK_MARGIN = 300.0  # a rest ending further off than BACKOFF_MAX plus this was set before the clock went back
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


class Outbox:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A file SQLite can't read is moved aside and the outbox starts empty: messages still waiting
        # in it are kept in the moved file but not sent, and new ones are sent as usual.
        self.set_aside = sqlitefile.open_store(
            self.path, self._first_open,
            then="messages still waiting in it aren't sent; new ones are sent as usual")

    def _first_open(self) -> None:
        with self._db() as db:
            sqlitefile.check(db)
            db.execute("CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, created REAL, title TEXT, "
                       "lines TEXT, attempts INTEGER DEFAULT 0, error TEXT)")
            cols = {r[1] for r in db.execute("PRAGMA table_info(outbox)")}
            if "route" not in cols:  # outboxes written before routes held one route's messages
                db.execute(f"ALTER TABLE outbox ADD COLUMN route TEXT DEFAULT '{DEFAULT_ROUTE}'")
            if "not_before" not in cols:
                db.execute("ALTER TABLE outbox ADD COLUMN not_before REAL DEFAULT 0")
            # A failing route's backoff. ``address`` is a fingerprint, never the webhook: a new address is tried at once.
            db.execute("CREATE TABLE IF NOT EXISTS route_state (route TEXT PRIMARY KEY, address TEXT, "
                       "failures INTEGER, next_try REAL, error TEXT)")

    def _db(self) -> ContextManager[sqlite3.Connection]:
        return sqlitefile.transaction(self.path)

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

    def count(self) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def trim(self) -> int:
        """Remove messages older than KEEP_TRYING, then the oldest beyond MAX_WAITING. How many went."""
        with self._db() as db:
            gone = db.execute("DELETE FROM outbox WHERE created < ?", (self.clock() - KEEP_TRYING,)).rowcount
            gone += db.execute("DELETE FROM outbox WHERE id NOT IN (SELECT id FROM outbox ORDER BY id DESC LIMIT ?)",
                               (MAX_WAITING,)).rowcount
        return int(gone)

    def resting(self, addresses: dict[str, str]) -> dict[str, str]:
        """Routes resting after failures, with their last error: route name -> error. A route
        whose address changed since it failed isn't resting, and neither is one whose next try is
        further off than BACKOFF_MAX (set before the clock went back)."""
        t = self.clock()
        with self._db() as db:
            rows = db.execute("SELECT route, address, next_try, error FROM route_state").fetchall()
        return {r[0]: r[3] or "" for r in rows
                if r[0] in addresses and r[1] == _fingerprint(addresses[r[0]]) and t < r[2] <= t + BACKOFF_MAX + CLOCK_MARGIN}

    def route_failed(self, route: str, address: str, error: str) -> float:
        """Count a failure and set when the route is next tried. Returns that wait in seconds."""
        fp = _fingerprint(address)
        with self._db() as db:
            row = db.execute("SELECT address, failures FROM route_state WHERE route = ?", (route,)).fetchone()
            failures = (row[1] if row and row[0] == fp else 0) + 1
            wait = min(BACKOFF_FIRST * 2 ** min(failures - 1, 20), BACKOFF_MAX)
            db.execute("INSERT INTO route_state (route, address, failures, next_try, error) VALUES (?, ?, ?, ?, ?) "
                       "ON CONFLICT(route) DO UPDATE SET address = excluded.address, failures = excluded.failures, "
                       "next_try = excluded.next_try, error = excluded.error",
                       (route, fp, failures, self.clock() + wait, error[:500]))
        return wait

    def route_ok(self, route: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM route_state WHERE route = ?", (route,))


def _fingerprint(address: str) -> str:
    return hashlib.sha256(address.encode()).hexdigest()[:16]


def flush(outbox: Outbox, by_name: Callable[[str], Any], post: Callable[[str, str, list[str]], None],
          down: dict[str, str], failed: dict[str, str], delayed: str) -> int:
    """Send what waits in ``outbox`` to routes that aren't in ``down``, at most MAX_PER_TRY per
    route. A route that fails goes in ``down`` and ``failed`` and rests. Returns how many went.
    ``delayed`` is the first line of a message that failed before, with ``{when}``."""
    t, sent = outbox.clock(), 0
    per_route: dict[str, int] = {}
    ok: set[str] = set()
    for item in outbox.pending():
        if item["not_before"] > t:
            continue
        route = by_name(item["route"]) or by_name(DEFAULT_ROUTE)
        if route is None or not route.url:
            continue  # its route was removed and there is no default: keep it until one exists
        if route.name in down or per_route.get(route.name, 0) >= MAX_PER_TRY:
            continue
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(item["created"]))
        if item["attempts"]:
            title, first = f"Delayed: {item['title']}", delayed.format(when=when)
        else:
            title, first = item["title"], f"Held during quiet hours since {when}."
        try:
            post(route.url, title, [first] + item["lines"])
        except Exception as exc:  # noqa: BLE001 - still unreachable: keep it
            down[route.name] = failed[route.name] = failure(exc)
            outbox.failed(item["id"], down[route.name])
            outbox.route_failed(route.name, route.url, down[route.name])
            continue
        outbox.done(item["id"])
        sent += 1
        per_route[route.name] = per_route.get(route.name, 0) + 1
        if route.name not in ok:
            ok.add(route.name)
            outbox.route_ok(route.name)
    return sent


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
    """Post what these changes need, after anything still waiting. Never raises. ``dropped`` counts
    messages removed unsent (older than KEEP_TRYING, or beyond MAX_WAITING); ``error`` is set only
    when a request was sent and failed this time, not while a route rests; ``waiting`` is what
    the outbox still holds."""
    if not routing.routes:
        return {"sent": 0, "queued": 0, "held": 0, "dropped": 0, "skipped": "no Teams webhook set"}
    now = now or dt.datetime.fromtimestamp(outbox.clock()).astimezone()
    sent = queued = held = 0
    dropped = outbox.trim()  # never sent: the caller logs and counts them
    # route name -> why it isn't tried now: resting after earlier failures, or failed this time
    down = outbox.resting({r.name: r.url for r in routing.routes})
    failed: dict[str, str] = {}
    sent += flush(outbox, routing.by_name, post, down, failed, "From {when}; Teams couldn't be reached then.")
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
                outbox.route_ok(name)
            except Exception as exc:  # noqa: BLE001
                down[name] = failed[name] = failure(exc)
                outbox.add(title, lines, down[name], route=name)
                outbox.route_failed(name, route.url, down[name])
                queued += 1
    return {"sent": sent, "queued": queued, "held": held, "dropped": dropped, "waiting": outbox.count(),
            "error": next(iter(failed.values()), None)}
