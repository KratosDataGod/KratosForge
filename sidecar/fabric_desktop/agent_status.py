"""What each agent publishes about itself: ``agents/<name>.json`` in each environment's control area.

Every ``agent.status_seconds`` the agent writes one file per environment it runs, next to the
pipeline mailbox (``LH_Meta/Files/_fabric_desktop/agents/<name>.json``; a local environment
keeps it in its ``.local`` folder). Anything that can read ``LH_Meta`` can tell which agents
serve the environment, whether they are alive, and what they are doing, without reaching the
agent's machine. A file only describes jobs for its own environment, so one workspace's
readers never see another environment's work.

Record, contract 1::

    {"contract": 1, "kind": "agent_status", "agent": "etl-01", "host": "srv-etl-01", "version": "0.21.0",
     "state": "running" | "stopping" | "stopped", "env": "prod", "environments": ["test", "prod"],
     "started_at": "...Z", "heartbeat_at": "...Z", "stale_after": "...Z", "status_seconds": 60,
     "concurrency": 1, "pipeline": {"enabled": true, "entities": [...], "allow_backfill": false},
     "incidents": {"enabled": true, "seconds": 300, "quiet_hours": "22:00-06:00", "quiet_except": ["critical"],
                   "escalate_after_minutes": 60, "escalate_to": "on-call",
                   "routes": [{"name", "teams", "owners", "criticality", "kinds", "envs"}]},  # no webhooks
     "counts": {"queued": 0, "running": 1, ...},           # this environment's jobs
     "running": [{"job_id", "source", "attempts", "entities", "started_at"}],
     "schedules": [{"id", "next"}],                         # this environment's schedules
     "recent": [{"job_id", "state", "source", "finished_at", "error"}]}  # the last 10 finished

A reader treats an agent whose ``stale_after`` has passed as offline: it stopped without saying
so (a crash, a power cut, a lost network). ``stopped`` means it shut down cleanly. Fields may be
added within contract 1; readers should ignore fields they don't know.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
from typing import Any

from . import __version__

CONTRACT = 1
RECENT = 10


def _iso(t: float | None) -> str | None:
    if t is None:
        return None
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def record(agent: Any, env: str, state: str = "running") -> dict[str, Any]:
    """The status record ``agent`` publishes for ``env``."""
    cfg, queue, now = agent.config, agent.queue, agent.clock()
    jobs = queue.for_env(env, limit=200)
    running = [j for j in jobs if j["state"] == "running"]
    finished = [j for j in jobs if j["state"] not in ("queued", "running")]
    finished.sort(key=lambda j: j.get("finished_at") or 0, reverse=True)
    pipeline = cfg.pipeline
    return {
        "contract": CONTRACT, "kind": "agent_status", "agent": cfg.name, "host": socket.gethostname(),
        "pid": os.getpid(), "version": __version__, "state": state, "env": env,
        "environments": list(cfg.environments), "started_at": _iso(agent.started_at),
        "heartbeat_at": _iso(now), "stale_after": _iso(now + 3 * cfg.status_seconds),
        "status_seconds": cfg.status_seconds, "concurrency": cfg.concurrency,
        "pipeline": {"enabled": pipeline.enabled and env in pipeline.environments,
                     "entities": list(pipeline.entities), "allow_backfill": pipeline.allow_backfill},
        "incidents": incidents_summary(cfg.incidents),
        "counts": queue.counts(env),
        "running": [{"job_id": j["id"], "source": j["source"], "attempts": j["attempts"],
                     "entities": j["payload"].get("entities") or [], "started_at": _iso(j["started_at"])}
                    for j in running],
        "schedules": [{"id": s.id, "next": _iso(queue.schedule_next(s.id))} for s in cfg.schedules if s.env == env],
        "recent": [{"job_id": j["id"], "state": j["state"], "source": j["source"],
                    "finished_at": _iso(j["finished_at"]), "error": (j["error"] or "")[:300] or None}
                   for j in finished[:RECENT]],
    }


def incidents_summary(inc: Any) -> dict[str, Any]:
    """What the app shows about this agent's incident checks: whether it checks, how often, and
    where messages go by route name. Webhook addresses and the variables holding them are left out."""
    return {"enabled": bool(inc.enabled), "seconds": inc.seconds, "quiet_hours": inc.quiet_hours,
            "quiet_except": list(inc.quiet_except), "escalate_after_minutes": inc.escalate_after_minutes,
            "escalate_to": inc.escalate_to,
            "routes": [{k: (list(v) if isinstance(v, tuple) else v) for k, v in r.items() if k != "webhook_env"}
                       for r in inc.routes]}


def publish(backend: Any, name: str, body: dict[str, Any]) -> None:
    """Write ``agents/<name>.json``, replacing what is there. Only this agent writes its own file."""
    key = f"agents/{name}.json"
    data = json.dumps(body, indent=2).encode()
    for _ in range(3):
        if backend.create(key, data):
            return
        current = backend.read(key)
        if current is not None and backend.replace(key, data, current.etag):
            return
    raise RuntimeError(f"couldn't write {key}: it kept changing (is another agent called {name}?)")
