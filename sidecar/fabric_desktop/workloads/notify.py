"""Telling a Teams channel when incidents open and recover, without flooding it or losing messages.

One message per change; when one evaluation changes more than ``digest_over`` incidents, one
digest instead. A message Teams doesn't take (no network, Teams down) waits in a small SQLite
outbox on this machine and goes out on the next try, marked as delayed with when it happened.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from .evaluate import describe

DIGEST_OVER = 5
KEEP_TRYING = 3 * 86400  # an alert older than this is dropped from the outbox, not sent


class Outbox:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, created REAL, title TEXT, "
                       "lines TEXT, attempts INTEGER DEFAULT 0, error TEXT)")

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def add(self, title: str, lines: list[str], error: str) -> None:
        with self._db() as db:
            db.execute("INSERT INTO outbox (created, title, lines, attempts, error) VALUES (?, ?, ?, 1, ?)",
                       (self.clock(), title, json.dumps(lines), error[:500]))

    def pending(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT id, created, title, lines, attempts, error FROM outbox ORDER BY id").fetchall()
        return [{"id": r[0], "created": r[1], "title": r[2], "lines": json.loads(r[3]), "attempts": r[4],
                 "error": r[5]} for r in rows]

    def done(self, item_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM outbox WHERE id = ?", (item_id,))

    def failed(self, item_id: int, error: str) -> None:
        with self._db() as db:
            db.execute("UPDATE outbox SET attempts = attempts + 1, error = ? WHERE id = ?", (error[:500], item_id))


def messages(changes: list[dict[str, Any]], source: str, digest_over: int = DIGEST_OVER) -> list[tuple[str, list[str]]]:
    """(title, lines) for each Teams message these changes need."""
    if not changes:
        return []
    opened = [c for c in changes if c["change"] != "recovered"]
    recovered = [c for c in changes if c["change"] == "recovered"]
    if len(changes) > digest_over:
        title = (f"{len(opened)} new problem{'s' if len(opened) != 1 else ''}"
                 + (f", {len(recovered)} recovered" if recovered else "") + f" ({source})")
        return [(title, [describe(c) for c in opened + recovered])]
    out = []
    for c in changes:
        inc = c["incident"]
        title = (f"Recovered: {inc.get('name') or inc['env'].title()}" if c["change"] == "recovered"
                 else f"{'Again' if c['change'] == 'reopened' else 'New'}: {inc['title']}")
        out.append((f"{title} ({source})", [describe(c)]))
    return out


def send(changes: list[dict[str, Any]], url: str, outbox: Outbox, source: str,
         post: Callable[[str, str, list[str]], None], digest_over: int = DIGEST_OVER) -> dict[str, Any]:
    """Post what these changes need, after anything still waiting. Never raises."""
    sent, queued = 0, 0
    if not url:
        return {"sent": 0, "queued": 0, "skipped": "no Teams webhook set"}
    now = outbox.clock()
    for item in outbox.pending():
        if now - item["created"] > KEEP_TRYING:
            outbox.done(item["id"])
            continue
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(item["created"]))
        try:
            post(url, f"Delayed: {item['title']}", [f"From {when}; Teams couldn't be reached then."] + item["lines"])
        except Exception as exc:  # noqa: BLE001 - still unreachable: keep it, and keep the new ones too
            outbox.failed(item["id"], f"{type(exc).__name__}: {exc}")
            for title, lines in messages(changes, source, digest_over):
                outbox.add(title, lines, f"{type(exc).__name__}: {exc}")
                queued += 1
            return {"sent": sent, "queued": queued, "error": f"{type(exc).__name__}: {exc}"}
        outbox.done(item["id"])
        sent += 1
    error = None
    for title, lines in messages(changes, source, digest_over):
        try:
            post(url, title, lines)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            outbox.add(title, lines, error)
            queued += 1
    return {"sent": sent, "queued": queued, "error": error}
