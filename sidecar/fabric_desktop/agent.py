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
anything cut off is picked up again by the next start.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from . import agent_status
from .agent_config import AgentConfig
from .dataops_service import post_teams
from .jobqueue import JobQueue
from .local_schedules import next_run
from .logs import fields
from .mailbox import Mailbox
from .meta import mesh as mesh_mod
from .runtime import Runtime
from .workloads import evaluate, model
from .workloads.incidents import IncidentLog
from .workloads.agents import read_status
from .workloads.notify import DEFAULT_ROUTE, Outbox, Quiet, Route, Routing, send

KIND_META = "meta"


def _epoch(local: dt.datetime) -> float:
    return local.timestamp()  # naive datetimes are this machine's local time, as schedules are


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
        self._controls: dict[str, Any] = {}
        self.mailbox: Mailbox | None = None
        self._mailbox_at = 0.0
        self._incidents_at = 0.0
        self._outbox: Outbox | None = None
        if config.pipeline.enabled:
            p = config.pipeline
            self.mailbox = Mailbox(self, self._control, p.environments, p.entities, p.allow_backfill,
                                   p.retention_days, known_entities=self.runtime.entity_names)

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
                state, message = model.classify_error(exc)
                signals = [evaluate.cant_evaluate(env, state, message)]
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
        when it starts again, not once per missed time."""
        fired = []
        now = self.clock()
        for s in self.config.schedules:
            due = self.queue.schedule_next(s.id)
            if due is None or due <= now:
                if due is not None:
                    self.enqueue(s.env, s.entities, s.backfill, source=f"schedule:{s.id}",
                                 dedupe_key=f"schedule:{s.id}")
                    fired.append(s.id)
                nxt = next_run(s.config, dt.datetime.fromtimestamp(now))
                if nxt is not None:
                    self.queue.set_schedule_next(s.id, _epoch(nxt))
        return fired

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
        try:
            results = self.runtime.run_environment(p["env"], p.get("entities") or None, p.get("backfill") or None,
                                                   bool(p.get("cascade")), job=job)
        except Exception as exc:
            from .retrying import is_retryable
            error = f"{type(exc).__name__}: {exc}"
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

    def _alert(self, job: dict[str, Any], error: str) -> None:
        url = self.config.teams_webhook
        if not url:
            return
        env = str(job["payload"].get("env", "")).title()
        try:
            post_teams(url, f"Fabric Desktop by KratosBI agent {self.config.name}: a {env} job didn't finish",
                       [f"Job {job['id']} ({job['source'] or 'manual'})", error[:1500]])
        except Exception as exc:  # the log has it either way
            self.log.warning("couldn't post the alert to Teams", extra=fields(job_id=job["id"], error=str(exc)))

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

    def _guarded(self, job: dict[str, Any]) -> None:
        if self.mailbox:
            if self.mailbox.cancel_requested(job):
                self.queue.cancel(job["id"], "the pipeline cancelled it", owner=self.config.name)
                self.mailbox.job_changed(self.queue.get(job["id"]) or job)
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
