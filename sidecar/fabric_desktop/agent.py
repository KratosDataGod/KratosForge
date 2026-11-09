"""The headless Fabric Desktop agent: runs metadata environments on this machine, unattended.

    fabric-desktop agent run      # the long-running service (systemd, Windows service, container)
    fabric-desktop agent check    # validate agent.toml, the project and the sign-in, then exit
    fabric-desktop agent enqueue prod [entity ...] [--backfill full]
    fabric-desktop agent status   # queue counts and the latest jobs

One loop does four things every ``poll_seconds``: puts back jobs whose worker died, enqueues
the schedules that are due, reads the pipeline mailbox (every ``pipeline.poll_seconds``, when
``[pipeline]`` is enabled), and starts queued jobs up to ``concurrency``. Each running job renews
its lease in the queue; each table it writes is held under a write lease in OneLake. Stopping
the service (SIGTERM, Ctrl+C, service stop) stops taking new jobs and waits for running ones;
anything cut off is picked up again by the next start. With ``[shortcut_health]`` it also checks
the environments' shortcuts every ``shortcut_health.minutes`` and posts each new break to Teams once.
It looks at the organization's policy file at start and every POLICY_CHECK_SECONDS, and writes a change
to its audit log as the desktop app does (policy_watch.py), since the policy applies to it too.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import socket
import sqlite3
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import IO, Any, Callable

from . import agent_status, jsonfile, policy_watch, shared_schedules, shortcut_watch
from .agent_config import AgentConfig
from .audit import AuditLog
from .audit import outcome as audit_outcome
from .dataops_service import post_teams
from .fabric_api import FabricClient
from .jobqueue import JobQueue
from .local_schedules import next_run
from .logs import fields
from .mailbox import ControlFolderInUse, LocalControl, Mailbox, hold_control_folder
from .meta import mesh as mesh_mod
from .meta.engine import Abandoned
from .onelake import FileStore
from .redact import describe
from .redact import without_webhook as _without_webhook
from .runtime import Runtime
from .workloads import deliver, evaluate
from .workloads.agents import read_status
from .workloads.incidents import IncidentLog
from .workloads.notify import DEFAULT_ROUTE, Outbox, Quiet, Route, Routing, failure, send, single
from .workloads.platform_notify import Ledger, PlatformRoute, PlatformRouting, validate
from .workloads.platform_notify import send as send_notices

INCIDENT_CLOCK = "incident-ages.json"  # in the agent's home: open incidents' ages, kept across restarts
MAX_SAVED_AGE = 30 * 86400.0  # a saved age past this is taken as this: no escalation waits that long
CRASHED_NEXT = ("Its worker stopped on every attempt: look in the service log for an out-of-memory kill or a crash, "
                "then lower agent.concurrency or run its tables in smaller jobs.")
PIPELINE_NEXT = "Fix the cause, then submit the request again from the pipeline."
AGENT_NO_EMAIL = ("Email routes are sent from the KratosForge app; on an agent use a Power Automate flow "
                  "(flow:https://...). This route's messages go nowhere")

KIND_META = "meta"
POLICY_CHECK_SECONDS = 900  # how often a running agent looks for a change to the policy file


def _epoch(local: dt.datetime) -> float:
    return local.timestamp()  # naive datetimes are this machine's local time, as schedules are


def _due(last: float | None, every: float, now: float) -> bool:
    """Whether a periodic task is due: never run, ``every`` seconds since it last ran, or the clock
    went back since then (it would otherwise wait out the jump before running again)."""
    return last is None or now - last >= every or now < last


# The longest a schedule waits between runs, plus an hour for a daylight-saving change. A next run
# further off than this was set before the clock went back, and is worked out again from now.
_DAY = 86400.0


def _longest_wait(when: dict[str, Any]) -> float:
    if when.get("kind") == "cron" and when.get("every_minutes"):
        return float(when["every_minutes"]) * 60 + 3600
    return (7 * _DAY if when.get("kind") == "weekly" else _DAY) + 3600


class JobLeaseLost(Abandoned):
    """This run's lease on its job ran out (it couldn't renew it in time), so the queue put the job
    back for another attempt; the run stops before its next table rather than run beside that one."""


class Agent:
    def __init__(self, config: AgentConfig, runtime: Runtime | None = None, queue: JobQueue | None = None,
                 log: logging.Logger | None = None, clock: Callable[[], float] = time.time):
        self.config = config
        self.runtime = runtime or Runtime(config)
        self.queue = queue or JobQueue(config.home / "queue.sqlite3", clock=clock,
                                       backoff_seconds=config.backoff_seconds,
                                       max_backoff_seconds=config.max_backoff_seconds)
        self.log = log or logging.getLogger("fabric_desktop.agent")
        self._queue_set_aside: Path | None = getattr(self.queue, "set_aside", None)  # alerted on the first step
        if self._queue_set_aside:
            self.log.error("the job queue file couldn't be read; started with an empty queue", extra=fields(
                moved_to=str(self._queue_set_aside)))
        self.clock = clock
        # Escalation ages run on this, so a clock change while the agent runs doesn't move them. A
        # clock handed in (tests) stands in for it, so moving that clock moves both.
        self.monotonic: Callable[[], float] = time.monotonic if clock is time.time else clock
        self._incident_seen = self._load_incident_clock()  # env -> IncidentLog.first_seen
        self._held: dict[str, IO[bytes]] = {}  # local control folders this running agent holds (run_forever)
        self._hold_folders = False
        self.stopping = threading.Event()
        self._running: dict[str, Future[None]] = {}
        self._claims: dict[str, str | None] = {}  # running job id -> its claim id, to renew it after a sleep
        self._stepped_at: float | None = None  # when the last pass began (a long gap: the machine slept)
        self._pool = ThreadPoolExecutor(max_workers=config.concurrency, thread_name_prefix="job")
        self.started_at = clock()
        self._status_at = 0.0
        self._pruned_at: float | None = None
        self._policy_at: float | None = None
        self._controls: dict[str, Any] = {}
        self._controls_lock = threading.Lock()  # the shortcut check's thread builds them too
        self.mailbox: Mailbox | None = None
        self._mailbox_at = 0.0
        self._incidents_at = 0.0
        self._shortcuts_at: float | None = None
        self._shortcut_thread: threading.Thread | None = None
        self._outbox: Outbox | None = None
        self._alert_box: Outbox | None = None  # "job didn't finish" alerts Teams didn't take yet
        self._alert_lock = threading.Lock()  # workers alert at once; one send at a time posts each just once
        self.messages_dropped = 0  # Teams or flow messages dropped unsent since this start (in the status record)
        self._dropped_lock = threading.Lock()  # the shortcut check sends from its own thread
        self._missed: set[str] = set()  # shared-schedule requests already alerted as missed, this start
        self.audit = AuditLog(config.home / "audit", app="agent")
        if config.pipeline.enabled:
            p = config.pipeline
            self.mailbox = Mailbox(self, self._control, p.environments, p.entities, p.allow_backfill,
                                   p.retention_days, known_entities=self.runtime.entity_names, claim=p.claim,
                                   take_over=p.take_over, max_attempts=config.max_attempts,
                                   verify_run=self.runtime.verify_pipeline_run, require_run=p.require_run,
                                   runs_file=config.home / "pipeline-runs.json")

    def _control(self, env: str) -> Any:
        """The environment's control files (mailbox and status records), built once."""
        with self._controls_lock:
            if env not in self._controls:
                backend = self.runtime.control_backend(env)
                self._hold(backend)
                self._controls[env] = backend
            return self._controls[env]

    def _load_incident_clock(self) -> dict[str, dict[str, tuple[float, float]]]:
        """How long each open incident had been open when this agent last looked, so a restart goes
        on from there rather than from the wall clock. Saved with the monotonic time it was saved at:
        when that is still behind now, the agent restarted without a reboot and the time between
        counts in full; after a reboot (monotonic time went back) the time the agent was down isn't
        known and isn't counted, so escalation can come later by that much, never earlier."""
        saved = jsonfile.read_json(self.config.home / INCIDENT_CLOCK, {})
        now, seen = self.monotonic(), {}
        for env, items in (saved.items() if isinstance(saved, dict) else ()):
            if env not in self.config.environments or not isinstance(items, dict):
                continue
            seen[str(env)] = {str(i): ((at if at <= now else now), min(max(0.0, age), MAX_SAVED_AGE))
                              for i, v in items.items() if isinstance(v, list) and len(v) == 2
                              and all(isinstance(x, (int, float)) for x in v) for at, age in [v]}
        return seen

    def _save_incident_clock(self) -> None:
        now = self.monotonic()
        data = {env: {i: [now, age + (now - at)] for i, (at, age) in items.items()}
                for env, items in self._incident_seen.items() if items}
        try:
            jsonfile.write_json(self.config.home / INCIDENT_CLOCK, data, indent=None)
        except OSError as exc:  # escalation still works this run; only a restart would start the ages again
            self.log.warning("couldn't save the incident ages", extra=fields(error=f"{type(exc).__name__}: {exc}"))

    def _hold(self, backend: Any) -> None:
        """While it runs, hold each local control folder it works in, so a second agent pointed at the
        same folder is refused rather than sharing it (mailbox.hold_control_folder)."""
        if not isinstance(backend, LocalControl) or not self._hold_folders:
            return
        key = str(Path(backend.folder).resolve())
        if key not in self._held:
            self._held[key] = hold_control_folder(Path(key), {
                "agent": self.config.name, "pid": os.getpid(), "host": socket.gethostname(),
                "since": dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc).isoformat(timespec="seconds")})

    def hold_control_folders(self) -> None:
        """Hold the local control folder of every environment before starting, or raise
        ControlFolderInUse. An environment whose folder can't be worked out yet is held when it can."""
        self._hold_folders = True
        for env in self.config.environments:
            try:
                self._control(env)
            except ControlFolderInUse:
                self.release_control_folders()
                raise
            except Exception as exc:
                self.log.warning("couldn't open the control files yet", extra=fields(
                    env=env, error=f"{type(exc).__name__}: {exc}"))

    def release_control_folders(self) -> None:
        self._hold_folders = False
        for f in self._held.values():
            f.close()
        self._held.clear()

    def publish_status(self, state: str = "running") -> None:
        """Write agents/<name>.json to each environment's control area. A failure is logged, never fatal."""
        for env in self.config.environments:
            try:
                agent_status.publish(self._control(env), self.config.name, agent_status.record(self, env, state))
            except Exception as exc:
                self._controls.pop(env, None)
                self.log.warning("couldn't publish the agent status", extra=fields(env=env, error=f"{type(exc).__name__}: {exc}"))

    def _can_send(self, route: str, url: str) -> bool:
        """Whether an agent can send to this address: a Teams webhook or a Power Automate flow. Email
        is sent from the KratosForge app, which has the company mail server; an agent has none."""
        if not url:
            return False
        warned: set[tuple[str, str]] = self.__dict__.setdefault("_route_warned", set())  # once per route and address
        try:
            deliver.check(url)
        except ValueError as exc:
            if (route, url) not in warned:
                warned.add((route, url))
                self.log.warning("an incident route's address can't be used; its messages go nowhere",
                                 extra=fields(route=route, error=str(exc)))
            return False
        if deliver.kind(url) == "email":
            if (route, url) not in warned:
                warned.add((route, url))
                self.log.warning(AGENT_NO_EMAIL, extra=fields(route=route))
            return False
        return True

    def _post(self, url: str, title: str, lines: list[str]) -> None:
        """Teams or a Power Automate flow (workloads/deliver.py), each message redacted and bounded."""
        deliver.poster(None, lambda u, t, ls: post_teams(u, t, ls))(url, title, lines)

    def incident_routing(self) -> Routing:
        """Where incident messages go: the [[incidents.route]] channels, and alerts.teams_webhook_env
        for anything none of them matches. Webhook addresses are read from the environment now."""
        inc = self.config.incidents
        routes = [Route(DEFAULT_ROUTE, self.config.teams_webhook)] if self._can_send(DEFAULT_ROUTE, self.config.teams_webhook) else []
        for r in inc.routes:
            url = os.environ.get(r["webhook_env"], "")
            if url:
                if self._can_send(r["name"], url):
                    routes.append(Route(r["name"], url, r["teams"], r["owners"], r["criticality"], r["kinds"], r["envs"]))
            else:
                self.log.warning("an incident route's webhook variable isn't set; its messages go nowhere",
                                 extra=fields(route=r["name"], variable=r["webhook_env"]))
        quiet = Quiet.parse(inc.quiet_hours, inc.quiet_except) if inc.quiet_hours else None
        return Routing(routes, quiet, inc.escalate_after_minutes * 60, inc.escalate_to)

    def evaluate_incidents(self) -> dict[str, list[dict[str, Any]]]:
        """Evaluate each environment's run log and agent records and bring its incident list up to
        date (workloads/incidents.py); escalate what nobody acknowledged; tell Teams. A failure is
        logged, never fatal: an environment that can't be read gets a "can't evaluate" incident,
        and its workload incidents stay as they are."""
        changed: dict[str, list[dict[str, Any]]] = {}
        by = f"agent {self.config.name}"
        now = dt.datetime.fromtimestamp(self.clock()).astimezone()
        routing = self.incident_routing()
        for env in self.config.environments:
            try:
                log = IncidentLog(self._control(env), env, clock=self.clock, monotonic=self.monotonic,
                                  first_seen=self._incident_seen.setdefault(env, {}))
            except Exception as exc:  # no control area: nowhere to keep incidents, so only the log says
                self._controls.pop(env, None)
                self.log.warning("couldn't open the incident list", extra=fields(env=env, error=f"{type(exc).__name__}: {exc}"))
                continue
            evaluated: set[str] = set()
            try:
                project = self.runtime._project()
                runs = self.runtime.meta_runs(env, 2000)
            except Exception as exc:
                signals, _ = evaluate.unreadable(env, exc)
            else:
                specs = {n: e.spec for n, e in project.entities.items()}
                signals = evaluate.signals(mesh_mod.project_name(project), specs, runs, env, now=now,
                                           team=mesh_mod.identity(project)["team"],
                                           promises=mesh_mod.promises(project))
                evaluated |= evaluate.WORKLOAD_KINDS
            try:
                signals += evaluate.agent_signals(read_status(log.backend), env, now, me=self.config.name)
                evaluated.add("agent_offline")
            except Exception as exc:  # agent records unreadable: their incidents stay as they are
                self.log.warning("couldn't read the agent records", extra=fields(env=env, error=f"{type(exc).__name__}: {exc}"))
            try:
                changes = log.sync(signals, evaluated=evaluated, by=by)
                if routing.escalate_after:
                    changes += log.escalate(routing.escalate_after, routing.escalate_to, by)
            except Exception as exc:
                self._controls.pop(env, None)
                self.log.warning("couldn't update the incident list", extra=fields(env=env, error=f"{type(exc).__name__}: {exc}"))
                continue
            for c in changes:
                self.log.info(f"incident {c['change']}", extra=fields(env=env, incident=c["incident"]["id"],
                                                                     title=c["incident"]["title"]))
            changed[env] = changes
        self._save_incident_clock()
        all_changes = [c for cs in changed.values() for c in cs]
        if routing.routes:  # also when nothing changed: messages left from before go out
            if self._outbox is None:
                self._outbox = Outbox(self.config.home / "alerts.sqlite3", clock=self.clock)
            result = send(all_changes, routing, self._outbox, f"agent {self.config.name}", self._post,
                          self.config.incidents.digest_over, now=now)
            self._count_dropped(result, "alerts.sqlite3")
            if result.get("error"):
                self.log.warning("couldn't post incidents to Teams; they'll go with the next try",
                                 extra=fields(queued=result["queued"], error=result["error"]))
        return changed

    def check_shortcuts(self) -> dict[str, dict[str, Any]]:
        """``[shortcut_health]``: check the shortcuts in each environment's workspaces and post each new
        break, and its fix, to Teams once (shortcut_watch.py). Only reads Fabric and OneLake. An
        environment that a live agent whose name sorts first also checks is left to that agent.
        A failure is logged, never fatal. Returns {env: {checked, broken} or {skipped: why}}."""
        url = self.config.shortcut_webhook
        if not url:
            self.log.warning("shortcut_health is on but its webhook variable isn't set; shortcuts aren't checked")
            return {}
        now = dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc)
        out: dict[str, dict[str, Any]] = {}
        found: list[dict[str, Any]] = []
        for env in self.config.shortcut_health.environments:
            try:
                workspaces = shortcut_watch.env_workspaces(self.runtime.environment_config(env))
            except Exception as exc:
                self.log.warning("couldn't read the project for the shortcut check", extra=fields(env=env, error=describe(exc)))
                out[env] = {"skipped": "the project can't be read"}
                continue
            if not workspaces:
                out[env] = {"skipped": "no workspace"}
                continue
            try:
                records = read_status(self._control(env))
            except Exception as exc:  # better a second message than none: check here
                self._controls.pop(env, None)
                records = []
                self.log.warning("couldn't read the agent records; checking shortcuts here", extra=fields(
                    env=env, error=describe(exc)))
            if not shortcut_watch.poster(self.config.name, records, now):
                out[env] = {"skipped": "another agent checks them"}
                continue
            try:
                # A token per call (the credential caches and refreshes it), so a long scan never runs on an expired one.
                rt = self.runtime
                rows, problems = shortcut_watch.scan(workspaces, FabricClient(rt.auth.fabric_token),
                                                     FabricClient(rt.onelake_token), FileStore(rt.auth.storage_token))
            except Exception as exc:
                self.log.warning("couldn't check the shortcuts", extra=fields(env=env, error=describe(exc)))
                out[env] = {"skipped": "the check failed"}
                continue
            for p in problems:
                self.log.warning("couldn't list some shortcuts", extra=fields(env=env, error=p))
            found += shortcut_watch.events(rows, env, now.isoformat(timespec="seconds"))
            out[env] = {"checked": len(rows), "broken": sum(r["health"] == "broken" for r in rows)}
            self.log.info("shortcuts checked", extra=fields(env=env, **out[env]))
        box = self.config.home / shortcut_watch.LEDGER
        checked = []
        for e in found:
            try:
                checked.append(validate(e))
            except ValueError as exc:  # one odd shortcut never holds back the others' messages
                self.log.warning("a shortcut message was left out", extra=fields(shortcut=str(e.get("id"))[:200],
                                                                                   error=describe(exc)))
        result = send_notices(checked, PlatformRouting([PlatformRoute(DEFAULT_ROUTE, url)]),
                              Outbox(box, clock=self.clock), Ledger(box, clock=self.clock), post_teams)
        self._count_dropped(result, box.name)
        if result.get("error"):
            self.log.warning("couldn't post shortcut breaks to Teams; they'll go with the next check",
                             extra=fields(queued=result["queued"], error=_without_webhook(result["error"], url)))
        return out

    def start_shortcut_check(self) -> threading.Thread | None:
        """Run ``check_shortcuts`` on its own thread, so a long scan doesn't hold up schedules, the
        mailbox or jobs. None while the previous check is still running: it is skipped, not queued."""
        if self._shortcut_thread is not None and self._shortcut_thread.is_alive():
            self.log.info("the last shortcut check is still running; skipping this one")
            return None

        def run() -> None:
            try:
                self.check_shortcuts()
            except Exception:  # a check must never take the agent down; the log has it
                self.log.exception("shortcut check crashed")

        self._shortcut_thread = threading.Thread(target=run, name="shortcut-health", daemon=True)
        self._shortcut_thread.start()
        return self._shortcut_thread

    # ---- adding work

    def enqueue(self, env: str, entities: list[str] | None = None, backfill: str = "", cascade: bool = False,
                source: str = "cli", dedupe_key: str | None = None) -> dict[str, Any]:
        if env not in self.config.environments:
            raise PermissionError(f"This agent may run {', '.join(self.config.environments)}, not {env}")
        payload = {"env": env, "entities": list(entities or []), "backfill": backfill, "cascade": cascade}
        job = self.queue.enqueue(KIND_META, payload, source=source, max_attempts=self.config.max_attempts,
                                 dedupe_key=dedupe_key)
        self.log.info("queued" if not job.get("deduplicated") else "already queued",
                      extra=fields(job_id=job["id"], env=env, entities=payload["entities"], source=source))
        return job

    def tick_schedules(self) -> list[str]:
        """Enqueue every schedule that is due. A schedule missed while the agent was down runs once
        when it starts again, not once per missed time. A shared schedule is written to its
        environment's mailbox instead (shared_schedules.py), so one agent of those sharing it runs it."""
        fired = []
        now = self.clock()
        for s in self.config.schedules:
            due = self.queue.schedule_next(s.id)
            if due is not None and due > now + _longest_wait(s.when):
                # Set before the clock went back: without this, a 15-minute schedule would stop for as
                # long as the jump. Worked out again from now, so nothing runs twice for one due time.
                self.log.warning("schedule's next run was further off than it can be; the clock went back, "
                                 "so it is worked out again", extra=fields(schedule=s.id))
                due = None
            if due is None or due <= now:
                if due is not None:
                    if s.shared:
                        self._submit_shared(s, due, now)
                    else:
                        self.enqueue(s.env, s.entities, s.backfill, source=f"schedule:{s.id}",
                                     dedupe_key=f"schedule:{s.id}")
                    fired.append(s.id)
                nxt = shared_schedules.next_aligned(s.when, now) if s.shared else None
                if nxt is None:
                    local = next_run(s.config, dt.datetime.fromtimestamp(now))
                    nxt = _epoch(local) if local is not None else None
                if nxt is not None:
                    self.queue.set_schedule_next(s.id, nxt)
        return fired

    def _submit_shared(self, s: Any, due: float, now: float) -> None:
        """One request for this due time in the mailbox, unless another agent wrote it first or its
        deadline passed. A mailbox that can't be reached is logged; the agents that can reach it run it."""
        try:
            rid, outcome = shared_schedules.submit(self._control(s.env), s, due, now, self.config.name)
        except Exception as exc:
            self._controls.pop(s.env, None)
            self.log.warning("couldn't write the shared schedule's request", extra=fields(
                schedule=s.id, env=s.env, error=f"{type(exc).__name__}: {exc}"))
            return
        if outcome == "late":
            self._missed_shared(s, rid, due)
            return
        self.log.info({"created": "shared schedule requested", "exists": "shared schedule already requested"}[outcome],
                      extra=fields(schedule=s.id, env=s.env, request_id=rid))

    def _missed_shared(self, s: Any, rid: str, due: float) -> None:
        """A due time this agent found past its deadline. If another agent wrote its request, it ran
        there. If none did, nobody ran it: logged, and one alert to the default route. Each agent
        alerts once for a due time; agents that were all away each send one."""
        if rid in self._missed:
            return
        self._missed.add(rid)
        try:
            requested = self._control(s.env).read(f"requests/{rid}.json") is not None
        except Exception as exc:  # can't tell: say so rather than stay quiet about a run that may not have happened
            self._controls.pop(s.env, None)
            self.log.warning("couldn't check whether another agent requested the shared schedule", extra=fields(
                schedule=s.id, env=s.env, request_id=rid, error=f"{type(exc).__name__}: {exc}"))
            requested = None
        if requested:
            self.log.info("shared schedule past its deadline; not requested",
                          extra=fields(schedule=s.id, env=s.env, request_id=rid))
            return
        when = shared_schedules.iso(due)
        self.log.warning("shared scheduled run missed: no agent requested it before its deadline",
                         extra=fields(schedule=s.id, env=s.env, request_id=rid, due=when))
        first = (f"Schedule {s.id}, due {when}: no agent requested it before its deadline, so it didn't run."
                 if requested is False else
                 f"Schedule {s.id}, due {when}: it passed its deadline here, and the {s.env.title()} mailbox "
                 "couldn't be read to check whether another agent ran it.")
        self._notify(f"KratosForge agent {self.config.name}: a shared {s.env.title()} run was missed",
                     [first, "Check that the agents with this schedule are running and can reach the mailbox. "
                             "The next due time runs as usual.",
                      f"To run it now: fabric-desktop agent enqueue {s.env} {' '.join(s.entities)}".rstrip()],
                     schedule=s.id, request_id=rid)

    def has_free_worker(self) -> bool:
        """Whether a request claimed now would start at once: fewer jobs running or queued here than workers."""
        busy = len([f for f in self._running.values() if not f.done()])
        queued = sum(self.queue.counts(env).get("queued", 0) for env in self.config.environments)
        return busy + queued < self.config.concurrency

    # ---- doing work

    def _heartbeat(self, job: dict[str, Any], done: threading.Event, lost: threading.Event | None = None) -> None:
        """Renew the job's lease every third of it until ``done``. A renewal that fails (the queue
        file busy, a disk error) is tried again at the next beat, so one failure doesn't let the lease
        lapse; when the lease is gone, ``lost`` is set and the run stops before its next table."""
        beat = self.config.job_lease_seconds / 3
        while not done.wait(beat):
            try:
                ours = self.queue.heartbeat(job["id"], self.config.name, self.config.job_lease_seconds,
                                            claim=job.get("claim_id"))
            except Exception as exc:  # noqa: BLE001 - try again at the next beat
                self.log.warning("couldn't renew the job's lease; trying again", extra=fields(
                    job_id=job["id"], error=describe(exc)))
                continue
            if not ours:
                self.log.warning("lost the job's lease", extra=fields(job_id=job["id"]))
                if lost is not None:
                    lost.set()
                return

    SETTLE_WAITS = (1.0, 4.0)  # seconds between tries to record a run's outcome (tests shorten them)

    def _settle(self, job: dict[str, Any], call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Record how a run went (succeed, fail, retry_later, cancel). The queue file can stay locked
        longer than its 30 s busy wait (another process, a virus scanner); the run's work is done, so
        try again rather than lose its outcome and run the job again. Repeating is safe: each call
        changes the job only while this claim still holds it (claim_id)."""
        for wait_s in (*self.SETTLE_WAITS, None):
            try:
                return call(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if wait_s is None:
                    raise
                self.log.warning("couldn't record the job's outcome; trying again", extra=fields(
                    job_id=job["id"], error=describe(exc)))
                time.sleep(wait_s)
        return None  # not reached

    def run_job(self, job: dict[str, Any]) -> None:
        p, owner = job["payload"], self.config.name
        ctx = {"job_id": job["id"], "env": p.get("env"), "attempt": job["attempts"], "source": job["source"]}
        self.log.info("job started", extra=fields(**ctx, entities=p.get("entities")))
        claim = job.get("claim_id")
        done, lost = threading.Event(), threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(job, done, lost), daemon=True)
        beat.start()
        t0 = time.monotonic()
        mailbox = bool(self.mailbox and str(job.get("source") or "").startswith("pipeline:"))

        def guard() -> None:  # before each table: stop if the job isn't this run's any more
            if lost.is_set():
                raise JobLeaseLost("this run's lease on the job ran out, so the job was put back to run again")
            if mailbox:
                self._still_ours(job)
        try:
            results = self.runtime.run_environment(p["env"], p.get("entities") or None, p.get("backfill") or None,
                                                   bool(p.get("cascade")), job=job, guard=guard)
        except JobLeaseLost as exc:  # the job is queued (or running) again: leave it to that attempt
            self._audit(job, "lease_lost", time.monotonic() - t0, error=str(exc))
            self.log.warning("job stopped: its lease ran out", extra=fields(**ctx, error=str(exc)))
            return
        except Abandoned as exc:
            self._settle(job, self.queue.cancel, job["id"], str(exc), owner=owner, claim=claim)
            self._audit(job, "handed_over", time.monotonic() - t0, error=str(exc))
            self.log.warning("job stopped: another agent took its request over", extra=fields(**ctx, error=str(exc)))
            return
        except Exception as exc:
            from .retrying import is_retryable
            error = f"{type(exc).__name__}: {exc}"
            self._audit(job, "error", time.monotonic() - t0, error=error)
            if is_retryable(exc):
                after = self._settle(job, self.queue.retry_later, job["id"], owner, error, claim=claim)
                self.log.warning("job will retry" if after and after["state"] == "queued" else "job gave up",
                                 extra=fields(**ctx, error=error))
                if after and after["state"] == "dead":
                    self._alert(job, after["error"])
            else:
                self._settle(job, self.queue.fail, job["id"], owner, error, claim=claim)
                self.log.error("job failed", extra=fields(**ctx, error=error))
                self._alert(job, error)
            return
        finally:
            done.set()
        seconds = round(time.monotonic() - t0, 1)
        summary = self._summary(results)
        failed = [r for r in results if r["status"] not in ("ok", "skipped")]
        result, reason = audit_outcome(results)  # the same reading the app and PC schedules give a run
        retry = [r["entity"] for r in failed if r.get("retryable")]
        if not failed:
            if self._settle(job, self.queue.succeed, job["id"], owner, summary, claim=claim):
                self._audit(job, "ok", seconds, **summary)
                self.log.info("job succeeded", extra=fields(**ctx, seconds=seconds, **summary))
            else:  # its lease ran out at the end: the job's next attempt records how it went
                why = "this run's lease on the job ran out before it could record it, so the job runs again"
                self._audit(job, "lease_lost", seconds, error=why, **summary)
                self.log.warning("job finished, but its lease ran out", extra=fields(**ctx, seconds=seconds, **summary))
            return
        self._audit(job, result, seconds, error="; ".join(f"{r['entity']}: {r['message']}" for r in failed),
                    reason=reason, **summary)
        if len(retry) == len(failed):
            # Only busy tables or network trouble: try again, for just those and what they block.
            skipped = [r["entity"] for r in results if r["status"] == "skipped"]
            error = "; ".join(f"{r['entity']}: {r['message']}" for r in failed)
            after = self._settle(job, self.queue.retry_later, job["id"], owner, error, claim=claim)
            if after and after["state"] == "queued" and p.get("entities") != retry + skipped and not p.get("cascade"):
                self._narrow(job["id"], retry + skipped)
            self.log.warning("job will retry", extra=fields(**ctx, seconds=seconds, error=error, **summary))
            if after and after["state"] == "dead":
                self._alert(job, after["error"])
        else:
            error = "; ".join(f"{r['entity']}: {r['message']}" for r in failed)
            self._settle(job, self.queue.fail, job["id"], owner, error, summary, claim=claim)
            self.log.error("job failed", extra=fields(**ctx, seconds=seconds, error=error, **summary))
            self._alert(job, error)

    def _still_ours(self, job: dict[str, Any]) -> None:
        """Before each table: stop if another agent took this job's pipeline request over."""
        other = self.mailbox.taken_over(job) if self.mailbox else ""
        if other:
            raise Abandoned(f"agent {other} took this request over")

    def _audit(self, job: dict[str, Any], outcome: str, seconds: float, error: str = "", reason: str = "",
               **summary: Any) -> None:
        """One audit line per job run (FD-125), next to the desktop app's lines for the same actions."""
        p = job["payload"]
        params = {"job_id": job["id"], "env": p.get("env"), "entities": p.get("entities"),
                  "backfill": p.get("backfill"), "cascade": p.get("cascade"), "source": job["source"],
                  "attempt": job["attempts"], "agent": self.config.name, **summary}
        try:
            self.audit.record("agent_job", outcome, seconds, params, error=error, reason=reason)
        except Exception as exc:  # a full disk mustn't stop the job; the log has it
            self.log.warning("couldn't write the audit line", extra=fields(job_id=job["id"], error=str(exc)))

    def _narrow(self, job_id: str, entities: list[str]) -> None:
        """A retry runs only the tables that didn't finish; the rest are written already."""
        job = self.queue.get(job_id)
        if job:
            self.queue.set_payload(job_id, job["payload"] | {"entities": entities})

    @staticmethod
    def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
        return {"tables": len(results), "ok": sum(r["status"] == "ok" for r in results),
                "failed": sum(r["status"] not in ("ok", "skipped") for r in results),
                "rows_written": sum(r.get("rows_written") or 0 for r in results),
                "run_ids": [r["run_id"] for r in results if r.get("run_id")]}

    def _alert_outbox(self) -> Outbox:
        if self._alert_box is None:
            self._alert_box = Outbox(self.config.home / "job_alerts.sqlite3", clock=self.clock)
        return self._alert_box

    def send_waiting_alerts(self) -> None:
        """Post job alerts Teams didn't take before (no network, Teams down), marked as delayed. They
        wait in job_alerts.sqlite3 in the agent's home and are dropped after three days."""
        url = self.config.teams_webhook
        if not self._can_send(DEFAULT_ROUTE, url) or (self._alert_box is None and not (self.config.home / "job_alerts.sqlite3").exists()):
            return
        with self._alert_lock:
            result = send([], single(url), self._alert_outbox(), f"agent {self.config.name}", self._post)
        self._count_dropped(result, "job_alerts.sqlite3")
        if result.get("error"):
            self.log.warning("couldn't post waiting alerts to Teams; they'll go with the next try",
                             extra=fields(error=_without_webhook(result["error"], url)))

    def _alert(self, job: dict[str, Any], error: str, crashed: bool = False) -> None:
        env = str(job["payload"].get("env", "")).title()
        title = f"KratosForge agent {self.config.name}: a {env} job didn't finish"
        lines = [f"Job {job['id']} ({job['source'] or 'manual'})", error[:1500]]
        if crashed:
            lines.append(CRASHED_NEXT)
        lines.append(PIPELINE_NEXT if str(job.get("dedupe_key") or "").startswith("pipeline:")
                     else f"Fix the cause, then run: fabric-desktop agent requeue {job['id']}")
        self._notify(title, lines, job_id=job["id"])

    def _notify(self, title: str, lines: list[str], **about: Any) -> None:
        """Post one message to the default route (alerts.teams_webhook_env). One Teams or the flow
        didn't take waits in job_alerts.sqlite3 and goes with the next try; while the route rests
        after failures (notify.py), it waits there without a request. ``about``: log fields."""
        url = self.config.teams_webhook
        if not self._can_send(DEFAULT_ROUTE, url):
            return
        self.send_waiting_alerts()  # older alerts first
        with self._alert_lock:
            # No outbox file yet means nothing ever failed here: no need to make one to find out.
            box = (self._alert_outbox() if self._alert_box is not None or (self.config.home / "job_alerts.sqlite3").exists()
                   else None)
            resting = box.resting({DEFAULT_ROUTE: url}) if box else {}
            if box and resting:
                box.add(title, lines, resting[DEFAULT_ROUTE])
                self.log.info("alert waits: Teams failed recently, so it goes with the next try", extra=fields(**about))
                return
            try:
                self._post(url, title, lines)
                if box:
                    box.route_ok(DEFAULT_ROUTE)
                return
            except Exception as exc:  # kept, and sent with the next try
                problem = _without_webhook(failure(exc), url)
                self._alert_outbox().add(title, lines, problem)
                self._alert_outbox().route_failed(DEFAULT_ROUTE, url, problem)
        self.log.warning("couldn't post the alert to Teams; it goes with the next try",
                         extra=fields(**about, error=problem))

    def messages_waiting(self) -> int | None:
        """Teams or flow messages waiting in this agent's outboxes (for the status record); None
        when one can't be read. Outboxes that were never made hold none and aren't made here."""
        try:
            total = 0
            for name in ("alerts.sqlite3", "job_alerts.sqlite3", shortcut_watch.LEDGER):
                path = self.config.home / name
                if path.exists():
                    total += Outbox(path, clock=self.clock).count()
            return total
        except Exception as exc:  # noqa: BLE001 - a count that can't be read never stops the status record
            self.log.warning("couldn't count the waiting messages", extra=fields(error=describe(exc)))
            return None

    def _count_dropped(self, result: dict[str, Any], outbox: str) -> None:
        """Log and count messages an outbox dropped unsent after three days (the status record shows the count)."""
        n = int(result.get("dropped") or 0)
        if not n:
            return
        with self._dropped_lock:
            self.messages_dropped += n
        self.log.warning("Teams or flow messages waited three days without being sent and were dropped",
                         extra=fields(dropped=n, outbox=outbox))

    def _alert_queue_set_aside(self) -> None:
        """Once, on the first step: the queue file couldn't be read and was moved aside at start."""
        moved, self._queue_set_aside = self._queue_set_aside, None
        if moved is None:
            return
        self._notify(f"KratosForge agent {self.config.name}: its job queue file couldn't be read",
                     [f"It was moved to {moved.name} in the agent's home, and the agent started with an empty queue.",
                      "Jobs that were waiting in it won't run. Schedules add their next runs again on their own; "
                      "queue any other job again with fabric-desktop agent enqueue."],
                     moved_to=moved.name)

    # ---- the loop

    def _woke(self) -> bool:
        """Whether the clock moved more than a status beat since the last pass, either way: the
        machine slept or hibernated, or the clock was changed."""
        now, last = self.clock(), self._stepped_at
        self._stepped_at = now
        return last is not None and (now < last or now - last > self.config.status_seconds + self.config.poll_seconds)

    def _renew_running(self) -> None:
        """Renew the leases of the jobs running here. After a sleep, or the clock jumping forward,
        their leases look run out; without this, recovery would put a job still running here back
        in the queue and start it a second time."""
        for job_id, future in list(self._running.items()):
            if future.done():
                continue
            try:
                self.queue.heartbeat(job_id, self.config.name, self.config.job_lease_seconds,
                                     claim=self._claims.get(job_id))
            except Exception as exc:  # noqa: BLE001 - its own heartbeat tries again
                self.log.warning("couldn't renew the job's lease; trying again", extra=fields(
                    job_id=job_id, error=describe(exc)))

    def step(self) -> None:
        """One pass of the loop; ``run_forever`` calls it every ``poll_seconds``."""
        self._alert_queue_set_aside()
        self._renew_running()  # before recovery, which would otherwise take back a job still running here
        if self._woke():
            # Say this agent is alive before anything slower, so peers judging it by its record
            # don't take its requests over while it catches up.
            self.log.info("the clock moved more than a status beat since the last pass (sleep, or a clock "
                          "change); publishing the status first")
            self._status_at = self.clock()
            self.publish_status()
        for job_id in self.queue.recover(max_lease=self.config.job_lease_seconds):
            job = self.queue.get(job_id)
            if job and job["state"] == "dead":  # its worker stopped on every attempt (say, a run that takes the agent down)
                self.log.error("job gave up: its worker stopped on every attempt",
                               extra=fields(job_id=job_id, error=job["error"]))
                self._alert(job, job["error"] or "", crashed=True)
            else:
                self.log.warning("job put back in the queue: its worker stopped", extra=fields(job_id=job_id))
        self.tick_schedules()
        if self.mailbox and _due(self._mailbox_at, self.config.pipeline.poll_seconds, self.clock()):
            self._mailbox_at = self.clock()
            self.mailbox.poll()
        self._running = {k: f for k, f in self._running.items() if not f.done()}
        self._claims = {k: c for k, c in self._claims.items() if k in self._running}
        while not self.stopping.is_set() and len(self._running) < self.config.concurrency:
            job = self.queue.claim(self.config.name, self.config.job_lease_seconds)
            if job is None:
                break
            self._claims[job["id"]] = job.get("claim_id")
            self._running[job["id"]] = self._pool.submit(self._guarded, job)
        inc = self.config.incidents
        if inc.enabled and _due(self._incidents_at, inc.seconds, self.clock()):
            self._incidents_at = self.clock()
            self.evaluate_incidents()
        if _due(self._status_at, self.config.status_seconds, self.clock()):
            self._status_at = self.clock()
            self.publish_status()
            self.send_waiting_alerts()
        sh = self.config.shortcut_health
        if sh.enabled and _due(self._shortcuts_at, sh.minutes * 60, self.clock()):
            self._shortcuts_at = self.clock()  # after the status record, which tells other agents this one checks
            self.start_shortcut_check()
        if _due(self._pruned_at, 86400, self.clock()):
            self._pruned_at = self.clock()
            self.prune_queue()
            self.sweep_home()
        if _due(self._policy_at, POLICY_CHECK_SECONDS, self.clock()):
            self._policy_at = self.clock()
            self.check_policy()

    def check_policy(self) -> None:
        """Records a change to the policy file in the agent's audit log (policy_file_seen the first time,
        policy_file_changed after), against the record in its home's policy-seen.json. A check that
        fails is logged and tried again at the next one; it never stops the loop."""
        try:
            policy_watch.check(self.config.home, self.audit)
        except Exception as exc:  # noqa: BLE001 - a record that can't be made never stops jobs
            self.log.warning("couldn't check the policy file", extra=fields(error=f"{type(exc).__name__}: {exc}"))

    def prune_queue(self) -> None:
        """Remove finished jobs older than queue_retention_days. Last in the pass, after the mailbox has
        brought pipeline statuses up to date. A failure is logged and tried again the next day."""
        try:
            gone = self.queue.prune(self.config.queue_retention_days)
        except Exception as exc:
            self.log.warning("couldn't prune the queue", extra=fields(error=f"{type(exc).__name__}: {exc}"))
            return
        if gone:
            self.log.info("finished jobs removed from the queue",
                          extra=fields(jobs=len(gone), older_than_days=self.config.queue_retention_days))

    def sweep_home(self) -> None:
        """Delete local copies of lakehouse data the agent's runs left in its data folder
        (``<home>/data``, Runtime.root, where the download cache and staging live) unused for longer
        than the organization allows, as the app does with its own folder (retention.py,
        local_retention_days): at start, then daily, after the queue is pruned. Only the sweep's cache
        folders are touched: never the queue, audit, logs, settings or a project's This PC data. A
        failure is logged and tried again the next day."""
        from . import app_settings, retention
        try:
            days = int(app_settings.value("local_retention_days"))
            result = retention.sweep(self.runtime.root, days)  # file times are real ones, not the loop's clock
        except Exception as exc:  # noqa: BLE001 - a cache sweep never stops jobs
            self.log.warning("couldn't clean up local copies", extra=fields(error=f"{type(exc).__name__}: {exc}"))
            return
        if result["removed"]:
            self.log.info("local copies removed from the agent's home",
                          extra=fields(items=len(result["removed"]), bytes=result["bytes"], older_than_days=days))

    def _guarded(self, job: dict[str, Any]) -> None:
        if self.mailbox:
            if self.mailbox.cancel_requested(job):
                self.queue.cancel(job["id"], "the pipeline cancelled it", owner=self.config.name)
                self.mailbox.job_changed(self.queue.get(job["id"]) or job)
                return
            other = self.mailbox.taken_over(job)
            if other:  # it was taken over while this agent was away: not this agent's to run
                self.queue.cancel(job["id"], f"agent {other} took this request over", owner=self.config.name)
                return
            self.mailbox.job_changed(job)
        try:
            self.run_job(job)
        except Exception:  # never let one job take the agent down
            self.log.exception("job crashed", extra=fields(job_id=job["id"]))
        if self.mailbox:
            self.mailbox.job_changed(self.queue.get(job["id"]) or job)

    def run_forever(self, install_signals: bool = True) -> None:
        if install_signals:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: self.stop())
        try:
            self.hold_control_folders()
        except ControlFolderInUse as exc:
            self.log.error(f"agent not started: {exc}")
            raise
        self.log.info("agent started", extra=fields(concurrency=self.config.concurrency,
                                                    environments=self.config.environments,
                                                    schedules=[s.id for s in self.config.schedules],
                                                    project=str(self.config.project)))
        from .credentials import KEY_FILE_OPEN, key_file_open
        if key_file_open(self.config.auth):
            self.log.warning(KEY_FILE_OPEN)
        while not self.stopping.is_set():
            try:
                self.step()
            except Exception:
                self.log.exception("loop error; carrying on")
            self.stopping.wait(self.config.poll_seconds)
        self.log.info("agent stopping: waiting for running jobs", extra=fields(running=list(self._running)))
        self.publish_status("stopping")
        running = [f for f in self._running.values() if not f.done()]
        while running:  # keep the record fresh, or a peer takes over a request still running here
            running = list(wait(running, timeout=self.config.status_seconds).not_done)
            if running:
                self.publish_status("stopping")
        self._pool.shutdown(wait=True)
        self.publish_status("stopped")
        self.release_control_folders()
        self.log.info("agent stopped")

    def stop(self) -> None:
        self.stopping.set()

    def status(self) -> dict[str, Any]:
        jobs = self.queue.list(20)
        return {"worker": self.config.name, "counts": self.queue.counts(),
                "schedules": [{"id": s.id, "env": s.env,
                               "next": dt.datetime.fromtimestamp(n).isoformat(timespec="minutes")
                               if (n := self.queue.schedule_next(s.id)) else None} for s in self.config.schedules],
                "jobs": [{k: j[k] for k in ("id", "state", "source", "attempts", "error", "result")}
                         | {"env": j["payload"].get("env"), "entities": j["payload"].get("entities"),
                            "created": dt.datetime.fromtimestamp(j["created_at"]).isoformat(timespec="seconds")}
                         for j in jobs]}
