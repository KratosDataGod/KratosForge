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
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any, Callable
from urllib.parse import urlsplit

from . import agent_status, shared_schedules, shortcut_watch
from .agent_config import AgentConfig
from .audit import AuditLog
from .dataops_service import post_teams
from .fabric_api import FabricClient
from .jobqueue import JobQueue
from .local_schedules import next_run
from .logs import fields
from .mailbox import Mailbox
from .meta import mesh as mesh_mod
from .meta.engine import Abandoned
from .onelake import FileStore
from .redact import describe
from .runtime import Runtime
from .workloads import evaluate
from .workloads.agents import read_status
from .workloads.incidents import IncidentLog
from .workloads.notify import DEFAULT_ROUTE, Outbox, Quiet, Route, Routing, failure, send, single
from .workloads.platform_notify import Ledger, PlatformRoute, PlatformRouting, validate
from .workloads.platform_notify import send as send_notices

KIND_META = "meta"


def _epoch(local: dt.datetime) -> float:
    return local.timestamp()  # naive datetimes are this machine's local time, as schedules are



def _without_webhook(text: str, url: str) -> str:
    """``text`` with the webhook's path and query replaced: a legacy Office 365 connector URL carries its
    secret in the path, which requests puts in connection errors and redact.secrets doesn't know (QA-016)."""
    parts = urlsplit(url)
    for piece in sorted({url, parts.path + ("?" + parts.query if parts.query else ""), parts.path, parts.query},
                        key=len, reverse=True):
        if len(piece) > 1:
            text = text.replace(piece, "[webhook]")
    return text

class Agent:
    def __init__(self, config: AgentConfig, runtime: Runtime | None = None, queue: JobQueue | None = None,
                 log: logging.Logger | None = None, clock: Callable[[], float] = time.time):
        self.config = config
        self.runtime = runtime or Runtime(config)
        self.queue = queue or JobQueue(config.home / "queue.sqlite3", clock=clock,
                                       backoff_seconds=config.backoff_seconds,
                                       max_backoff_seconds=config.max_backoff_seconds)
        self.log = log or logging.getLogger("fabric_desktop.agent")
        self.clock = clock
        self.stopping = threading.Event()
        self._running: dict[str, Future[None]] = {}
        self._pool = ThreadPoolExecutor(max_workers=config.concurrency, thread_name_prefix="job")
        self.started_at = clock()
        self._status_at = 0.0
        self._pruned_at: float | None = None
        self._controls: dict[str, Any] = {}
        self.mailbox: Mailbox | None = None
        self._mailbox_at = 0.0
        self._incidents_at = 0.0
        self._shortcuts_at: float | None = None
        self._shortcut_thread: threading.Thread | None = None
        self._outbox: Outbox | None = None
        self._alert_box: Outbox | None = None  # "job didn't finish" alerts Teams didn't take yet
        self._alert_lock = threading.Lock()  # workers alert at once; one send at a time posts each just once
        self.audit = AuditLog(config.home / "audit", app="agent")
        if config.pipeline.enabled:
            p = config.pipeline
            self.mailbox = Mailbox(self, self._control, p.environments, p.entities, p.allow_backfill,
                                   p.retention_days, known_entities=self.runtime.entity_names, claim=p.claim,
                                   take_over=p.take_over, max_attempts=config.max_attempts)

    def _control(self, env: str) -> Any:
        """The environment's control files (mailbox and status records), built once."""
        if env not in self._controls:
            self._controls[env] = self.runtime.control_backend(env)
        return self._controls[env]

    def publish_status(self, state: str = "running") -> None:
        """Write agents/<name>.json to each environment's control area. A failure is logged, never fatal."""
        for env in self.config.environments:
            try:
                agent_status.publish(self._control(env), self.config.name, agent_status.record(self, env, state))
            except Exception as exc:
                self._controls.pop(env, None)
                self.log.warning("couldn't publish the agent status", extra=fields(env=env, error=f"{type(exc).__name__}: {exc}"))

    def incident_routing(self) -> Routing:
        """Where incident messages go: the [[incidents.route]] channels, and alerts.teams_webhook_env
        for anything none of them matches. Webhook addresses are read from the environment now."""
        inc = self.config.incidents
        routes = [Route(DEFAULT_ROUTE, self.config.teams_webhook)] if self.config.teams_webhook else []
        for r in inc.routes:
            url = os.environ.get(r["webhook_env"], "")
            if url:
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
                log = IncidentLog(self._control(env), env, clock=self.clock)
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
                                           team=mesh_mod.identity(project)["team"])
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
        all_changes = [c for cs in changed.values() for c in cs]
        if routing.routes:  # also when nothing changed: messages left from before go out
            if self._outbox is None:
                self._outbox = Outbox(self.config.home / "alerts.sqlite3", clock=self.clock)
            result = send(all_changes, routing, self._outbox, f"agent {self.config.name}", post_teams,
                          self.config.incidents.digest_over, now=now)
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
        self.log.info({"created": "shared schedule requested", "exists": "shared schedule already requested",
                       "late": "shared schedule past its deadline; not requested"}[outcome],
                      extra=fields(schedule=s.id, env=s.env, request_id=rid))

    def has_free_worker(self) -> bool:
        """Whether a request claimed now would start at once: fewer jobs running or queued here than workers."""
        busy = len([f for f in self._running.values() if not f.done()])
        queued = sum(self.queue.counts(env).get("queued", 0) for env in self.config.environments)
        return busy + queued < self.config.concurrency

    # ---- doing work

    def _heartbeat(self, job: dict[str, Any], done: threading.Event) -> None:
        beat = self.config.job_lease_seconds / 3
        while not done.wait(beat):
            if not self.queue.heartbeat(job["id"], self.config.name, self.config.job_lease_seconds):
                self.log.warning("lost the job's lease", extra=fields(job_id=job["id"]))
                return

    def run_job(self, job: dict[str, Any]) -> None:
        p, owner = job["payload"], self.config.name
        ctx = {"job_id": job["id"], "env": p.get("env"), "attempt": job["attempts"], "source": job["source"]}
        self.log.info("job started", extra=fields(**ctx, entities=p.get("entities")))
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(job, done), daemon=True)
        beat.start()
        t0 = time.monotonic()
        extra: dict[str, Any] = {}
        if self.mailbox and str(job.get("source") or "").startswith("pipeline:"):
            extra["guard"] = lambda: self._still_ours(job)
        try:
            results = self.runtime.run_environment(p["env"], p.get("entities") or None, p.get("backfill") or None,
                                                   bool(p.get("cascade")), job=job, **extra)
        except Abandoned as exc:
            self.queue.cancel(job["id"], str(exc), owner=owner)
            self._audit(job, "handed_over", time.monotonic() - t0, error=str(exc))
            self.log.warning("job stopped: another agent took its request over", extra=fields(**ctx, error=str(exc)))
            return
        except Exception as exc:
            from .retrying import is_retryable
            error = f"{type(exc).__name__}: {exc}"
            self._audit(job, "error", time.monotonic() - t0, error=error)
            if is_retryable(exc):
                after = self.queue.retry_later(job["id"], owner, error)
                self.log.warning("job will retry" if after and after["state"] == "queued" else "job gave up",
                                 extra=fields(**ctx, error=error))
                if after and after["state"] == "dead":
                    self._alert(job, after["error"])
            else:
                self.queue.fail(job["id"], owner, error)
                self.log.error("job failed", extra=fields(**ctx, error=error))
                self._alert(job, error)
            return
        finally:
            done.set()
        seconds = round(time.monotonic() - t0, 1)
        summary = self._summary(results)
        failed = [r for r in results if r["status"] not in ("ok", "skipped")]
        retry = [r["entity"] for r in failed if r.get("retryable")]
        self._audit(job, "error" if failed else "ok", seconds,
                    error="; ".join(f"{r['entity']}: {r['message']}" for r in failed), **summary)
        if not failed:
            self.queue.succeed(job["id"], owner, summary)
            self.log.info("job succeeded", extra=fields(**ctx, seconds=seconds, **summary))
        elif len(retry) == len(failed):
            # Only busy tables or network trouble: try again, for just those and what they block.
            skipped = [r["entity"] for r in results if r["status"] == "skipped"]
            error = "; ".join(f"{r['entity']}: {r['message']}" for r in failed)
            after = self.queue.retry_later(job["id"], owner, error)
            if after and after["state"] == "queued" and p.get("entities") != retry + skipped and not p.get("cascade"):
                self._narrow(job["id"], retry + skipped)
            self.log.warning("job will retry", extra=fields(**ctx, seconds=seconds, error=error, **summary))
            if after and after["state"] == "dead":
                self._alert(job, after["error"])
        else:
            error = "; ".join(f"{r['entity']}: {r['message']}" for r in failed)
            self.queue.fail(job["id"], owner, error, summary)
            self.log.error("job failed", extra=fields(**ctx, seconds=seconds, error=error, **summary))
            self._alert(job, error)

    def _still_ours(self, job: dict[str, Any]) -> None:
        """Before each table: stop if another agent took this job's pipeline request over."""
        other = self.mailbox.taken_over(job) if self.mailbox else ""
        if other:
            raise Abandoned(f"agent {other} took this request over")

    def _audit(self, job: dict[str, Any], outcome: str, seconds: float, error: str = "", **summary: Any) -> None:
        """One audit line per job run (FD-125), next to the desktop app's lines for the same actions."""
        p = job["payload"]
        params = {"job_id": job["id"], "env": p.get("env"), "entities": p.get("entities"),
                  "backfill": p.get("backfill"), "cascade": p.get("cascade"), "source": job["source"],
                  "attempt": job["attempts"], "agent": self.config.name, **summary}
        try:
            self.audit.record("agent_job", outcome, seconds, params, error=error)
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
        if not url or (self._alert_box is None and not (self.config.home / "job_alerts.sqlite3").exists()):
            return
        with self._alert_lock:
            result = send([], single(url), self._alert_outbox(), f"agent {self.config.name}", post_teams)
        if result.get("error"):
            self.log.warning("couldn't post waiting alerts to Teams; they'll go with the next try",
                             extra=fields(error=_without_webhook(result["error"], url)))

    def _alert(self, job: dict[str, Any], error: str) -> None:
        url = self.config.teams_webhook
        if not url:
            return
        self.send_waiting_alerts()  # older alerts first
        env = str(job["payload"].get("env", "")).title()
        title = f"KratosForge agent {self.config.name}: a {env} job didn't finish"
        lines = [f"Job {job['id']} ({job['source'] or 'manual'})", error[:1500]]
        with self._alert_lock:
            try:
                post_teams(url, title, lines)
                return
            except Exception as exc:  # kept, and sent with the next try
                problem = _without_webhook(failure(exc), url)
                self._alert_outbox().add(title, lines, problem)
        self.log.warning("couldn't post the alert to Teams; it goes with the next try",
                         extra=fields(job_id=job["id"], error=problem))

    # ---- the loop

    def step(self) -> None:
        """One pass of the loop; ``run_forever`` calls it every ``poll_seconds``."""
        for job_id in self.queue.recover():
            self.log.warning("job put back in the queue: its worker stopped", extra=fields(job_id=job_id))
        self.tick_schedules()
        if self.mailbox and self.clock() - self._mailbox_at >= self.config.pipeline.poll_seconds:
            self._mailbox_at = self.clock()
            self.mailbox.poll()
        self._running = {k: f for k, f in self._running.items() if not f.done()}
        while not self.stopping.is_set() and len(self._running) < self.config.concurrency:
            job = self.queue.claim(self.config.name, self.config.job_lease_seconds)
            if job is None:
                break
            self._running[job["id"]] = self._pool.submit(self._guarded, job)
        inc = self.config.incidents
        if inc.enabled and self.clock() - self._incidents_at >= inc.seconds:
            self._incidents_at = self.clock()
            self.evaluate_incidents()
        if self.clock() - self._status_at >= self.config.status_seconds:
            self._status_at = self.clock()
            self.publish_status()
            self.send_waiting_alerts()
        sh = self.config.shortcut_health
        if sh.enabled and (self._shortcuts_at is None or self.clock() - self._shortcuts_at >= sh.minutes * 60):
            self._shortcuts_at = self.clock()  # after the status record, which tells other agents this one checks
            self.start_shortcut_check()
        if self._pruned_at is None or self.clock() - self._pruned_at >= 86400:
            self._pruned_at = self.clock()
            self.prune_queue()

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
        self.log.info("agent started", extra=fields(concurrency=self.config.concurrency,
                                                    environments=self.config.environments,
                                                    schedules=[s.id for s in self.config.schedules],
                                                    project=str(self.config.project)))
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
