"""Results waiting on this PC for a central lakehouse that couldn't be reached (PR-OPT-006).

When a write to the central lakehouse fails for a reason that passes (no network, throttling, a
lost commit race, a dropped upload), what it was writing waits here instead of being lost, and is
sent the next time Optimize writes to that lakehouse. Sending twice is harmless: every row has a
``record_id`` and goes in with an insert-only MERGE, so a batch that was half sent finishes and a
batch that was fully sent adds nothing.

One SQLite file on this PC. An item is one call's input (an analysis run, or local runs), kept
for the lakehouse it was meant for. Bounded: at most ``MAX_ITEMS`` items per lakehouse and none
older than ``MAX_AGE_DAYS``; past either, the oldest go and the count of dropped items is kept and
shown, never silent. Errors are stored with secrets removed. Nothing here talks to Fabric.

Sync states (ESI-105). ``sync_state`` says, per kind and for the lakehouse as a whole, which of
``STATES`` the results are in: **synced** (nothing waiting), **synchronizing** (a send is running
now), **pending** (waiting to try again after throttling or a lost commit race), **offline** (the
lakehouse couldn't be reached), **failed** (the lakehouse refused: no permission, or read-only), or
**conflict** (the lakehouse's schema version doesn't fit this app: newer, or older and missing the
table, so a person has to update the app or upgrade the lakehouse).
Each item keeps the reason of its last failure; the caller classifies the error, this file doesn't.
Dropped items are still counted beside the state.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .. import redact, sqlitefile

_log = logging.getLogger(__name__)

ANALYSIS, LOCAL_EXECUTIONS = "analysis", "local_executions"
KINDS = (ANALYSIS, LOCAL_EXECUTIONS)
MAX_ITEMS = 200
MAX_AGE_DAYS = 30
BACKOFF_SECONDS = 60.0
MAX_BACKOFF_SECONDS = 3600.0
SYNCED, PENDING, SYNCHRONIZING, FAILED, CONFLICT, OFFLINE = (
    "synced", "pending", "synchronizing", "failed", "conflict", "offline")
STATES = (SYNCED, PENDING, SYNCHRONIZING, FAILED, CONFLICT, OFFLINE)
# Why an item is still waiting, worst first: the state of several items is the worst of theirs.
REASONS = (CONFLICT, FAILED, OFFLINE, PENDING)
# A send that started longer ago than this and never said it ended (the app was closed or crashed
# mid-send) no longer counts as running.
STALE_SYNC_SECONDS = 600.0

_SCHEMA = """
create table if not exists outbox (
    id text primary key,
    lakehouse_id text not null,
    kind text not null,
    payload text not null,
    rows integer not null,
    created_at real not null,
    attempts integer not null default 0,
    next_at real not null,
    last_error text,
    reason text
);
create index if not exists outbox_lakehouse on outbox (lakehouse_id, created_at);
create table if not exists outbox_state (
    lakehouse_id text primary key,
    last_sync real,
    last_error text,
    last_error_at real,
    dropped integer not null default 0,
    syncing_since real
);
"""
# Columns added after the first release, for an outbox file an older app created.
_ADDED = (("outbox", "reason", "text"), ("outbox_state", "syncing_since", "real"))


def rows_in(kind: str, payload: dict[str, Any]) -> int:
    if kind == ANALYSIS:
        return 1 + sum(len(payload.get(k) or []) for k in ("artifacts", "findings", "recommendations"))
    return len(payload.get("rows") or [])


class Outbox:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time, max_items: int = MAX_ITEMS,
                 max_age_days: float = MAX_AGE_DAYS):
        self.path = Path(path)
        self.clock = clock
        self.max_items, self.max_age = max(1, int(max_items)), max_age_days * 86400
        # A file SQLite can't read is moved aside and the outbox starts empty, so a damaged file never
        # stops Optimize writing: results waiting in it stay in the moved file but aren't sent. Running
        # the X-ray or sharing runs again records them.
        self.set_aside = sqlitefile.open_store(
            self.path, self._first_open,
            then="results waiting in it for the central lakehouse aren't sent; new ones are kept as usual")

    def _first_open(self) -> None:
        with self._db() as db:
            sqlitefile.check(db)
            db.executescript(_SCHEMA)
            for table, column, kind in _ADDED:
                have = {r[1] for r in db.execute(f"pragma table_info({table})")}
                if column not in have:
                    db.execute(f"alter table {table} add column {column} {kind}")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """One connection per call, closed after it: X-ray writes from its own thread, and on Windows
        an open handle keeps the file locked."""
        with sqlitefile.connect(self.path) as db:  # synchronous=full, like every store
            yield db

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._db() as db:
            db.execute("begin immediate")
            try:
                yield db
            except BaseException:
                db.execute("rollback")
                raise
            db.execute("commit")

    @staticmethod
    def _state(db: sqlite3.Connection, lakehouse_id: str) -> None:
        db.execute("insert or ignore into outbox_state (lakehouse_id) values (?)", (lakehouse_id,))

    def _trim(self, db: sqlite3.Connection, lakehouse_id: str, now: float) -> None:
        """Drop what is too old, then the oldest past the cap, counting each one dropped. Items added
        in the same clock tick (Windows' clock is coarse) are ordered by when they were added (rowid),
        never by their random id."""
        old = db.execute("delete from outbox where lakehouse_id = ? and created_at < ?",
                         (lakehouse_id, now - self.max_age)).rowcount
        over = db.execute(
            "delete from outbox where id in (select id from outbox where lakehouse_id = ? "
            "order by created_at desc, rowid desc limit -1 offset ?)", (lakehouse_id, self.max_items)).rowcount
        if old + over:
            self._state(db, lakehouse_id)
            db.execute("update outbox_state set dropped = dropped + ? where lakehouse_id = ?",
                       (old + over, lakehouse_id))

    def add(self, lakehouse_id: str, kind: str, payload: dict[str, Any], error: str = "",
            reason: str = PENDING) -> str:
        """Keep one call's input for the lakehouse, with why it couldn't be sent (``reason``, one of
        ``REASONS``). Returns the item's id, or "" when everything in it was already waiting."""
        if kind not in KINDS:
            raise ValueError(f"Unknown outbox item kind: {kind}")
        _check_reason(reason)
        if not (lakehouse_id or "").strip():
            raise ValueError("An outbox item needs the lakehouse it is for.")
        now, item = self.clock(), uuid.uuid4().hex
        error_text = redact.secrets(error)[:500] if error else None
        with self._tx() as db:
            # The same thing queued twice (a scan shared again, runs shared again while still offline)
            # waits once, so repeats don't fill the cap and push out other results.
            if kind == ANALYSIS:
                run_id = str((payload.get("run") or {}).get("run_id") or "")
                for old in self._items(db, lakehouse_id, ANALYSIS):
                    if run_id and str((old["payload"].get("run") or {}).get("run_id") or "") == run_id:
                        db.execute("delete from outbox where id = ?", (old["id"],))
            else:
                waiting = {r.get("execution_id") for old in self._items(db, lakehouse_id, LOCAL_EXECUTIONS)
                           for r in old["payload"].get("rows") or []}
                rows = [r for r in payload.get("rows") or [] if r.get("execution_id") not in waiting]
                if not rows:
                    return ""
                payload = payload | {"rows": rows}
            db.execute("insert into outbox (id, lakehouse_id, kind, payload, rows, created_at, attempts, next_at, "
                       "last_error, reason) values (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                       (item, lakehouse_id, kind, json.dumps(payload, default=str), rows_in(kind, payload), now,
                        now + BACKOFF_SECONDS, error_text, reason))
            self._state(db, lakehouse_id)
            if error_text:
                db.execute("update outbox_state set last_error = ?, last_error_at = ? where lakehouse_id = ?",
                           (error_text, now, lakehouse_id))
            self._trim(db, lakehouse_id, now)
        return item

    @staticmethod
    def _items(db: sqlite3.Connection, lakehouse_id: str, kind: str) -> list[dict[str, Any]]:
        rows = db.execute("select id, payload from outbox where lakehouse_id = ? and kind = ?", (lakehouse_id, kind))
        return [{"id": r["id"], "payload": json.loads(r["payload"])} for r in rows.fetchall()]

    def waiting_execution_ids(self, lakehouse_id: str) -> set[str]:
        """Runs on this PC already waiting for the lakehouse, so sharing again doesn't count them as new."""
        with self._db() as db:
            return {str(r.get("execution_id")) for old in self._items(db, lakehouse_id, LOCAL_EXECUTIONS)
                    for r in old["payload"].get("rows") or []}

    def due(self, lakehouse_id: str, include_waiting: bool = False) -> list[dict[str, Any]]:
        """Items for the lakehouse, oldest first; only those whose wait is over unless ``include_waiting``."""
        now = self.clock()
        with self._tx() as db:
            self._trim(db, lakehouse_id, now)
            rows = db.execute("select * from outbox where lakehouse_id = ? and (? or next_at <= ?) "
                              "order by created_at, rowid", (lakehouse_id, int(include_waiting), now)).fetchall()
        return [{"id": r["id"], "kind": r["kind"], "payload": json.loads(r["payload"]), "rows": r["rows"],
                 "attempts": r["attempts"]} for r in rows]

    def sent(self, item_id: str) -> None:
        with self._tx() as db:
            row = db.execute("select lakehouse_id from outbox where id = ?", (item_id,)).fetchone()
            db.execute("delete from outbox where id = ?", (item_id,))
            if row:
                self._state(db, row["lakehouse_id"])
                # A send that worked means the lakehouse is reachable again: the last error is over.
                db.execute("update outbox_state set last_sync = ?, last_error = null, last_error_at = null "
                           "where lakehouse_id = ?",
                           (self.clock(), row["lakehouse_id"]))

    def failed(self, item_id: str, error: str, reason: str = PENDING) -> None:
        """Try the item again later, waiting longer each time. ``reason`` is why it failed this time."""
        _check_reason(reason)
        now, error_text = self.clock(), redact.secrets(error)[:500]
        with self._tx() as db:
            row = db.execute("select lakehouse_id, attempts from outbox where id = ?", (item_id,)).fetchone()
            if not row:
                return
            wait = min(MAX_BACKOFF_SECONDS, BACKOFF_SECONDS * 2 ** min(row["attempts"], 16))
            db.execute("update outbox set attempts = attempts + 1, next_at = ?, last_error = ?, reason = ? "
                       "where id = ?", (now + wait, error_text, reason, item_id))
            self._state(db, row["lakehouse_id"])
            db.execute("update outbox_state set last_error = ?, last_error_at = ? where lakehouse_id = ?",
                       (error_text, now, row["lakehouse_id"]))

    def dropped(self, item_id: str, error: str) -> None:
        """Give up on an item that can never be sent as it is (the lakehouse refused its contents)."""
        now, reason = self.clock(), redact.secrets(error)[:500]
        with self._tx() as db:
            row = db.execute("select lakehouse_id from outbox where id = ?", (item_id,)).fetchone()
            if not row:
                return
            db.execute("delete from outbox where id = ?", (item_id,))
            self._state(db, row["lakehouse_id"])
            db.execute("update outbox_state set dropped = dropped + 1, last_error = ?, last_error_at = ? "
                       "where lakehouse_id = ?", (reason, now, row["lakehouse_id"]))

    def discard(self, lakehouse_id: str) -> int:
        """Remove everything waiting for the lakehouse on this PC; the lakehouse itself is untouched."""
        with self._tx() as db:
            n = db.execute("delete from outbox where lakehouse_id = ?", (lakehouse_id,)).rowcount
            if n:
                self._state(db, lakehouse_id)
                db.execute("update outbox_state set dropped = dropped + ? where lakehouse_id = ?", (n, lakehouse_id))
        return n

    def status(self, lakehouse_id: str) -> dict[str, Any]:
        """What is waiting for the lakehouse, when it last sent, the last error and how many were dropped."""
        with self._db() as db:
            count = db.execute("select count(*) as items, coalesce(sum(rows), 0) as rows, min(created_at) as oldest "
                               "from outbox where lakehouse_id = ?", (lakehouse_id,)).fetchone()
            state = db.execute("select * from outbox_state where lakehouse_id = ?", (lakehouse_id,)).fetchone()
        return {"pending": count["items"], "pending_rows": count["rows"], "oldest": _iso(count["oldest"]),
                "last_sync": _iso(state["last_sync"]) if state else None,
                "last_error": state["last_error"] if state else None,
                "last_error_at": _iso(state["last_error_at"]) if state else None,
                "dropped": state["dropped"] if state else 0}

    @contextmanager
    def syncing(self, lakehouse_id: str) -> Iterator[None]:
        """Mark a send to the lakehouse as running while the block runs, so ``sync_state`` says
        synchronizing. A mark left by a send that never ended goes stale after STALE_SYNC_SECONDS.
        There is one mark per lakehouse: if two sends overlap, the first to end clears it. Clearing
        it never hides the send's own error: a failure to clear is logged and the mark goes stale."""
        with self._tx() as db:
            self._state(db, lakehouse_id)
            db.execute("update outbox_state set syncing_since = ? where lakehouse_id = ?",
                       (self.clock(), lakehouse_id))
        try:
            yield
        finally:
            try:
                with self._tx() as db:
                    db.execute("update outbox_state set syncing_since = null where lakehouse_id = ?",
                               (lakehouse_id,))
            except sqlite3.Error as exc:
                _log.warning("Couldn't clear the sync mark for a lakehouse; it goes stale on its own: %s", exc)

    def sync_state(self, lakehouse_id: str) -> dict[str, Any]:
        """The lakehouse's sync state and each kind's (see the module docstring), with what is
        waiting per kind and the dropped count. Reads only."""
        with self._db() as db:
            rows = db.execute("select kind, reason, count(*) as items, coalesce(sum(rows), 0) as rows "
                              "from outbox where lakehouse_id = ? group by kind, reason", (lakehouse_id,)).fetchall()
            state = db.execute("select * from outbox_state where lakehouse_id = ?", (lakehouse_id,)).fetchone()
        kinds: dict[str, dict[str, Any]] = {k: {"state": SYNCED, "pending": 0, "pending_rows": 0} for k in KINDS}
        for r in rows:
            k = kinds.setdefault(r["kind"], {"state": SYNCED, "pending": 0, "pending_rows": 0})
            k["pending"] += r["items"]
            k["pending_rows"] += r["rows"]
            # An item an older app queued has no reason: it is waiting to be tried again.
            k["state"] = _worst(k["state"], r["reason"] if r["reason"] in REASONS else PENDING)
        since = state["syncing_since"] if state else None
        running = since is not None and self.clock() - since < STALE_SYNC_SECONDS
        overall = SYNCED
        for k in kinds.values():
            overall = _worst(overall, k["state"])
        return {"state": SYNCHRONIZING if running else overall, "waiting_state": overall, "kinds": kinds,
                "syncing_since": _iso(since) if running else None,
                "last_sync": _iso(state["last_sync"]) if state else None,
                "dropped": state["dropped"] if state else 0}


def _check_reason(reason: str) -> None:
    if reason not in REASONS:
        raise ValueError(f"Unknown outbox reason: {reason}")


def _worst(a: str, b: str) -> str:
    """The worse of two states: a reason beats synced, and earlier REASONS beat later ones."""
    rank = {r: i for i, r in enumerate(REASONS)}
    return min(a, b, key=lambda s: rank.get(s, len(rank)))


def _iso(at: float | None) -> str | None:
    if at is None:
        return None
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat(timespec="seconds")
