"""Run requests from Fabric pipelines: a mailbox in each environment's ``LH_Meta`` lakehouse.

A pipeline asks the agent to run an environment by writing a small JSON file; the agent answers
in a status file next to it. Nothing listens on a network port, and nothing outside the
environment's own workspace can ask: only identities that may write that ``LH_Meta`` can
submit, and a request names the environment whose mailbox it sits in.

    LH_Meta/Files/_fabric_desktop/
        requests/<request_id>.json   written by the pipeline (contract below)
        status/<request_id>.json     written by the agent: accepted, running, then a final state
        cancel/<request_id>.json     written by the pipeline to cancel (any content)

Request, contract 1::

    {"contract": 1, "env": "prod", "entities": ["silver.orders"], "backfill": "",
     "cascade": false, "deadline": "2026-10-17T03:00:00Z", "submitted_by": "pl_nightly run 8c1..."}

``env`` is required. ``entities`` empty means every entity (refused when the agent has an entity
allowlist). ``deadline`` is the latest time the run may start; past it the request expires.
``submitted_by`` is free text the pipeline sets, recorded as given.

Each request is claimed by creating its status file with ``If-None-Match: *``, so one agent takes
it however many poll the mailbox; the job id is the request id's dedupe key in the agent's queue,
so a crash between claiming and queueing doesn't run it twice. Status states:

    accepted   queued on the agent        running    a worker holds it
    succeeded  every table ran            failed     a table failed (``error`` says which)
    cancelled  a cancel file arrived before it started, or before a retry
    expired    the deadline passed before it started
    rejected   the request was malformed or not allowed (``reason`` says why)

The last five are final: the agent never changes a final status again.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import logging
import re
import socket
import threading
from pathlib import Path
from typing import Any, Callable

import requests

from .leases import LocalBackend, OneLakeBackend, Stored
from .logs import fields
from .onelake import DFS_HOST

CONTRACT = 1
CONTROL_DIR = "Files/_fabric_desktop"
FINAL = ("succeeded", "failed", "cancelled", "expired", "rejected")
MAX_REQUEST_BYTES = 64 * 1024
WRITING_GRACE = 60  # seconds an unreadable request gets before it is rejected
_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_JOB_STATE = {"queued": "accepted", "running": "running", "succeeded": "succeeded", "failed": "failed",
              "dead": "failed", "cancelled": "cancelled"}


class LocalControl(LocalBackend):
    """Control files in a folder on this machine (local environments, and tests)."""

    def _path(self, key: str) -> Path:
        return self.folder / key

    def list(self, folder: str) -> list[str]:
        try:
            return sorted(p.name for p in (self.folder / folder).iterdir() if p.suffix == ".json")
        except FileNotFoundError:
            return []


class OneLakeControl(OneLakeBackend):
    """Control files in ``LH_Meta``'s Files area, changed with the same conditional requests as leases."""

    def _url(self, key: str) -> str:
        return f"https://{DFS_HOST}/{self.workspace_id}/{self.lakehouse_id}/{CONTROL_DIR}/{key}"

    def list(self, folder: str) -> list[str]:
        url = f"https://{DFS_HOST}/{self.workspace_id}"
        params = {"resource": "filesystem", "recursive": "false",
                  "directory": f"{self.lakehouse_id}/{CONTROL_DIR}/{folder}"}
        names: list[str] = []
        while True:
            resp = self._http.get(url, params=params, headers=self._headers(), timeout=60)
            if resp.status_code == 404:
                return []  # no request has been written yet
            resp.raise_for_status()
            names += [p["name"].rsplit("/", 1)[-1] for p in resp.json().get("paths", [])
                      if str(p.get("isDirectory", "")).lower() != "true"]
            token = resp.headers.get("x-ms-continuation")
            if not token:
                return sorted(n for n in names if n.endswith(".json"))
            params["continuation"] = token


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _epoch(text: str) -> float:
    when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("give the deadline with a time zone, e.g. 2026-10-17T03:00:00Z")
    return when.timestamp()


class Mailbox:
    """The agent's side of the contract, for every environment it takes pipeline requests for.

    ``control(env)`` returns the environment's control files backend; ``agent`` is the
    :class:`~fabric_desktop.agent.Agent` that queues the work.
    """

    def __init__(self, agent: Any, control: Callable[[str], Any], environments: list[str],
                 entities: list[str] | None = None, allow_backfill: bool = False, retention_days: float = 14,
                 known_entities: Callable[[], set[str]] | None = None, log: logging.Logger | None = None):
        self.agent, self.environments = agent, list(environments)
        self.entities, self.allow_backfill = list(entities or []), allow_backfill
        self.retention = retention_days * 86400
        self._control_for = control
        self._controls: dict[str, Any] = {}
        self._known = known_entities
        self.log = log or logging.getLogger("fabric_desktop.mailbox")
        self._lock = threading.Lock()

    @property
    def clock(self) -> Callable[[], float]:
        return self.agent.clock

    def control(self, env: str) -> Any:
        if env not in self._controls:
            self._controls[env] = self._control_for(env)
        return self._controls[env]

    # ---- files

    def _read(self, env: str, key: str) -> tuple[dict[str, Any] | None, Stored | None]:
        stored = self.control(env).read(key)
        if stored is None:
            return None, None
        try:
            body = json.loads(stored.data)
        except ValueError:
            return None, stored
        return (body if isinstance(body, dict) else None), stored

    def _write_status(self, env: str, rid: str, status: dict[str, Any], stored: Stored | None) -> bool:
        """Create the status (the claim) when ``stored`` is None, else replace it if unchanged since read."""
        data = json.dumps(status, indent=2, default=str).encode()
        key = f"status/{rid}.json"
        backend = self.control(env)
        return bool(backend.create(key, data) if stored is None else backend.replace(key, data, stored.etag))

    # ---- the loop

    def poll(self) -> None:
        """Claim new requests and bring every status this agent owns up to date. One environment's
        trouble (OneLake unreachable, LH_Meta missing) doesn't stop the others."""
        for env in self.environments:
            try:
                self._poll_env(env)
            except Exception as exc:
                self._controls.pop(env, None)  # build it again next time (a token or lakehouse may have changed)
                self.log.warning("couldn't read the pipeline mailbox", extra=fields(env=env, error=f"{type(exc).__name__}: {exc}"))

    def _poll_env(self, env: str) -> None:
        backend = self.control(env)
        for name in backend.list("requests"):
            rid = name[:-5]
            if not _ID.fullmatch(rid):
                continue  # can't name a status file after it; the docs say what ids may contain
            with self._lock:
                status, stored = self._read(env, f"status/{rid}.json")
                if stored is None:
                    self._claim(env, rid)
                elif status and status.get("agent") == self.agent.config.name and status.get("state") not in FINAL:
                    self._sync(env, rid, status, stored)
                elif status and status.get("state") in FINAL:
                    self._expire_files(env, rid, status, stored)

    def job_changed(self, job: dict[str, Any]) -> None:
        """Called by the agent when a pipeline job starts or ends, so the status doesn't wait for the next poll."""
        source = str(job.get("source") or "")
        if not source.startswith("pipeline:"):
            return
        env, rid = job["payload"].get("env", ""), source.split(":", 1)[1]
        if env not in self.environments:
            return
        try:
            with self._lock:
                status, stored = self._read(env, f"status/{rid}.json")
                if status and stored and status.get("agent") == self.agent.config.name and status.get("state") not in FINAL:
                    self._sync(env, rid, status, stored)
        except Exception as exc:  # the next poll tries again
            self.log.warning("couldn't update the pipeline status", extra=fields(env=env, request_id=rid, error=str(exc)))

    def cancel_requested(self, job: dict[str, Any]) -> bool:
        """Whether a pipeline cancelled this job before the worker that just claimed it starts it (a retry, say)."""
        source = str(job.get("source") or "")
        env = job["payload"].get("env", "")
        if not source.startswith("pipeline:") or env not in self.environments:
            return False
        try:
            return self.control(env).read(f"cancel/{source.split(':', 1)[1]}.json") is not None
        except Exception:  # can't tell: run it, as the request asked
            return False

    # ---- one request

    def _claim(self, env: str, rid: str) -> None:
        now = self.clock()
        stored = self.control(env).read(f"requests/{rid}.json")
        if stored is None:
            return  # deleted since the listing
        request, reason = self._parse(env, rid, stored.data)
        if not request and now - stored.modified < WRITING_GRACE:
            return  # empty or half-written: its writer may still be at it, so look again next time
        base = {"contract": CONTRACT, "request_id": rid, "agent": self.agent.config.name, "host": socket.gethostname(),
                "env": env, "received_at": _iso(now), "updated_at": _iso(now)}
        if request:
            base |= {k: request[k] for k in ("entities", "backfill", "cascade", "deadline", "submitted_by") if k in request}
        if reason:
            status = base | {"state": "rejected", "reason": reason}
        elif self.control(env).read(f"cancel/{rid}.json") is not None:
            status = base | {"state": "cancelled", "reason": "a cancel file arrived before the agent took the request"}
        elif request.get("deadline") and _epoch(request["deadline"]) < now:
            status = base | {"state": "expired", "reason": f"its deadline {request['deadline']} passed before it started"}
        else:
            status = base | {"state": "accepted"}
        if not self._write_status(env, rid, status, None):
            return  # another agent claimed it first
        self.log.info(f"pipeline request {status['state']}", extra=fields(env=env, request_id=rid, reason=status.get("reason")))
        if status["state"] == "accepted":
            self._sync(env, rid, *self._read(env, f"status/{rid}.json"))  # type: ignore[arg-type]

    def _parse(self, env: str, rid: str, data: bytes) -> tuple[dict[str, Any], str]:
        if len(data) > MAX_REQUEST_BYTES:
            return {}, f"the request is {len(data)} bytes; the most is {MAX_REQUEST_BYTES}"
        if not data.strip():
            return {}, "the request file is empty"
        try:
            raw = json.loads(data)
        except ValueError as exc:
            return {}, f"the request isn't JSON: {exc}"
        if not isinstance(raw, dict):
            return {}, "the request is a JSON object"
        problems = []
        contract = raw.get("contract", CONTRACT)
        if contract != CONTRACT:
            problems.append(f"this agent speaks contract {CONTRACT}, not {contract!r}; update the agent or the pipeline")
        if raw.get("request_id") not in (None, rid):
            problems.append(f"request_id {raw.get('request_id')!r} doesn't match the file name {rid}")
        if raw.get("env") != env:
            problems.append(f"env is {raw.get('env')!r}, but this is the {env} mailbox; a request runs the environment it is written to")
        entities = raw.get("entities", [])
        if not isinstance(entities, list) or not all(isinstance(e, str) for e in entities):
            problems.append("entities is a list of entity names")
            entities = []
        backfill, cascade = raw.get("backfill", "") or "", raw.get("cascade", False)
        if backfill not in ("", "full"):
            problems.append('backfill is "" or "full"')
        if not isinstance(cascade, bool):
            problems.append("cascade is true or false")
        elif cascade and len(entities) != 1:
            problems.append("cascade runs one entity and everything downstream of it; name exactly one")
        if (backfill or cascade) and not self.allow_backfill:
            problems.append("this agent doesn't take backfills from pipelines (pipeline.allow_backfill in agent.toml)")
        deadline = raw.get("deadline")
        if deadline is not None:
            try:
                _epoch(str(deadline))
            except ValueError as exc:
                problems.append(f"deadline: {exc}")
        submitted_by = raw.get("submitted_by", "")
        if not isinstance(submitted_by, str) or len(submitted_by) > 200:
            problems.append("submitted_by is text of at most 200 characters")
        if env not in self.environments:
            problems.append(f"this agent doesn't take pipeline requests for {env}")
        known = self._known() if self._known else None
        if known is not None:
            unknown = [e for e in entities if e not in known]
            if unknown:
                problems.append(f"no entity called {', '.join(unknown)} in the project")
        if self.entities:
            if not entities:
                problems.append("list the entities to run; this agent only takes " + ", ".join(self.entities))
            refused = [e for e in entities if not any(fnmatch.fnmatchcase(e, p) for p in self.entities)]
            if refused:
                problems.append(f"{', '.join(refused)} isn't in pipeline.entities in agent.toml")
        request = {"entities": entities, "backfill": backfill, "cascade": bool(cascade) if isinstance(cascade, bool) else False,
                   "submitted_by": submitted_by if isinstance(submitted_by, str) else ""}
        if deadline is not None:
            request["deadline"] = str(deadline)
        return request, "; ".join(problems)

    def _sync(self, env: str, rid: str, status: dict[str, Any], stored: Stored) -> None:
        """Make the status say what the agent's queue says, queueing the job if a crash left it unqueued."""
        key = f"pipeline:{rid}"
        queue = self.agent.queue
        job = queue.find(key)
        now = self.clock()
        if job is None:
            job = self.agent.enqueue(env, status.get("entities") or [], status.get("backfill", ""),
                                     bool(status.get("cascade")), source=key, dedupe_key=key)
        cancel = self.control(env).read(f"cancel/{rid}.json") is not None
        if job["state"] == "queued":
            deadline = status.get("deadline")
            if cancel:
                queue.cancel(job["id"], "the pipeline cancelled it")
            elif deadline and _epoch(deadline) < now:
                queue.cancel(job["id"], f"its deadline {deadline} passed before it started")
            job = queue.get(job["id"]) or job
        state = "expired" if job["state"] == "cancelled" and (job.get("error") or "").startswith("its deadline") \
            else _JOB_STATE.get(job["state"], "accepted")
        new = status | {"state": state, "job_id": job["id"], "attempts": job["attempts"]}
        if job.get("started_at"):
            new["started_at"] = _iso(job["started_at"])
        if job.get("finished_at") and state in FINAL:
            new["finished_at"] = _iso(job["finished_at"])
        if job.get("result"):
            new["result"] = job["result"]
        if job.get("error"):
            new["error" if state != "accepted" else "last_error"] = job["error"]
        if cancel and state == "running":
            new["cancel"] = "requested while running: the run in progress finishes, and it won't be retried"
        if {k: v for k, v in new.items() if k != "updated_at"} == {k: v for k, v in status.items() if k != "updated_at"}:
            return
        new["updated_at"] = _iso(now)
        if self._write_status(env, rid, new, stored):
            if state != status.get("state"):
                self.log.info(f"pipeline request {state}", extra=fields(env=env, request_id=rid, job_id=job["id"]))

    def _expire_files(self, env: str, rid: str, status: dict[str, Any], stored: Stored) -> None:
        """Remove a finished request's files once they are ``retention_days`` old, so the mailbox stays small."""
        try:
            updated = _epoch(str(status.get("updated_at")))
        except ValueError:
            return
        if self.clock() - updated < self.retention:
            return
        backend = self.control(env)
        for folder in ("requests", "cancel"):
            other = backend.read(f"{folder}/{rid}.json")
            if other is not None:
                backend.delete(f"{folder}/{rid}.json", other.etag)
        backend.delete(f"status/{rid}.json", stored.etag)


def control_backend_for(store: Any, meta_lakehouse_id: str | None, token: Callable[[], str],
                        session: requests.Session | None = None) -> Any:
    """The control files backend for an environment's store: OneLake ``LH_Meta``, or a local folder."""
    from .meta import LocalStore, OneLakeStore
    if isinstance(store, OneLakeStore):
        if not meta_lakehouse_id:
            raise LookupError("LH_Meta doesn't exist in this environment's workspace yet; promote to it first")
        return OneLakeControl(store.workspace_id, meta_lakehouse_id, token, session)
    if isinstance(store, LocalStore):
        return LocalControl(Path(store.root) / "_control")
    raise TypeError(f"no control files for {type(store).__name__}")
