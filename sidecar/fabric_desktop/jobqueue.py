"""The agent's durable job queue: SQLite on the agent's own disk.

A job moves queued -> running -> succeeded | failed | dead. ``running`` is a lease: the worker
that claimed it must renew ``lease_until`` while it works. When a worker dies (crash, reboot,
killed service), its lease runs out and :meth:`JobQueue.recover` puts the job back in the queue,
so it runs again rather than staying "running" forever. A job that fails with an error worth
retrying waits ``backoff * 2^(attempt-1)`` (capped) and runs again until ``max_attempts``; after
that it is ``dead`` and needs a person. A job whose tables failed their checks is ``failed``:
running it again would fail the same way. A queued job can be ``cancelled`` instead.

A person can :meth:`JobQueue.requeue` a ``dead`` or ``failed`` job once they've fixed the cause, and
:meth:`JobQueue.prune` removes finished jobs once they're old, so the file doesn't grow forever.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

STATES = ("queued", "running", "succeeded", "failed", "dead", "cancelled")
ACTIVE = ("queued", "running")
PRUNABLE = ("succeeded", "failed", "cancelled")  # dead jobs stay until a person requeues them

_SCHEMA = """
create table if not exists jobs (
    id text primary key,
    kind text not null,
    payload text not null,
    state text not null,
    source text not null default '',
    dedupe_key text,
    attempts integer not null default 0,
    max_attempts integer not null,
    next_at real not null,
    owner text,
    lease_until real,
    created_at real not null,
    started_at real,
    finished_at real,
    result text,
    error text
);
create index if not exists jobs_ready on jobs (state, next_at);
create table if not exists schedule_state (
    id text primary key,
    next_at real not null
);
"""


class JobQueue:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time,
                 backoff_seconds: float = 60, max_backoff_seconds: float = 1800):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.backoff_seconds, self.max_backoff_seconds = backoff_seconds, max_backoff_seconds
        with self._db() as db:
            db.execute("pragma journal_mode=wal")  # kept in the file, so later connections use it too
            db.executescript(_SCHEMA)  # executescript commits on its own
            if "requeued_by" not in {r["name"] for r in db.execute("pragma table_info(jobs)")}:
                try:  # a queue file from before requeue
                    db.execute("alter table jobs add column requeued_by text")
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc):  # another process added it first
                        raise

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """A connection for one read or change, closed after it. One kept per thread was never closed
        when its thread ended, and on Windows an open handle keeps the queue file locked."""
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("pragma synchronous=full")  # a job that says succeeded stays succeeded after a power cut
            yield db
        finally:
            db.close()

    def _rows(self, sql: str, args: Any = ()) -> list[sqlite3.Row]:
        with self._db() as db:
            return db.execute(sql, args).fetchall()

    def _one(self, sql: str, args: Any = ()) -> sqlite3.Row | None:
        rows = self._rows(sql, args)
        return rows[0] if rows else None

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._db() as db:
            db.execute("begin immediate")  # one writer at a time, also across processes
            try:
                yield db
            except BaseException:
                db.execute("rollback")
                raise
            db.execute("commit")

    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
        if r is None:
            return None
        out = dict(r)
        out["payload"] = json.loads(out["payload"])
        out["result"] = json.loads(out["result"]) if out["result"] else None
        return out

    # ---- adding work

    def enqueue(self, kind: str, payload: dict[str, Any], source: str = "", max_attempts: int = 3,
                dedupe_key: str | None = None, delay: float = 0) -> dict[str, Any]:
        """Add a job. With ``dedupe_key``, a job with the same key that is still queued or running is
        returned instead, so a schedule that fires while its last run is going doesn't pile up."""
        now = self.clock()
        with self._tx() as db:
            if dedupe_key:
                same = db.execute("select * from jobs where dedupe_key = ? and state in ('queued', 'running')",
                                  (dedupe_key,)).fetchone()
                if same:
                    return self._row(same) | {"deduplicated": True}  # type: ignore[operator]
            job_id = f"job-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now))}-{uuid.uuid4().hex[:6]}"
            db.execute("insert into jobs (id, kind, payload, state, source, dedupe_key, max_attempts, next_at, created_at)"
                       " values (?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
                       (job_id, kind, json.dumps(payload), source, dedupe_key, max(1, int(max_attempts)), now + delay, now))
            return self._row(db.execute("select * from jobs where id = ?", (job_id,)).fetchone())  # type: ignore[return-value]

    # ---- working

    def claim(self, owner: str, lease_seconds: float) -> dict[str, Any] | None:
        """The oldest job that is due, now leased to ``owner``; None when nothing is due."""
        now = self.clock()
        with self._tx() as db:
            row = db.execute("select id from jobs where state = 'queued' and next_at <= ? order by next_at, created_at limit 1",
                             (now,)).fetchone()
            if row is None:
                return None
            db.execute("update jobs set state = 'running', owner = ?, lease_until = ?, attempts = attempts + 1,"
                       " started_at = ?, error = null where id = ?", (owner, now + lease_seconds, now, row["id"]))
            return self._row(db.execute("select * from jobs where id = ?", (row["id"],)).fetchone())

    def heartbeat(self, job_id: str, owner: str, lease_seconds: float) -> bool:
        """Extend the lease. False when the job is no longer this owner's (it was recovered)."""
        with self._tx() as db:
            cur = db.execute("update jobs set lease_until = ? where id = ? and owner = ? and state = 'running'",
                             (self.clock() + lease_seconds, job_id, owner))
            return cur.rowcount == 1

    def _finish(self, job_id: str, owner: str, state: str, result: Any = None, error: str | None = None) -> bool:
        with self._tx() as db:
            cur = db.execute("update jobs set state = ?, finished_at = ?, lease_until = null, result = ?, error = ?"
                             " where id = ? and owner = ? and state = 'running'",
                             (state, self.clock(), json.dumps(result, default=str) if result is not None else None,
                              error, job_id, owner))
            return cur.rowcount == 1

    def succeed(self, job_id: str, owner: str, result: Any = None) -> bool:
        return self._finish(job_id, owner, "succeeded", result)

    def fail(self, job_id: str, owner: str, error: str, result: Any = None) -> bool:
        """A failure retrying won't fix (a table failed its checks): no more attempts."""
        return self._finish(job_id, owner, "failed", result, error)

    def retry_later(self, job_id: str, owner: str, error: str) -> dict[str, Any] | None:
        """A failure worth another try (network, throttling, a table busy): back in the queue after the
        backoff, or ``dead`` once the attempts are used up. Returns the job as it now is."""
        now = self.clock()
        with self._tx() as db:
            job = db.execute("select * from jobs where id = ? and owner = ? and state = 'running'", (job_id, owner)).fetchone()
            if job is None:
                return None
            self._requeue(db, job, error, now)
            return self._row(db.execute("select * from jobs where id = ?", (job_id,)).fetchone())

    def _requeue(self, db: sqlite3.Connection, job: sqlite3.Row, error: str, now: float) -> None:
        if job["attempts"] >= job["max_attempts"]:
            db.execute("update jobs set state = 'dead', finished_at = ?, lease_until = null, error = ? where id = ?",
                       (now, f"Gave up after {job['attempts']} attempts. Last error: {error}", job["id"]))
            return
        wait = min(self.backoff_seconds * 2 ** (job["attempts"] - 1), self.max_backoff_seconds)
        db.execute("update jobs set state = 'queued', owner = null, lease_until = null, next_at = ?, error = ? where id = ?",
                   (now + wait, error, job["id"]))

    def recover(self) -> list[str]:
        """Running jobs whose worker stopped renewing (it crashed or was killed) go back in the queue,
        counting the lost run as an attempt. Returns their ids."""
        now = self.clock()
        with self._tx() as db:
            stale = db.execute("select * from jobs where state = 'running' and lease_until < ?", (now,)).fetchall()
            for job in stale:
                self._requeue(db, job, f"{job['owner']} stopped while running it", now)
            return [j["id"] for j in stale]

    def cancel(self, job_id: str, reason: str, owner: str | None = None) -> bool:
        """Stop a queued job (waiting to start or to retry) from running. With ``owner``, also a job
        that worker has just claimed and not yet started; a job that is writing is never stopped."""
        with self._tx() as db:
            cur = db.execute("update jobs set state = 'cancelled', finished_at = ?, lease_until = null, error = ?"
                             " where id = ? and (state = 'queued' or (state = 'running' and owner = ?))",
                             (self.clock(), reason, job_id, owner))
            return cur.rowcount == 1

    def requeue(self, job_id: str, by: str) -> dict[str, Any]:
        """Put a ``dead`` or ``failed`` job back in the queue with its attempts reset, recording ``by``.
        Raises LookupError for an unknown job and ValueError for one that can't go back."""
        with self._tx() as db:
            job = db.execute("select * from jobs where id = ?", (job_id,)).fetchone()
            if job is None:
                raise LookupError(f"There is no job {job_id} in this agent's queue.")
            if job["state"] not in ("dead", "failed"):
                raise ValueError(f"Job {job_id} is {job['state']}; only a dead or failed job can be requeued.")
            if (job["dedupe_key"] or "").startswith("pipeline:"):  # its status file says it finished
                raise ValueError(f"Job {job_id} came from a Fabric pipeline; submit the request again from the pipeline.")
            if job["dedupe_key"] and db.execute("select 1 from jobs where dedupe_key = ? and state in ('queued', 'running')",
                                                (job["dedupe_key"],)).fetchone():
                raise ValueError(f"Job {job_id} has a newer run queued or running; it wasn't requeued.")
            db.execute("update jobs set state = 'queued', attempts = 0, next_at = ?, owner = null, lease_until = null,"
                       " started_at = null, finished_at = null, result = null, error = null, requeued_by = ? where id = ?",
                       (self.clock(), by, job_id))
            return self._row(db.execute("select * from jobs where id = ?", (job_id,)).fetchone())  # type: ignore[return-value]

    def prune(self, older_than_days: float, dry_run: bool = False) -> list[str]:
        """Remove succeeded, failed and cancelled jobs that finished more than ``older_than_days`` ago,
        in one transaction; queued, running and dead jobs stay. With ``dry_run``, only list them.
        Returns their ids. Only queue rows go; nothing in OneLake is touched."""
        cutoff = self.clock() - older_than_days * 86400
        where = (f"state in ({', '.join('?' * len(PRUNABLE))}) and finished_at is not null and finished_at < ?",
                 (*PRUNABLE, cutoff))
        if dry_run:
            return [r["id"] for r in self._rows(f"select id from jobs where {where[0]}", where[1])]
        with self._tx() as db:
            ids = [r["id"] for r in db.execute(f"select id from jobs where {where[0]}", where[1])]
            db.execute(f"delete from jobs where {where[0]}", where[1])
            return ids

    def set_payload(self, job_id: str, payload: dict[str, Any]) -> bool:
        """Change what a queued job will do (a retry narrowed to the tables that didn't finish)."""
        with self._tx() as db:
            cur = db.execute("update jobs set payload = ? where id = ? and state = 'queued'", (json.dumps(payload), job_id))
            return cur.rowcount == 1

    # ---- reading

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._row(self._one("select * from jobs where id = ?", (job_id,)))

    def find(self, dedupe_key: str) -> dict[str, Any] | None:
        """The newest job with ``dedupe_key``, in any state."""
        return self._row(self._one("select * from jobs where dedupe_key = ? order by created_at desc limit 1",
                                     (dedupe_key,)))

    def list(self, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]:
        sql, args = "select * from jobs", ()
        if state:
            sql, args = sql + " where state = ?", (state,)
        rows = self._rows(sql + " order by created_at desc limit ?", (*args, limit))
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def counts(self, env: str | None = None) -> dict[str, int]:
        """Jobs in each state; with ``env``, only that environment's."""
        sql, args = "select state, count(*) as n from jobs", ()
        if env is not None:
            sql, args = sql + " where json_extract(payload, '$.env') = ?", (env,)
        rows = self._rows(sql + " group by state", args)
        return {s: 0 for s in STATES} | {r["state"]: r["n"] for r in rows}

    def for_env(self, env: str, limit: int = 200) -> list[dict[str, Any]]:
        """An environment's newest jobs, plus every one still queued or running."""
        rows = self._rows(
            "select * from jobs where json_extract(payload, '$.env') = ? and (state in ('queued', 'running') or id in"
            " (select id from jobs where json_extract(payload, '$.env') = ? order by created_at desc limit ?))"
            " order by created_at desc", (env, env, limit))
        return [self._row(r) for r in rows]  # type: ignore[misc]

    # ---- schedules: when each fires next, kept here so a restart neither skips nor doubles a run

    def schedule_next(self, schedule_id: str) -> float | None:
        row = self._one("select next_at from schedule_state where id = ?", (schedule_id,))
        return row["next_at"] if row else None

    def set_schedule_next(self, schedule_id: str, next_at: float) -> None:
        with self._tx() as db:
            db.execute("insert into schedule_state (id, next_at) values (?, ?)"
                       " on conflict(id) do update set next_at = excluded.next_at", (schedule_id, next_at))

    def close(self) -> None:
        """Nothing stays open between calls; kept for callers that close the queue when they're done."""
