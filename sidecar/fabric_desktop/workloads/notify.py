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

A message is written to the outbox before it is posted and deleted once Teams takes it, so a process
that stops mid-send (a crash, a kill, the PC losing power) never loses it: the next send after
SENDING_GRACE posts it, marked as delayed and as possibly already in the channel, since a post cut
off on the way may have reached Teams or the inbox. That is the only way one message can go out twice. Until the
grace ends, no other process sends it, so two windows sending at once don't post it twice.

A post that was sent but got no answer (Teams or the flow didn't answer in time, the connection dropped
after the request went, the mail server stopped answering while it took the message) may have arrived.
It is never sent again, so it can't be posted twice, unless the message was kept as ``idempotent`` (its
receiver drops a repeat). It is taken out of the outbox, counted as ``unconfirmed`` and named in
``error``, and audit_trouble gives it an audit line, so it is never lost without a word.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Callable, ContextManager

from .. import jsonfile, redact, sqlitefile
from .evaluate import describe

_log = logging.getLogger(__name__)

DIGEST_OVER = 5
KEEP_TRYING = 3 * 86400  # an alert older than this is dropped from the outbox, not sent
MAX_WAITING = 500  # messages an outbox keeps; beyond this the oldest are dropped (and counted)
MAX_PER_TRY = 25  # waiting messages sent to one route in one call, so a recovery doesn't flood the channel
BACKOFF_FIRST = 60.0  # seconds a route rests after its first failure, doubling with each one after
BACKOFF_MAX = 1800.0  # the longest a failing route rests between tries
CLOCK_MARGIN = 300.0  # a rest ending further off than BACKOFF_MAX plus this was set before the clock went back
DEFAULT_ROUTE = "default"
LINE_MAX = 500  # characters in one title or line of a Teams message
# Longer than any one post can take (Teams: 3 tries of 5 + 15 s with two 10 s waits for throttling;
# email: 20 s), so a message still marked sending after this was cut off by a stop, not slow.
SENDING_GRACE = 600.0
CUT_OFF = "From {when}; sending it was cut off, so it may have arrived already."
UNCONFIRMED = ("{error}. It was sent but no answer came, so it may have arrived. It isn't sent again, "
               "so it can't be posted twice. Check the channel or inbox.")


class OutcomeUnknown(RuntimeError):
    """A post that was sent but got no answer, so it may have arrived. A transport raises it when it can
    tell (the mail server stopped answering while it took the message); outcome_unknown tells the rest."""


def outcome_unknown(exc: BaseException) -> bool:
    """Whether a failed post may still have arrived: it was sent, and no answer came. A post refused or
    never sent (no connection, a connect timeout, any answer such as 429, 403 or 503) is False."""
    if isinstance(exc, OutcomeUnknown):
        return True
    try:
        import requests
        from urllib3.exceptions import ProtocolError
    except ImportError:  # pragma: no cover - both ship with the engine
        return False
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return False  # never connected: nothing was sent
    if isinstance(exc, (requests.exceptions.ReadTimeout, requests.exceptions.ChunkedEncodingError)):
        return True
    # requests wraps a connection dropped after the request went ("Connection aborted") in a
    # ConnectionError whose first argument is urllib3's ProtocolError; one that never connected
    # carries a MaxRetryError instead.
    return isinstance(exc, requests.exceptions.ConnectionError) and bool(exc.args) and isinstance(exc.args[0], ProtocolError)


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


_QUIET = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)-([01]?\d|2[0-3]):([0-5]\d)")  # as agent_config's _QUIET


@dataclass(frozen=True)
class Quiet:
    """Hours (this machine's clock) when messages wait, except for the listed criticalities."""
    start: dt.time
    end: dt.time
    exempt: tuple[str, ...] = ("critical",)

    @staticmethod
    def parse(text: str, exempt: tuple[str, ...] = ("critical",)) -> "Quiet":
        """Two times in hours and minutes, like 22:00-06:00. Anything else (seconds, 24:00, one time) is
        refused with ValueError, as agent.toml refuses it, so every caller reads quiet hours alike."""
        match = _QUIET.fullmatch(text.strip())
        if not match:
            raise ValueError("quiet hours look like 22:00-07:00")
        a, b = (dt.time(int(h), int(m)) for h, m in (match.group(1, 2), match.group(3, 4)))
        return Quiet(a, b, exempt)

    @staticmethod
    def parse_saved(text: str, exempt: tuple[str, ...] = ("critical",)) -> "Quiet":
        """Quiet hours read back from saved settings. Builds before hours-and-minutes were enforced
        saved any ISO times (22:00:00-06:00:30): those still read, seconds kept, with a warning, so
        an old setting never stops notices. Saving them again needs hours and minutes (``parse``)."""
        try:
            return Quiet.parse(text, exempt)
        except ValueError:
            a, b = (dt.time.fromisoformat(x.strip()) for x in text.split("-"))  # ValueError as before
            _log.warning("Quiet hours %r were saved with seconds; read as %s-%s. Save them again as "
                         "hours and minutes, like 22:00-06:00.", text, a.isoformat(), b.isoformat())
            return Quiet(a, b, exempt)

    def until(self, now: dt.datetime, criticality: str) -> dt.datetime | None:
        """When quiet hours end, if ``now`` is inside them and ``criticality`` isn't exempt."""
        if criticality.lower() in {e.lower() for e in self.exempt}:
            return None
        t = now.time()
        inside = (self.start <= t < self.end) if self.start < self.end else (t >= self.start or t < self.end)
        if not inside:
            return None
        end = now.replace(hour=self.end.hour, minute=self.end.minute, second=self.end.second,
                          microsecond=self.end.microsecond)
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


# Messages already posted (or sent with no answer) whose row couldn't be deleted, the outbox file being
# locked, by the outbox's resolved path: shared by every Outbox on that file in this process, and kept in a
# file beside it (GONE_SUFFIX) so a restart doesn't send them again either. Deleted on the next write that works.
# Each kept as id -> its row's created time (None when unknown): a row id SQLite reuses after another process
# deleted the row is a different message, with another created time, and is neither hidden nor deleted.
_GONE: dict[str, dict[int, float | None]] = {}
_GONE_LOCK = threading.Lock()
GONE_SUFFIX = ".sent-not-removed.json"


class Outbox:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._gone_file = self.path.with_name(self.path.name + GONE_SUFFIX)
        fresh = not self.path.exists()
        # A file SQLite can't read is moved aside and the outbox starts empty: messages still waiting
        # in it are kept in the moved file but not sent, and new ones are sent as usual.
        self.set_aside = sqlitefile.open_store(
            self.path, self._first_open,
            then="messages still waiting in it aren't sent; new ones are sent as usual")
        key = str(self.path.resolve())
        with _GONE_LOCK:
            self._gone = _GONE.setdefault(key, {})
            self._created: dict[int, float] = {}  # row id -> created, for rows this Outbox wrote or read
            self._gone_seen: float | None = None  # the kept-ids file's mtime when last read
            if fresh or self.set_aside is not None:  # a new, empty file: ids kept for an old one mean nothing in it
                self._gone.clear()
                self._gone_file.unlink(missing_ok=True)
        self._load_gone()

    def _load_gone(self) -> None:
        """Take in the ids another Outbox (this process or another) kept in the file beside the outbox, when
        it changed since last read. Only (id, created) pairs: one that matches no row hides nothing."""
        try:
            seen = self._gone_file.stat().st_mtime
        except OSError:  # no file: nothing kept on disk
            return
        if seen == self._gone_seen:
            return
        saved = jsonfile.read_json(self._gone_file, [])
        with _GONE_LOCK:
            self._gone_seen = seen
            for pair in saved if isinstance(saved, list) else []:
                if isinstance(pair, list) and len(pair) == 2 and type(pair[0]) is int and type(pair[1]) in (int, float):
                    self._gone[pair[0]] = float(pair[1])

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
            if "sending" not in cols:  # 1 while a post is under way: left at 1 only by a stop mid-send
                db.execute("ALTER TABLE outbox ADD COLUMN sending INTEGER DEFAULT 0")
            if "idempotent" not in cols:  # 1: its receiver drops a repeat, so a post with no answer is sent again
                db.execute("ALTER TABLE outbox ADD COLUMN idempotent INTEGER DEFAULT 0")
            # A failing route's backoff. ``address`` is a fingerprint, never the webhook: a new address is tried at once.
            db.execute("CREATE TABLE IF NOT EXISTS route_state (route TEXT PRIMARY KEY, address TEXT, "
                       "failures INTEGER, next_try REAL, error TEXT)")

    def _db(self) -> ContextManager[sqlite3.Connection]:
        return sqlitefile.transaction(self.path)

    def add(self, title: str, lines: list[str], error: str, route: str = DEFAULT_ROUTE,
            not_before: float = 0.0, db: sqlite3.Connection | None = None, idempotent: bool = False) -> None:
        sql = ("INSERT INTO outbox (created, title, lines, attempts, error, route, not_before, idempotent) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
        values = (self.clock(), title, json.dumps(lines), 1 if error else 0, error[:500], route, not_before,
                  int(idempotent))
        if db is not None:
            db.execute(sql, values)
            return
        with self._db() as conn:
            conn.execute(sql, values)

    def sending(self, title: str, lines: list[str], route: str = DEFAULT_ROUTE,
                db: sqlite3.Connection | None = None, idempotent: bool = False) -> int:
        """Keep a message about to be posted; its id. Call ``done`` once it's posted and ``not_sent`` if it
        fails. Kept until then, so a stop mid-send leaves it to go out SENDING_GRACE later. ``db``: a
        transaction already open on this outbox's file, so the message is kept together with other changes.
        ``idempotent``: its receiver drops a repeat, so a post that gets no answer is sent again."""
        sql = ("INSERT INTO outbox (created, title, lines, attempts, error, route, not_before, sending, idempotent) "
               "VALUES (?, ?, ?, 0, '', ?, ?, 1, ?)")
        values = (self.clock(), title, json.dumps(lines), route, self.clock() + SENDING_GRACE, int(idempotent))
        if db is not None:
            item_id = int(db.execute(sql, values).lastrowid or 0)
        else:
            with self._db() as conn:
                item_id = int(conn.execute(sql, values).lastrowid or 0)
        self._created[item_id] = values[0]
        return item_id

    def not_sent(self, item_id: int, error: str) -> None:
        """A post that failed: the message waits like any other that Teams didn't take. Never raises: on a
        locked file it stays marked as sending, so it goes once more after SENDING_GRACE, as after a stop."""
        try:
            with self._db() as db:
                db.execute("UPDATE outbox SET attempts = attempts + 1, error = ?, not_before = 0, sending = 0 "
                           "WHERE id = ?", (error[:500], item_id))
        except (sqlite3.Error, OSError) as exc:
            _log.warning("Couldn't note a failed post in %s; it's tried again later: %s", self.path.name, failure(exc))

    def pending(self) -> list[dict[str, Any]]:
        self._load_gone()
        self._settle_gone()
        with self._db() as db:
            rows = db.execute("SELECT id, created, title, lines, attempts, error, route, not_before, sending, idempotent "
                              "FROM outbox ORDER BY id").fetchall()
        self._created.update((r[0], r[1]) for r in rows)
        return [{"id": r[0], "created": r[1], "title": r[2], "lines": json.loads(r[3]), "attempts": r[4],
                 "error": r[5], "route": r[6] or DEFAULT_ROUTE, "not_before": r[7] or 0.0, "sending": bool(r[8]),
                 "idempotent": bool(r[9])}
                for r in rows if not self._is_gone(r[0], r[1])]

    def idempotent(self, item_id: int) -> bool:
        """Whether a kept message may be sent again after a post with no answer. False when the outbox
        can't be read: a message is then never sent twice, which is the safe side (at most once)."""
        try:
            with self._db() as db:
                row = db.execute("SELECT idempotent FROM outbox WHERE id = ?", (item_id,)).fetchone()
        except (sqlite3.Error, OSError) as exc:
            _log.warning("Couldn't read %s; a post with no answer isn't sent again: %s", self.path.name, failure(exc))
            return False
        return bool(row and row[0])

    def done(self, item_id: int) -> None:
        """Take out a message that went (or may have). Never raises: when the file is locked, its id is
        kept, in this process and in the GONE_SUFFIX file beside the outbox, so it is never sent again, and
        the row is deleted on the next use that can write. The one case left: the app stops between the post
        and that file's write while the outbox is locked; the message then goes once more, marked as possibly
        sent already (CUT_OFF), as after any stop mid-send."""
        with _GONE_LOCK:
            self._gone[item_id] = self._created.pop(item_id, None)
        if not self._settle_gone():
            self._save_gone()

    def _save_gone(self) -> None:
        with _GONE_LOCK:
            pairs = [[i, c] for i, c in sorted(self._gone.items()) if c is not None]  # on disk, only a full match
        try:
            jsonfile.write_json(self._gone_file, pairs, indent=None)
        except OSError as exc:
            _log.warning("Couldn't keep the sent messages %s still holds in %s; this process won't send them "
                         "again: %s", self.path.name, self._gone_file.name, failure(exc))

    def _settle_gone(self) -> bool:
        """Delete the rows of messages that went; True when none is left to delete."""
        with _GONE_LOCK:
            kept = dict(self._gone)
        if not kept:
            return True
        try:
            with self._db() as db:
                db.executemany("DELETE FROM outbox WHERE id = ? AND (? IS NULL OR created = ?)",
                               [(i, c, c) for i, c in kept.items()])
        except (sqlite3.Error, OSError) as exc:
            _log.warning("Couldn't take %d sent message(s) out of %s yet; they aren't sent again: %s",
                         len(kept), self.path.name, failure(exc))
            return False
        with _GONE_LOCK:
            for i, c in kept.items():
                if i in self._gone and self._gone[i] == c:
                    del self._gone[i]
            left = bool(self._gone)
        if left:
            self._save_gone()
        else:
            try:
                self._gone_file.unlink(missing_ok=True)
            except OSError as exc:  # its ids are gone from the outbox: a stale file only filters ids no row has
                _log.warning("Couldn't remove %s: %s", self._gone_file.name, failure(exc))
        return not left

    def failed(self, item_id: int, error: str) -> None:
        with self._db() as db:
            db.execute("UPDATE outbox SET attempts = attempts + 1, error = ? WHERE id = ?", (error[:500], item_id))

    def count(self) -> int:
        self._settle_gone()
        with self._db() as db:
            rows = db.execute("SELECT id, created FROM outbox").fetchall()
        return sum(1 for r in rows if not self._is_gone(r[0], r[1]))

    def _is_gone(self, item_id: int, created: float) -> bool:
        with _GONE_LOCK:
            if item_id not in self._gone:
                return False
            kept = self._gone[item_id]
        return kept is None or kept == created

    def trim(self) -> int:
        """Remove messages older than KEEP_TRYING, then the oldest beyond MAX_WAITING. How many went."""
        self._settle_gone()
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
        """Count a failure and set when the route is next tried. Returns that wait in seconds. Never raises:
        on a locked file the route rests only for the rest of this send (0 is returned)."""
        try:
            return self._route_failed(route, address, error)
        except (sqlite3.Error, OSError) as exc:
            _log.warning("Couldn't note a failing route in %s: %s", self.path.name, failure(exc))
            return 0.0

    def _route_failed(self, route: str, address: str, error: str) -> float:
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
        """The route took a post: it no longer rests. Never raises: on a locked file it is noted next time."""
        try:
            with self._db() as db:
                db.execute("DELETE FROM route_state WHERE route = ?", (route,))
        except (sqlite3.Error, OSError) as exc:
            _log.warning("Couldn't note a route that works again in %s: %s", self.path.name, failure(exc))


def _fingerprint(address: str) -> str:
    return hashlib.sha256(address.encode()).hexdigest()[:16]


def flush(outbox: Outbox, by_name: Callable[[str], Any], post: Callable[[str, str, list[str]], None],
          down: dict[str, str], failed: dict[str, str], delayed: str, unconfirmed: list[int] | None = None) -> int:
    """Send what waits in ``outbox`` to routes that aren't in ``down``, at most MAX_PER_TRY per
    route. A route that fails goes in ``down`` and ``failed`` and rests. Returns how many went.
    ``delayed`` is the first line of a message that failed before, with ``{when}``. A message sent with
    no answer is taken out unless idempotent (see the module's docstring), its id added to ``unconfirmed``."""
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
        elif item["sending"]:  # a send a stop cut off: it may have reached the channel
            title, first = f"Delayed: {item['title']}", CUT_OFF.format(when=when)
        else:
            title, first = item["title"], f"Held during quiet hours since {when}."
        try:
            post(route.url, title, [first] + item["lines"])
        except Exception as exc:  # noqa: BLE001 - still unreachable: keep it, unless it may have arrived
            _not_taken(outbox, item["id"], item["idempotent"], route, exc, down, failed, unconfirmed)
            continue
        outbox.done(item["id"])
        sent += 1
        per_route[route.name] = per_route.get(route.name, 0) + 1
        if route.name not in ok:
            ok.add(route.name)
            outbox.route_ok(route.name)
    return sent


NOT_DELIVERED = "notice_not_delivered"  # the audit action for a send that failed or dropped messages
_ADDRESS = re.compile(r"(?:flow:)?https?://\S+|/workflows/\S+|mailto:\S+|[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")


def audit_trouble(audit: Any, result: dict[str, Any], outbox: str) -> None:
    """One audit line (NOT_DELIVERED, outcome error) when a send's post failed this time or its outbox
    dropped messages unsent, so neither is only in a log: ``queued``, ``dropped``, ``unconfirmed``
    (sent with no answer, so not sent again) and ``lost`` (couldn't be kept nor posted) say which, and the error is kept without any address. A route resting between tries writes nothing: it sent nothing.
    Never raises: a line that can't be written is logged, and the messages still wait."""
    error, dropped = str(result.get("error") or ""), int(result.get("dropped") or 0)
    unconfirmed, lost = int(result.get("unconfirmed") or 0), int(result.get("lost") or 0)
    if not error and not dropped and not unconfirmed and not lost:
        return
    params = {"outbox": outbox, "queued": int(result.get("queued") or 0), "dropped": dropped,
              "unconfirmed": unconfirmed, "waiting": int(result.get("waiting") or 0)}
    if lost:  # couldn't be kept (a locked outbox) nor posted: never sent
        params["lost"] = lost
    try:
        audit.record(NOT_DELIVERED, "error", 0.0, params,
                     error=_ADDRESS.sub("<address>", redact.secrets(error))[:300], reason="failed")
    except Exception as exc:  # noqa: BLE001 - the audit line is extra: the messages are kept either way
        _log.warning("A notice that wasn't delivered couldn't be recorded in the audit log: %s", type(exc).__name__)


def settle_failed(outbox: Outbox, item_id: int, exc: BaseException, why: str, idempotent: bool | None = None) -> bool:
    """A kept message whose post failed with ``exc``. It waits in the outbox to be sent again (``why`` kept
    with it), unless it was sent with no answer and isn't idempotent: then it may have arrived, so it is
    taken out and True is returned, for the caller to say so (UNCONFIRMED) and record it."""
    if outcome_unknown(exc) and not (outbox.idempotent(item_id) if idempotent is None else idempotent):
        outbox.done(item_id)
        return True
    outbox.not_sent(item_id, why)
    return False


def _not_taken(outbox: Outbox, item_id: int, idempotent: bool | None, route: Any, exc: Exception,
               down: dict[str, str], failed: dict[str, str], unconfirmed: list[int] | None) -> None:
    """A post that failed: settle_failed, and the route rests, in ``down`` and ``failed`` with why. A
    message taken out as unconfirmed has its id put in ``unconfirmed``."""
    why = failure(exc)
    if settle_failed(outbox, item_id, exc, why, idempotent):
        if unconfirmed is not None:
            unconfirmed.append(item_id)
        why = UNCONFIRMED.format(error=why)
    down[route.name] = failed[route.name] = why
    outbox.route_failed(route.name, route.url, why)


def post_kept(outbox: Outbox, item_id: int, route: Any, title: str, lines: list[str],
              post: Callable[[str, str, list[str]], None], down: dict[str, str], failed: dict[str, str],
              unconfirmed: list[int] | None = None) -> bool:
    """Post a message ``Outbox.sending`` kept. True once Teams took it (and it's deleted). False when it
    failed: it waits in the outbox and the route rests, in ``down`` and ``failed`` with why. A post sent
    with no answer isn't kept to send again unless the message is idempotent: its id goes in ``unconfirmed``."""
    try:
        post(route.url, title, lines)
    except Exception as exc:  # noqa: BLE001 - a route that can't be sent to keeps the message and rests
        _not_taken(outbox, item_id, None, route, exc, down, failed, unconfirmed)
        return False
    outbox.done(item_id)
    outbox.route_ok(route.name)
    return True


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


OUTBOX_TROUBLE = "{path} couldn't be used ({error})"
LOST = "{n} notice(s) couldn't be kept to send later, so they weren't sent"
_NOT_KEPT = object()


def send(changes: list[dict[str, Any]], routing: Routing, outbox: Outbox | None, source: str,
         post: Callable[[str, str, list[str]], None], digest_over: int = DIGEST_OVER,
         now: dt.datetime | None = None, idempotent: bool = False) -> dict[str, Any]:
    """Post what these changes need, after anything still waiting. Never raises for an outbox file that is
    busy or broken, nor for a post that fails (a bug still does, so it's seen). ``dropped`` counts
    messages removed unsent (older than KEEP_TRYING, or beyond MAX_WAITING); ``error`` is set only
    when a request was sent and failed this time, not while a route rests, or when the outbox couldn't
    be used; ``waiting`` is what the outbox still holds; ``unconfirmed`` counts posts sent with no answer,
    not sent again. ``idempotent``: these messages' receiver drops a repeat, so one sent with no answer
    waits to be sent again.

    An outbox that can't be used (``None``, or its file locked past SQLite's wait) doesn't lose the new
    messages: each is posted once, straight away, without being kept. One that can't be posted then, or
    held for quiet hours, is counted in ``lost`` and named in ``error``, never sent later. A message already
    posted whose row can't be deleted isn't sent again, by this process or after a restart (``Outbox.done``
    says the one case left)."""
    if not routing.routes:
        return {"sent": 0, "queued": 0, "held": 0, "dropped": 0, "skipped": "no Teams webhook set"}
    now = now or dt.datetime.fromtimestamp(outbox.clock() if outbox is not None else time.time()).astimezone()
    sent = queued = held = lost = direct_unconfirmed = 0
    trouble: list[str] = []

    def kept(step: Callable[[], Any], default: Any = _NOT_KEPT) -> Any:
        """Run one outbox step; on a locked or broken file, note why and return ``default``."""
        try:
            return step()
        except (sqlite3.Error, OSError) as exc:  # a locked or broken outbox file; any other error is a bug and raises
            why = OUTBOX_TROUBLE.format(path=outbox.path.name if outbox is not None else "The outbox", error=failure(exc))
            _log.warning("%s; new messages are posted without being kept", why)
            trouble.append(why)
            return default

    failed: dict[str, str] = {}
    unconfirmed: list[int] = []
    dropped = 0
    down: dict[str, str] | None = None
    if outbox is not None:
        dropped = kept(outbox.trim, 0)  # never sent: the caller logs and counts them
        # route name -> why it isn't tried now: resting after earlier failures, or failed this time
        down = kept(lambda: outbox.resting({r.name: r.url for r in routing.routes}), None)
    usable = down is not None
    down = down or {}
    if usable and outbox is not None:
        sent += kept(lambda: flush(outbox, routing.by_name, post, down, failed,
                                   "From {when}; Teams couldn't be reached then.", unconfirmed), 0)
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
            if usable and outbox is not None and kept(partial(outbox.add, title, lines, "", route=name, not_before=until,
                                                              idempotent=idempotent)) is not _NOT_KEPT:
                held += 1
            else:  # posting it now would break quiet hours, and it can't wait: lost, and said so
                lost += 1
    for name, cs in by_route.items():
        route = routing.by_name(name)
        assert route is not None
        for title, lines in messages(cs, source, digest_over):
            if name in down:
                if usable and outbox is not None and kept(partial(outbox.add, title, lines, down[name], route=name,
                                                                  idempotent=idempotent)) is not _NOT_KEPT:
                    queued += 1
                else:
                    lost += 1
                continue
            item_id = kept(partial(outbox.sending, title, lines, route=name, idempotent=idempotent)) \
                if usable and outbox is not None else _NOT_KEPT
            if outbox is None or not isinstance(item_id, int):  # not kept: posted once now, never again
                try:
                    post(route.url, title, lines)
                except Exception as exc:  # noqa: BLE001 - a route that can't be sent to: counted as lost, logged
                    why = failure(exc)
                    if outcome_unknown(exc):
                        direct_unconfirmed += 1
                        why = UNCONFIRMED.format(error=why)
                    else:
                        lost += 1
                    down[name] = failed[name] = why
                    _log.warning("A notice that couldn't be kept wasn't posted either: %s", why)
                    continue
                sent += 1
                continue
            ok = kept(partial(post_kept, outbox, item_id, route, title, lines, post, down, failed, unconfirmed), None)
            if ok:
                sent += 1
            elif ok is False and item_id not in unconfirmed:
                queued += 1
    waiting = kept(outbox.count, 0) if outbox is not None and usable else 0
    error = next(iter(failed.values()), None) or (trouble[0] if trouble else None)
    if lost and not error:
        error = LOST.format(n=lost)
    out = {"sent": sent, "queued": queued, "held": held, "dropped": dropped,
           "unconfirmed": len(unconfirmed) + direct_unconfirmed, "waiting": waiting, "error": error}
    if lost:
        out["lost"] = lost
    return out
