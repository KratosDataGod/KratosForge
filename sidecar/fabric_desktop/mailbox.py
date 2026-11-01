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
``submitted_by`` is free text the pipeline sets, recorded as given: nothing checks it, and OneLake
doesn't say who wrote a file (its owner is always ``$superuser``). What can be checked is the run:

    "pipeline_run": {"item_id": "<@pipeline().Pipeline>", "run_id": "<@pipeline().RunId>"}

The agent looks that job up in the environment's workspace with the Fabric API and records the
answer as the status's ``pipeline_run``: ``verified`` is true only for a Pipeline job of that item,
started within ``RUN_MAX_AGE`` and not in the future. With ``[pipeline] require_run = true`` a request
that doesn't name a run, or whose run doesn't check out or has already finished (the notebook that
writes a request waits for its answer, so its run is still going), is rejected. A lookup Fabric
couldn't answer just then (429, 5xx, no connection) is tried again on the next poll for up to
``RUN_LOOKUP_GRACE`` before it counts. Untested on a Fabric tenant (PB-049).

Each request is claimed by creating its status file with ``If-None-Match: *``, so one agent takes
it however many poll the mailbox (with ``[pipeline] claim = "when_free"``, only while it has a worker
free, so a busy agent leaves the request for one that isn't); the job id is the request id's dedupe key in the agent's queue,
so a crash between claiming and queueing doesn't run it twice. Status states:

    accepted   queued on the agent        running    a worker holds it
    succeeded  every table ran            failed     a table failed (``error`` says which)
    cancelled  a cancel file arrived before it started, or before a retry
    expired    the deadline passed before it started
    rejected   the request was malformed or not allowed (``reason`` says why)

The last five are final: the agent never changes a final status again.

Takeover (``[pipeline] take_over = true``, Preview): a request that is ``accepted`` or ``running``
belongs to the agent its status names. Once that agent's own status record (``agents/<name>.json``)
is past its ``stale_after``, plus ``TAKEOVER_GRACE`` for clock differences, another agent may take
the request over by replacing the status with ``If-Match`` on the etag it read, so exactly one
agent does. The status then names the new agent, ``taken_over_from`` and ``taken_over_at``, and
``agents`` lists every agent that has held it. A request that ``max_attempts`` agents have held
ends ``failed``, so one that crashes every agent doesn't go round the pool forever. An agent whose
request was taken over (it was paused, say, and came back) finds its status replace refused, or
reads the status before each table, and stops: it cancels its copy and writes nothing more. The
new agent takes each table's write lease before it writes, so it waits for a lease the lost agent
still holds to run out. An owner with no status record, or one that can't be read, keeps its
requests: an agent can't be judged lost without it. Untested on a Fabric tenant (roadmap 7.1).
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

from . import jsonfile
from .errors import NotSetUp
from .leases import LocalBackend, OneLakeBackend, Stored
from .logs import fields
from .onelake import DFS_HOST, _onelake_error
from .paging import Pages
from .redact import describe
from .retrying import send_with_retry

CONTRACT = 1
CONTROL_DIR = "Files/_fabric_desktop"
FINAL = ("succeeded", "failed", "cancelled", "expired", "rejected")
MAX_REQUEST_BYTES = 64 * 1024
WRITING_GRACE = 60  # seconds an unreadable request gets before it is rejected
RUN_MAX_AGE = 24 * 3600  # seconds: an older pipeline run can't vouch for a new request
RUN_CLOCK_SKEW = 300  # seconds a run's start time may be ahead of this PC's clock
RUN_LOOKUP_GRACE = 15 * 60  # seconds a request waits for a run lookup Fabric couldn't answer
RUN_GOING = ("InProgress", "NotStarted")  # a run that wrote a request and waits for its answer
_TRANSIENT = (408, 429, 500, 502, 503, 504)
TAKEOVER_GRACE = 60  # seconds past an agent's stale_after before its requests are taken over (clock differences)
_RUN_FIELDS = ("job_id", "attempts", "started_at", "finished_at", "result", "error", "last_error", "cancel")
_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_GUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
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
        pages = Pages(f"the {folder} mailbox folder")
        while True:
            resp = send_with_retry(lambda: self._http.get(url, params=dict(params), headers=self._headers(),
                                                          timeout=60), "GET", sleep=self._sleep)
            if resp.status_code == 404:
                return []  # no request has been written yet
            if resp.status_code >= 400:
                raise _onelake_error(resp, f"list the {folder} mailbox folder")
            names += [p["name"].rsplit("/", 1)[-1] for p in resp.json().get("paths", [])
                      if str(p.get("isDirectory", "")).lower() != "true"]
            token = resp.headers.get("x-ms-continuation")
            if not token:
                return sorted(n for n in names if n.endswith(".json"))
            params["continuation"] = pages.follow(token)


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _epoch(text: str) -> float:
    when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("give the deadline with a time zone, e.g. 2026-10-17T03:00:00Z")
    return when.timestamp()


def _fabric_utc(text: Any) -> float:
    """A Fabric time such as ``2026-10-28T06:35:00.7812154`` (UTC, up to seven decimals, often no zone)."""
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?", str(text or ""))
    if not m:
        raise ValueError(f"not a time: {text!r}")
    when = dt.datetime.fromisoformat(m.group(1) + (f".{m.group(2)[:6]}" if m.group(2) else "")
                                     + (m.group(3) or "Z").replace("Z", "+00:00"))
    return when.timestamp()


class Mailbox:
    """The agent's side of the contract, for every environment it takes pipeline requests for.

    ``control(env)`` returns the environment's control files backend; ``agent`` is the
    :class:`~fabric_desktop.agent.Agent` that queues the work.
    """

    def __init__(self, agent: Any, control: Callable[[str], Any], environments: list[str],
                 entities: list[str] | None = None, allow_backfill: bool = False, retention_days: float = 14,
                 known_entities: Callable[[], set[str]] | None = None, log: logging.Logger | None = None,
                 claim: str = "always", take_over: bool = False, max_attempts: int = 3,
                 verify_run: Callable[[str, str, str], dict[str, Any]] | None = None, require_run: bool = False,
                 runs_file: Path | None = None):
        self.agent, self.environments = agent, list(environments)
        self.claim = claim  # "when_free": leave new requests for an agent with a worker free
        self.take_over, self.max_attempts = take_over, max(1, int(max_attempts))
        # verify_run(env, item_id, run_id) returns Fabric's job instance (Runtime.verify_pipeline_run)
        self.verify_run, self.require_run = verify_run, require_run
        # With require_run, the pipeline runs already accepted, {env: {run_id: {"request_id", "at"}}}, kept
        # on this PC for RUN_MAX_AGE so one run vouches for one request only (#969)
        self._runs_file = runs_file
        self._runs: dict[str, dict[str, dict[str, Any]]] = {}
        self._runs_lock = threading.Lock()  # _claim runs under self._lock already
        if runs_file is not None:
            loaded = jsonfile.read_json(runs_file, {})
            self._runs = {env: {run: dict(e) for run, e in runs.items()  # a hand-edited entry without a time is dropped
                                if isinstance(e, dict) and isinstance(e.get("at"), (int, float)) and not isinstance(e["at"], bool)}
                          for env, runs in loaded.items() if isinstance(runs, dict)} if isinstance(loaded, dict) else {}
        self._lost_seen: dict[tuple[str, str], bool] = {}  # per poll: each owner's record is read once
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
                getattr(self.agent, "_controls", {}).pop(env, None)
                self.log.warning("couldn't read the pipeline mailbox", extra=fields(env=env, error=f"{type(exc).__name__}: {exc}"))

    def _poll_env(self, env: str) -> None:
        backend = self.control(env)
        self._lost_seen = {}
        for name in backend.list("requests"):
            rid = name[:-5]
            if not _ID.fullmatch(rid):
                continue  # can't name a status file after it; the docs say what ids may contain
            with self._lock:
                status, stored = self._read(env, f"status/{rid}.json")
                if stored is None:
                    if self.claim == "when_free" and not self.agent.has_free_worker():
                        continue  # another agent with room takes it; this one looks again next poll
                    self._claim(env, rid)
                elif status and status.get("state") in FINAL:
                    self._expire_files(env, rid, status, stored)
                elif status and status.get("agent") == self.agent.config.name:
                    self._sync(env, rid, status, stored)
                elif status:
                    self._handed_over(rid, status)
                    if self.take_over:
                        self._take_over(env, rid, status, stored)

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

    def taken_over(self, job: dict[str, Any]) -> str:
        """The agent that has this pipeline job's request now, when another agent took it over; ""
        when it is still this agent's, isn't a pipeline job, or the status can't be read."""
        source = str(job.get("source") or "")
        env = job["payload"].get("env", "")
        if not source.startswith("pipeline:") or env not in self.environments:
            return ""
        try:
            status, _ = self._read(env, f"status/{source.split(':', 1)[1]}.json")
        except Exception as exc:  # can't tell: carry on; the table lease keeps two writers apart
            self.log.warning("Couldn't read the pipeline job's status; carrying on: %s", describe(exc))
            return ""
        owner = str((status or {}).get("agent") or "")
        return owner if owner and owner != self.agent.config.name else ""

    def cancel_requested(self, job: dict[str, Any]) -> bool:
        """Whether a pipeline cancelled this job before the worker that just claimed it starts it (a retry, say)."""
        source = str(job.get("source") or "")
        env = job["payload"].get("env", "")
        if not source.startswith("pipeline:") or env not in self.environments:
            return False
        try:
            return self.control(env).read(f"cancel/{source.split(':', 1)[1]}.json") is not None
        except Exception as exc:  # can't tell: run it, as the request asked
            self.log.warning("Couldn't check for a cancel request; running the job: %s", describe(exc))
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
                "env": env, "received_at": _iso(now), "updated_at": _iso(now), "agents": [self.agent.config.name]}
        if request:
            base |= {k: request[k] for k in ("entities", "backfill", "cascade", "deadline", "submitted_by") if k in request}
        if request.get("pipeline_run") and not reason:
            base["pipeline_run"] = self._check_run(env, request["pipeline_run"], now)
            if self.require_run and not base["pipeline_run"]["verified"]:
                if base["pipeline_run"].get("transient") and now - stored.modified < RUN_LOOKUP_GRACE:
                    return  # Fabric couldn't answer just then; ask again next poll
                reason = f"its pipeline run didn't check out: {base['pipeline_run']['detail']} (pipeline.require_run in agent.toml)"
            elif self.require_run:
                other = self._run_used(env, request["pipeline_run"]["run_id"], rid, now)
                if other:
                    reason = f"pipeline run {request['pipeline_run']['run_id']} already asked for request {other}"
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
        if self.require_run and status["state"] == "accepted" and request.get("pipeline_run"):
            self._run_taken(env, request["pipeline_run"]["run_id"], rid, now)
        self.log.info(f"pipeline request {status['state']}", extra=fields(env=env, request_id=rid, reason=status.get("reason")))
        if status["state"] == "accepted":
            self._sync(env, rid, *self._read(env, f"status/{rid}.json"))  # type: ignore[arg-type]

    def _run_used(self, env: str, run_id: str, rid: str, now: float) -> str | None:
        """The other request ``run_id`` already vouched for within RUN_MAX_AGE, or None. The same
        request id asking again is the same request."""
        with self._runs_lock:
            entry = self._runs.get(env, {}).get(run_id)
        if entry and now - float(entry.get("at") or 0) <= RUN_MAX_AGE and entry.get("request_id") != rid:
            return str(entry.get("request_id"))
        return None

    def _run_taken(self, env: str, run_id: str, rid: str, now: float) -> None:
        with self._runs_lock:
            for runs in self._runs.values():  # drop what's past RUN_MAX_AGE as we go
                for old in [r for r, e in runs.items() if now - float(e.get("at") or 0) > RUN_MAX_AGE]:
                    del runs[old]
            self._runs.setdefault(env, {})[run_id] = {"request_id": rid, "at": now}
            if self._runs_file is not None:
                jsonfile.write_json(self._runs_file, self._runs)

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
        run = raw.get("pipeline_run")
        if run is not None and not (isinstance(run, dict) and all(isinstance(run.get(k), str) and _GUID.fullmatch(run[k])
                                                                  for k in ("item_id", "run_id"))):
            problems.append("pipeline_run is {\"item_id\": <pipeline id>, \"run_id\": <run id>}, both GUIDs")
            run = None
        if run is None and self.require_run:
            problems.append("this agent only takes requests that name the pipeline run that wrote them "
                            "(pipeline_run; pipeline.require_run in agent.toml)")
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
        if run is not None:
            request["pipeline_run"] = {"item_id": run["item_id"].lower(), "run_id": run["run_id"].lower()}
        return request, "; ".join(problems)

    def _check_run(self, env: str, run: dict[str, str], now: float) -> dict[str, Any]:
        """What Fabric says about the pipeline run a request names. Never raises: a run that can't be
        found or read is recorded as not verified, with why."""
        out: dict[str, Any] = dict(run, verified=False)
        if self.verify_run is None:
            return out | {"detail": "this agent doesn't check pipeline runs"}
        try:
            job = self.verify_run(env, run["item_id"], run["run_id"])
        except Exception as exc:  # recorded on the status; require_run decides whether it matters
            if getattr(exc, "status", None) in _TRANSIENT or isinstance(exc, (requests.ConnectionError, requests.Timeout)):
                return out | {"detail": f"couldn't look it up in the {env} workspace just now: {describe(exc)}",
                              "transient": True}
            return out | {"detail": f"couldn't find it in the {env} workspace: {describe(exc)}"}
        problems = []
        if str(job.get("jobType") or "") != "Pipeline":
            problems.append(f"it is a {job.get('jobType') or 'job of unknown type'}, not a pipeline run")
        if str(job.get("itemId") or "").lower() != run["item_id"]:
            problems.append("Fabric names a different item for it")
        try:
            age = now - _fabric_utc(job.get("startTimeUtc"))
            if age > RUN_MAX_AGE:
                problems.append(f"it started {age / 3600:.0f} hours ago; the most is {RUN_MAX_AGE // 3600}")
            elif age < -RUN_CLOCK_SKEW:
                problems.append("Fabric gives it a start time in the future")
        except ValueError:
            problems.append("Fabric gave no start time for it")
        out |= {"status": str(job.get("status") or ""), "started_at": str(job.get("startTimeUtc") or "")}
        if self.require_run and out["status"] not in RUN_GOING:
            problems.append(f"it has already finished ({out['status'] or 'no status'}); the run that asks is still going")
        if problems:
            return out | {"detail": "; ".join(problems)}
        return out | {"verified": True, "detail": f"pipeline run {run['run_id']} of item {run['item_id']}, {out['status']}"}

    def _sync(self, env: str, rid: str, status: dict[str, Any], stored: Stored) -> None:
        """Make the status say what the agent's queue says, queueing the job if a crash left it unqueued."""
        key = f"pipeline:{rid}"
        queue = self.agent.queue
        job = queue.find(key)
        now = self.clock()
        if job is not None and "job_id" not in status and job["state"] not in ("queued", "running"):
            job = None  # this agent held it once before and was taken over; this is a new go at it
        # A copy still queued or running here (this agent was paused, then got the request back) is
        # carried on as it is: the request is this agent's again, so taken_over() lets it continue.
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
            return
        latest, _ = self._read(env, f"status/{rid}.json")  # changed since read: was it taken over?
        if latest and latest.get("agent") != self.agent.config.name:
            self._handed_over(rid, latest)

    # ---- takeover

    def _handed_over(self, rid: str, status: dict[str, Any]) -> None:
        """Another agent has this request now. If this agent still has its own copy waiting, cancel
        it: the request is no longer this agent's to run, and it writes nothing more for it. A copy
        that is running stops before its next table (``taken_over``)."""
        job = self.agent.queue.find(f"pipeline:{rid}")
        if job and job["state"] == "queued" and self.agent.queue.cancel(job["id"], f"agent {status.get('agent')} took this request over"):
            self.log.warning("pipeline request taken over by another agent", extra=fields(
                request_id=rid, job_id=job["id"], agent=status.get("agent")))

    def _lost(self, env: str, owner: str) -> bool:
        """Whether ``owner`` stopped reporting: its status record is past ``stale_after``. No record,
        or one that can't be read, is not lost: this agent can't tell."""
        if not owner or not _ID.fullmatch(owner):
            return False
        if (env, owner) not in self._lost_seen:
            self._lost_seen[env, owner] = self._past_stale(env, owner)
        return self._lost_seen[env, owner]

    def _past_stale(self, env: str, owner: str) -> bool:
        stored = self.control(env).read(f"agents/{owner}.json")
        if stored is None:
            return False
        try:
            record = json.loads(stored.data)
            return record.get("kind") == "agent_status" and self.clock() > _epoch(str(record["stale_after"])) + TAKEOVER_GRACE
        except (ValueError, KeyError, TypeError, AttributeError):
            return False

    def _take_over(self, env: str, rid: str, status: dict[str, Any], stored: Stored) -> None:
        owner, me = str(status.get("agent") or ""), self.agent.config.name
        if not self._lost(env, owner):
            return
        if self.claim == "when_free" and not self.agent.has_free_worker():
            return
        req = self.control(env).read(f"requests/{rid}.json")
        if req is None:
            return
        _, reason = self._parse(env, rid, req.data)
        if reason:
            return  # this agent wouldn't take the request new; leave it for one that would
        now = self.clock()
        held = [str(a) for a in status.get("agents") or [owner] if a]
        keep = {k: v for k, v in status.items() if k not in _RUN_FIELDS}
        if len(held) >= self.max_attempts:
            new = keep | {"state": "failed", "finished_at": _iso(now),
                          "error": f"{len(held)} agents took this request and stopped reporting: {', '.join(held)}"}
        else:
            new = keep | {"agent": me, "host": socket.gethostname(), "state": "accepted", "agents": held + [me],
                          "taken_over_from": owner, "taken_over_at": _iso(now)}
        new["updated_at"] = _iso(now)
        if not self._write_status(env, rid, new, stored):
            return  # another agent took it over first, or its owner came back
        self.log.warning("pipeline request taken over" if new["state"] == "accepted" else "pipeline request failed: too many agents lost",
                         extra=fields(env=env, request_id=rid, taken_over_from=owner, agents=new.get("agents", held)))
        if new["state"] == "accepted":
            self._sync(env, rid, *self._read(env, f"status/{rid}.json"))  # type: ignore[arg-type]

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
            raise NotSetUp("LH_Meta doesn't exist in this environment's workspace yet; promote to it first")
        return OneLakeControl(store.workspace_id, meta_lakehouse_id, token, session)
    if isinstance(store, LocalStore):
        return LocalControl(Path(store.root) / "_control")
    raise TypeError(f"no control files for {type(store).__name__}")
