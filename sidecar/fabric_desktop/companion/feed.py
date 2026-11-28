"""The run feed (MC-001): one list of what is running on this PC and what ran, for the phone.

Nothing here runs, stops or stores anything. Each source reads a run type's existing status call
and turns its records into one shape:

    id          "<kind>:<the run type's own id>", the same across reconnects
    kind        sql, notebooks, pipeline, proof, side_by_side, fabric
    name        what the person would call it ("SQL query on Sales", "Prove: daily_load")
    state       queued, running, cancelling, succeeded, failed, cancelled or unknown
    started, ended   seconds since the epoch, or None
    seconds     how long it ran (or has been running), or None
    progress    {"done", "total", "unit"} where the run type reports it, else None
    engine      this_pc or fabric
    can_cancel  whether the run type can cancel it now: the phone shows Cancel only when true
    message     the run's own one-line message, with anything credential-shaped removed

A source that fails (a file that can't be read, a service that isn't there) is reported under
``sources`` and leaves the others alone, so one broken run type never hides the rest.

Metadata runs aren't listed yet: reading their run log can mean reading OneLake, too slow for a
feed the phone polls, and they can't be cancelled (meta_service has no stop).

``changes(since, epoch)`` gives only the runs that changed after a version, for the phone's long
poll. Versions live in memory, so each feed has a random ``epoch``: after the engine restarts the
epoch differs and the phone is told to ``reset`` and read the whole list, even when the new feed's
version has already passed the phone's. Removals are remembered for MAX_TOMBSTONES runs; a
``since`` older than the oldest removal forgotten is answered with ``reset`` too.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
import threading
from collections.abc import Callable, Iterable
from typing import Any

from ..redact import secrets as redact_secrets
from ..redact import user_paths

STATES = ("queued", "running", "cancelling", "succeeded", "failed", "cancelled", "unknown")
ACTIVE = ("queued", "running", "cancelling")
KINDS = ("sql", "notebooks", "pipeline", "proof", "side_by_side", "fabric")
MAX_NAME = 120
MAX_MESSAGE = 300
MAX_TOMBSTONES = 500
_BATCH_FILE = re.compile(r"^([0-9a-f]{12})\.json$")

SQL_STATES = {"queued": "queued", "running": "running", "cancelling": "cancelling", "completed": "succeeded",
              "failed": "failed", "timed_out": "failed", "cancelled": "cancelled"}
PIPELINE_STATES = {"pending": "queued", "running": "running", "succeeded": "succeeded", "failed": "failed",
                   "cancelled": "cancelled"}
PROOF_STATES = {"preparing": "running", "pc": "running", "fabric": "running", "verifying": "running",
                "done": "succeeded", "failed": "failed", "cancelled": "cancelled"}
SIDE_BY_SIDE_STATES = {"preparing": "running", "local": "running", "fabric": "running", "checking": "running",
                       "done": "succeeded", "failed": "failed", "cancelled": "cancelled"}
FABRIC_STATES = {"NotStarted": "queued", "InProgress": "running", "Completed": "succeeded", "Failed": "failed",
                 "Cancelled": "cancelled", "Deduped": "cancelled"}


def _text(value: Any, limit: int) -> str:
    """Text for the phone: printable, credential-shaped parts removed, cut to ``limit``."""
    text = value if isinstance(value, str) else ""
    return user_paths(redact_secrets("".join(c for c in text if c.isprintable())))[:limit]


def _when(value: Any) -> float | None:
    """Seconds since the epoch from a float, or an ISO time as the services write them."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    return None


def _seconds(started: float | None, ended: float | None, now: float) -> float | None:
    if started is None:
        return None
    return round(max(0.0, (ended if ended is not None else now) - started), 1)


def _progress(done: Any, total: Any, unit: str) -> dict[str, Any] | None:
    if not isinstance(done, int) or isinstance(done, bool) or done < 0:
        return None
    if total is not None and (not isinstance(total, int) or isinstance(total, bool) or total < done):
        total = None
    return {"done": done, "total": total, "unit": unit}


def run(kind: str, native_id: Any, name: Any, state: str, started: Any = None, ended: Any = None,
        seconds: Any = None, progress: dict[str, Any] | None = None, engine: str = "this_pc",
        can_cancel: bool = False, message: Any = "", now: float = 0.0) -> dict[str, Any]:
    """One feed row. A state outside STATES becomes unknown, and only an active run can cancel."""
    state = state if state in STATES else "unknown"
    begin, end = _when(started), _when(ended)
    took = round(float(seconds), 1) if isinstance(seconds, int | float) and not isinstance(seconds, bool) \
        else _seconds(begin, end, now)
    return {"id": f"{kind}:{native_id}", "kind": kind, "name": _text(name, MAX_NAME) or kind, "state": state,
            "started": begin, "ended": end, "seconds": took, "progress": progress, "engine": engine,
            "can_cancel": bool(can_cancel) and state in ("queued", "running"), "message": _text(message, MAX_MESSAGE)}


# ---- sources: each takes the engine's Service and returns rows


def sql_runs(service: Any, now: float) -> list[dict[str, Any]]:
    rows = []
    for r in service.sql_runs.recent():
        where = r.get("lakehouse") or r.get("warehouse") or r.get("project") or r.get("target") or ""
        statements = r.get("statements")
        progress = None
        if isinstance(statements, int) and statements > 1:
            done = r.get("statement") if isinstance(r.get("statement"), int) else 0
            progress = _progress(max(0, done - (1 if r.get("state") == "running" else 0)), statements, "statements")
        rows.append(run("sql", r.get("id"), f"SQL query on {where}" if where else "SQL query",
                        SQL_STATES.get(r.get("state"), "unknown"), r.get("started") or r.get("created"), r.get("ended"),
                        r.get("seconds"), progress, "this_pc", r.get("state") in ("queued", "running"),
                        r.get("error") or "", now))
    return rows


def _batch_state(batch: dict[str, Any]) -> str:
    items = batch.get("items") or []
    if batch.get("state") == "lost":
        return "failed"
    if batch.get("state") != "done":
        return "cancelling" if batch.get("stopping") else "running"
    statuses = {i.get("status") for i in items}
    if "failed" in statuses:
        return "failed"
    if "stopped" in statuses or batch.get("stopping"):
        return "cancelled"
    return "succeeded"


def notebook_batches(service: Any, now: float) -> list[dict[str, Any]]:
    folder = service._batch_dir()
    if not folder.is_dir():
        return []
    rows = []
    for path in folder.iterdir():
        match = _BATCH_FILE.match(path.name)
        if not match:
            continue
        try:
            batch = service.notebook_batch(match.group(1))
        except (LookupError, ValueError, OSError, TypeError, KeyError):
            continue  # written half-way, or removed as it was read: the next poll sees it
        items = batch.get("items") or []
        names = [str(i.get("name")) for i in items]
        label = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        state = _batch_state(batch)
        done = sum(1 for i in items if i.get("status") in ("ok", "failed", "skipped", "stopped"))
        running = [i for i in items if i.get("status") == "running"]
        message = ""
        if len(running) == 1 and isinstance(running[0].get("cell"), int):
            message = f"{running[0]['name']}: cell {running[0]['cell']} of {running[0].get('cells') or '?'}"
        failed = [i for i in items if i.get("status") == "failed"]
        if failed and not message:
            message = f"{failed[0].get('name')}: {failed[0].get('message') or 'failed'}"
        rows.append(run("notebooks", batch.get("id"), f"Notebooks: {label}", state, batch.get("started"),
                        batch.get("ended"), None, _progress(done, len(items), "notebooks"), "this_pc",
                        state == "running", message, now))
    return rows


def pipelines(service: Any, now: float) -> list[dict[str, Any]]:
    rows = []
    for r in service.pipeline_runs(limit=50).get("runs", []):
        counts = r.get("counts") or {}
        done = sum(int(counts.get(s) or 0) for s in ("succeeded", "failed", "skipped", "cancelled"))
        state = PIPELINE_STATES.get(r.get("state"), "unknown")
        rows.append(run("pipeline", r.get("run_id"), f"Pipeline: {r.get('pipeline') or ''}", state, r.get("started"),
                        r.get("ended"), r.get("seconds"), _progress(done, None, "activities"), "this_pc",
                        state in ("queued", "running"), r.get("stop_reason") or "", now))
    return rows


def proofs(service: Any, now: float) -> list[dict[str, Any]]:
    rows = []
    for r in service.prove_list(limit=20):
        pc = r.get("pc") or {}
        state = PROOF_STATES.get(r.get("state"), "unknown")
        progress = _progress(pc.get("cell"), pc.get("cells_total"), "cells") if r.get("state") == "pc" else None
        rows.append(run("proof", r.get("id"), f"Prove: {r.get('name') or ''}", state, r.get("started_at"),
                        r.get("finished_at"), None, progress, "fabric" if r.get("state") == "fabric" else "this_pc",
                        state == "running", r.get("message") or "", now))
    return rows


def side_by_side(service: Any, now: float) -> list[dict[str, Any]]:
    rows = []
    for r in service.side_by_side_list(limit=20):
        state = SIDE_BY_SIDE_STATES.get(r.get("state"), "unknown")
        rows.append(run("side_by_side", r.get("id"), f"Side by side: {r.get('name') or ''}", state,
                        r.get("started_at"), r.get("finished_at"), None, None,
                        "fabric" if r.get("state") == "fabric" else "this_pc", state == "running",
                        r.get("message") or "", now))
    return rows


def fabric_jobs(service: Any, now: float) -> list[dict[str, Any]]:
    """Jobs this app started in Fabric, from the app's own record. Never asks Fabric: the desktop's
    Monitor refreshes them, and a phone poll must not make a call to Fabric."""
    rows = []
    for r in service.fabric_runs(limit=50, refresh=False):
        job = r.get("job_id")
        if not job:
            continue
        state = FABRIC_STATES.get(r.get("status"), "unknown")
        cancellable = bool(r.get("workspace_id") and r.get("item_id"))
        rows.append(run("fabric", job, r.get("name") or r.get("kind") or "Fabric job", state, r.get("started_at"),
                        r.get("finished_at"), None, None, "fabric", cancellable, r.get("message") or "", now))
    return rows


SOURCES: dict[str, Callable[[Any, float], list[dict[str, Any]]]] = {
    "sql": sql_runs, "notebooks": notebook_batches, "pipeline": pipelines, "proof": proofs,
    "side_by_side": side_by_side, "fabric": fabric_jobs,
}


def _order(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Active runs first, then newest first; a run with no start time last."""
    return sorted(rows, key=lambda r: (r["state"] not in ACTIVE, -(r["started"] or 0.0), r["id"]))


def _fingerprint(row: dict[str, Any]) -> str:
    """What makes a row "changed" for the phone. Elapsed seconds of a running run tick every poll, so
    they're left out: the phone counts them itself from ``started``."""
    stable = {k: v for k, v in row.items() if not (k == "seconds" and row["state"] in ACTIVE)}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


class RunFeed:
    """Reads every source on each call; keeps only fingerprints and versions, in memory."""

    def __init__(self, service: Any, sources: dict[str, Callable[[Any, float], list[dict[str, Any]]]] | None = None,
                 clock: Callable[[], float] | None = None):
        import time

        self.service = service
        self.sources = dict(SOURCES if sources is None else sources)
        self.clock = clock or time.time
        self._lock = threading.Lock()
        self._version = 0
        self._seen: dict[str, tuple[str, int]] = {}  # run id -> (fingerprint, version it last changed)
        self._gone: dict[str, int] = {}  # run id -> version it left the feed
        self._pruned = 0  # the highest version of a removal no longer remembered
        self.epoch = secrets.token_hex(8)

    def _read(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        now = self.clock()
        rows: dict[str, dict[str, Any]] = {}
        status: dict[str, str] = {}
        for name, source in self.sources.items():
            try:
                for row in source(self.service, now):
                    rows[row["id"]] = row
                status[name] = "ok"
            except Exception as exc:  # noqa: BLE001 - one run type's failure never hides the others
                status[name] = _text(f"unavailable: {type(exc).__name__}", 80)
        return list(rows.values()), status

    def _track(self, rows: list[dict[str, Any]], status: dict[str, str]) -> None:
        """Give each new or changed row the next version. A row that left the feed gets a tombstone,
        unless its source failed this time (it may well still be there)."""
        current = {r["id"] for r in rows}
        for row in rows:
            fp = _fingerprint(row)
            seen = self._seen.get(row["id"])
            if seen is None or seen[0] != fp:
                self._version += 1
                self._seen[row["id"]] = (fp, self._version)
                self._gone.pop(row["id"], None)
        failed = {k for k, v in status.items() if v != "ok"}
        for rid in [r for r in self._seen if r not in current]:
            if rid.split(":", 1)[0] in failed:
                continue
            self._version += 1
            del self._seen[rid]
            self._gone[rid] = self._version
        while len(self._gone) > MAX_TOMBSTONES:
            oldest = next(iter(self._gone))
            self._pruned = max(self._pruned, self._gone.pop(oldest))

    def list(self, limit: int = 50, active_only: bool = False) -> dict[str, Any]:
        limit = max(1, min(200, int(limit)))
        rows, status = self._read()
        with self._lock:
            self._track(rows, status)
            version = self._version
        if active_only:
            rows = [r for r in rows if r["state"] in ACTIVE]
        return {"epoch": self.epoch, "version": version, "runs": _order(rows)[:limit], "sources": status}

    def changes(self, since: int, epoch: str) -> dict[str, Any]:
        """Runs that changed after version ``since`` of feed ``epoch``, and ids that left the feed.
        Another epoch (the engine restarted), a version this feed never gave, or one older than a
        removal it has forgotten answers ``reset``, with every run, so the phone replaces its list."""
        rows, status = self._read()
        with self._lock:
            self._track(rows, status)
            version = self._version
            if (epoch != self.epoch or not isinstance(since, int) or isinstance(since, bool) or since < 0
                    or since > version or since < self._pruned):
                return {"epoch": self.epoch, "version": version, "reset": True, "runs": _order(rows), "removed": [],
                        "sources": status}
            changed = {rid for rid, (_, v) in self._seen.items() if v > since}
            removed = sorted(rid for rid, v in self._gone.items() if v > since)
        return {"epoch": self.epoch, "version": version, "reset": False,
                "runs": _order(r for r in rows if r["id"] in changed), "removed": removed, "sources": status}

    def get(self, run_id: str) -> dict[str, Any] | None:
        if not isinstance(run_id, str) or ":" not in run_id or run_id.split(":", 1)[0] not in KINDS:
            return None
        return next((r for r in self._read()[0] if r["id"] == run_id), None)
