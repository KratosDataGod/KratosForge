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
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .. import redact

ANALYSIS, LOCAL_EXECUTIONS = "analysis", "local_executions"
KINDS = (ANALYSIS, LOCAL_EXECUTIONS)
MAX_ITEMS = 200
MAX_AGE_DAYS = 30
BACKOFF_SECONDS = 60.0
MAX_BACKOFF_SECONDS = 3600.0

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
    last_error text
);
create index if not exists outbox_lakehouse on outbox (lakehouse_id, created_at);
create table if not exists outbox_state (
    lakehouse_id text primary key,
    last_sync real,
    last_error text,
    last_error_at real,
    dropped integer not null default 0
);
"""


def rows_in(kind: str, payload: dict[str, Any]) -> int:
    if kind == ANALYSIS:
        return 1 + sum(len(payload.get(k) or []) for k in ("artifacts", "findings", "recommendations"))
    return len(payload.get("rows") or [])


class Outbox:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time, max_items: int = MAX_ITEMS,
                 max_age_days: float = MAX_AGE_DAYS):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.max_items, self.max_age = max(1, int(max_items)), max_age_days * 86400
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """One connection per call, closed after it: X-ray writes from its own thread, and on Windows
        an open handle keeps the file locked."""
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("pragma synchronous=full")
            yield db
        finally:
            db.close()

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
        """Drop what is too old, then the oldest past the cap, counting each one dropped."""
        old = db.execute("delete from outbox where lakehouse_id = ? and created_at < ?",
                         (lakehouse_id, now - self.max_age)).rowcount
        over = db.execute(
            "delete from outbox where id in (select id from outbox where lakehouse_id = ? "
            "order by created_at desc, id desc limit -1 offset ?)", (lakehouse_id, self.max_items)).rowcount
        if old + over:
            self._state(db, lakehouse_id)
            db.execute("update outbox_state set dropped = dropped + ? where lakehouse_id = ?",
                       (old + over, lakehouse_id))

    def add(self, lakehouse_id: str, kind: str, payload: dict[str, Any], error: str = "") -> str:
        """Keep one call's input for the lakehouse, with why it couldn't be sent."""
        if kind not in KINDS:
            raise ValueError(f"Unknown outbox item kind: {kind}")
        if not (lakehouse_id or "").strip():
            raise ValueError("An outbox item needs the lakehouse it is for.")
        now, item = self.clock(), uuid.uuid4().hex
        reason = redact.secrets(error)[:500] if error else None
        with self._tx() as db:
            db.execute("insert into outbox (id, lakehouse_id, kind, payload, rows, created_at, attempts, next_at, "
                       "last_error) values (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                       (item, lakehouse_id, kind, json.dumps(payload, default=str), rows_in(kind, payload), now,
                        now + BACKOFF_SECONDS, reason))
            self._state(db, lakehouse_id)
            if reason:
                db.execute("update outbox_state set last_error = ?, last_error_at = ? where lakehouse_id = ?",
                           (reason, now, lakehouse_id))
            self._trim(db, lakehouse_id, now)
        return item

    def due(self, lakehouse_id: str, include_waiting: bool = False) -> list[dict[str, Any]]:
        """Items for the lakehouse, oldest first; only those whose wait is over unless ``include_waiting``."""
        now = self.clock()
        with self._tx() as db:
            self._trim(db, lakehouse_id, now)
            rows = db.execute("select * from outbox where lakehouse_id = ? and (? or next_at <= ?) "
                              "order by created_at, id", (lakehouse_id, int(include_waiting), now)).fetchall()
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

    def failed(self, item_id: str, error: str) -> None:
        """Try the item again later, waiting longer each time."""
        now, reason = self.clock(), redact.secrets(error)[:500]
        with self._tx() as db:
            row = db.execute("select lakehouse_id, attempts from outbox where id = ?", (item_id,)).fetchone()
            if not row:
                return
            wait = min(MAX_BACKOFF_SECONDS, BACKOFF_SECONDS * 2 ** min(row["attempts"], 16))
            db.execute("update outbox set attempts = attempts + 1, next_at = ?, last_error = ? where id = ?",
                       (now + wait, reason, item_id))
            self._state(db, row["lakehouse_id"])
            db.execute("update outbox_state set last_error = ?, last_error_at = ? where lakehouse_id = ?",
                       (reason, now, row["lakehouse_id"]))

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


def _iso(at: float | None) -> str | None:
    if at is None:
        return None
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat(timespec="seconds")
