"""Incidents in the app: read, evaluate and handle the incidents of an environment.

The same evaluation as the agent's (workloads/evaluate.py), over the same run log, written to the
same document in the environment's control area (workloads/incidents.py), so the app and the
agent never open two incidents for one problem. The app never evaluates on its own schedule:
it does when asked (``incidents_evaluate``), so nothing polls a tenant in the background.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from . import __version__, app_settings, sealed_settings
from .auth import STORAGE_SCOPES
from .dataops_service import post_teams
from .meta.project import ENVIRONMENTS
from .meta import mesh as mesh_mod
from .redact import describe
from .workloads import evaluate, model
from .workloads.incidents import KIND_LABELS, IncidentLog
from .workloads.agents import agent_state, fleet, read_status
from .workloads.overlap import overlaps
from .workloads import queue, run_as
from .workloads.notify import Outbox, send, single

_log = logging.getLogger(__name__)


class IncidentMixin:
    def _incident_log(self, env: str) -> IncidentLog:
        from .mailbox import control_backend_for
        from .meta import OneLakeStore
        from .meta.store import META_LAKEHOUSE
        if env not in ENVIRONMENTS:
            raise ValueError(f"env is one of {', '.join(ENVIRONMENTS)}")
        project = self._project()  # type: ignore[attr-defined]
        store = self._store(project, env)  # type: ignore[attr-defined]
        meta_id = store.lakehouse_ids.get(META_LAKEHOUSE) if isinstance(store, OneLakeStore) else None
        token = lambda: self._wl_token(STORAGE_SCOPES) or ""  # type: ignore[attr-defined]  # noqa: E731 - never prompts
        return IncidentLog(control_backend_for(store, meta_id, token), env)

    def _incident_user(self) -> str:
        return self._wl_user() or "someone on this PC"  # type: ignore[attr-defined]

    def incidents_list(self, env: str, include_resolved: bool = True) -> dict[str, Any]:
        """The environment's incidents, active first. A read problem is reported, not raised."""
        if env not in ENVIRONMENTS:
            raise ValueError(f"env is one of {', '.join(ENVIRONMENTS)}")
        try:
            items = self._incident_log(env).list(None if include_resolved else ("open", "acknowledged"))
        except Exception as exc:  # noqa: BLE001
            state, message = model.classify_error(exc)
            return {"env": env, "incidents": [], "observation": model.observation("Incidents", state, detail=message)}
        for i in items:
            i["kind_label"] = KIND_LABELS.get(i["kind"], i["kind"])
        return {"env": env, "incidents": items, "observation": model.observation("Incidents", "live", model.now()),
                "user": self._wl_user(), "agents": self._incident_agents(env)}  # type: ignore[attr-defined]

    def _incident_agents(self, env: str) -> dict[str, Any]:
        """Which agents serve ``env`` and whether they check its incidents on a timer, from their
        status records. ``readable`` is false when the records couldn't be listed."""
        try:
            records = read_status(self._incident_log(env).backend)
        except Exception as exc:  # noqa: BLE001
            return {"readable": False, "detail": model.classify_error(exc)[1], "agents": []}
        now = dt.datetime.now(dt.timezone.utc)
        out = []
        for r in records:
            state = agent_state(r, now)
            inc = r.get("incidents") if isinstance(r.get("incidents"), dict) else None  # None: an agent too old to say
            out.append({"agent": r.get("agent"), "host": r.get("host"), "version": r.get("version"), "state": state,
                        "heartbeat_at": r.get("heartbeat_at"), "incidents": inc})
        return {"readable": True, "agents": sorted(out, key=lambda a: str(a["agent"]))}

    def _agent_records(self) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """Each environment's agent status records, and whether each environment could be read."""
        by_env: dict[str, list[dict[str, Any]]] = {}
        envs = []
        for env in ENVIRONMENTS:
            try:
                by_env[env] = read_status(self._incident_log(env).backend)
            except Exception as exc:  # noqa: BLE001
                state, message = model.classify_error(exc)
                envs.append({"env": env, "count": None, "observation": model.observation("Agents", state, detail=message)})
                continue
            envs.append({"env": env, "count": len(by_env[env]), "observation": model.observation("Agents", "live", model.now())})
        return envs, by_env

    def agents_list(self) -> dict[str, Any]:
        """Every agent serving this project's environments, from the status records they write
        (read only; nothing here reaches an agent). Each environment says whether it could be read."""
        now = dt.datetime.now(dt.timezone.utc)
        envs, by_env = self._agent_records()
        try:
            pc, pc_readable = self.pc_schedules.listing(), True  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001  # compare agents with each other, and say this PC wasn't read
            pc, pc_readable = [], False
        return {"envs": envs, "agents": fleet(by_env, now, __version__), "app_version": __version__,
                **overlaps(by_env, pc, now), "pc_schedules_read": pc_readable}

    def queue_list(self, hours: float = 24) -> dict[str, Any]:
        """Running, queued and next work across this PC, each agent and Fabric (workloads/queue.py),
        from what the app already holds: agent status records, this PC's schedules, the Fabric jobs
        this app started as last checked, and the Fabric schedules Monitor > Fabric > Schedules last read. It
        makes no Fabric call of its own, so it never polls a tenant. Each source says how it stands."""
        now = dt.datetime.now(dt.timezone.utc)
        envs, by_env = self._agent_records()
        sources: dict[str, Any] = {"agents": envs}
        try:
            pc = self.pc_schedules.listing()  # type: ignore[attr-defined]
            sources["pc"] = model.observation("This PC", "live", model.now())
        except Exception as exc:  # noqa: BLE001
            pc = []
            state, message = model.classify_error(exc)
            sources["pc"] = model.observation("This PC", state, detail=message)
        try:
            runs = self.fabric_runs(limit=100, refresh=False)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001  # an unreadable local log of this app's Fabric jobs: none to show
            runs = []
        cached = dict(getattr(self, "_fabric_schedules", None) or {})
        sources["fabric_schedules"] = self._schedules_source(cached)
        rows = queue.build(by_env, pc, runs, cached.get("items") or [], self._env_of(), now, hours)
        return {"rows": rows, "sources": sources, "hours": hours}

    def run_as_list(self) -> dict[str, Any]:
        """Who runs each workload, with what credential, and where data goes (workloads/run_as.py):
        your sign-in, agent status records and the Fabric schedules Schedules last read. Read only,
        no Fabric call, never a sign-in window."""
        now = dt.datetime.now(dt.timezone.utc)
        envs, by_env = self._agent_records()
        cached = dict(getattr(self, "_fabric_schedules", None) or {})
        policy = {k: app_settings.value(k) for k in ("allow_fabric_runs", "allowed_tenants", "local_retention_days")}
        out = run_as.build(self._wl_user(), by_env, cached.get("items") or [], self._env_of(), now, policy)  # type: ignore[attr-defined]
        return out | {"sources": {"agents": envs, "fabric_schedules": self._schedules_source(cached)}}

    def _env_of(self) -> dict[str, str]:
        env_of: dict[str, str] = {}
        try:
            for env, cfg in self._project().environments.items():  # type: ignore[attr-defined]
                if cfg.get("workspace_id"):
                    env_of[str(cfg["workspace_id"])] = env
        except Exception as exc:  # noqa: BLE001  # no project open: Fabric rows show no environment
            _log.info("No metadata project; Fabric rows show no environment: %s", describe(exc))
        return env_of

    @staticmethod
    def _schedules_source(cached: dict[str, Any]) -> dict[str, Any]:
        return ({"state": "not_read"} if not cached else {"state": "reading"} if cached.get("pending")
                else {"state": "error", "detail": cached["error"]} if cached.get("error")
                else {"state": "read", "at": cached.get("scanned")})

    def incidents_summary(self, workload: str = "") -> dict[str, Any]:
        """Active incidents per environment, for the environment chips and a workload's page. With
        ``workload``, only that workload's. An environment that can't be read says so."""
        envs = []
        for env in ENVIRONMENTS:
            try:
                items = self._incident_log(env).active()
            except Exception as exc:  # noqa: BLE001
                state, message = model.classify_error(exc)
                envs.append({"env": env, "count": None, "incidents": [],
                             "observation": model.observation("Incidents", state, detail=message)})
                continue
            if workload:
                items = [i for i in items if i.get("workload") == workload]
            for i in items:
                i["kind_label"] = KIND_LABELS.get(i["kind"], i["kind"])
            envs.append({"env": env, "count": len(items), "incidents": items,
                         "observation": model.observation("Incidents", "live", model.now())})
        return {"envs": envs, "user": self._wl_user()}  # type: ignore[attr-defined]

    def incidents_evaluate(self, env: str, notify: bool = False) -> dict[str, Any]:
        """Evaluate the environment now and update its incidents; with ``notify``, tell Teams."""
        project = self._project()  # type: ignore[attr-defined]
        log = self._incident_log(env)
        crit = {a_id.split(":", 2)[2]: a["criticality"]["value"]
                for a_id, a in self.workload_store.assignments().items()  # type: ignore[attr-defined]
                if a_id.startswith(f"meta:{mesh_mod.project_name(project)}:") and a.get("criticality")}
        evaluated: set[str] = set()
        not_set_up = ""
        try:
            runs = self.meta_runs(env, 2000)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - can't read it: that is the finding, unless it isn't set up
            signals, not_set_up = evaluate.unreadable(env, exc)
        else:
            specs = {n: e.spec for n, e in project.entities.items()}
            signals = evaluate.signals(mesh_mod.project_name(project), specs, runs, env, criticality=crit,
                                       team=mesh_mod.identity(project)["team"])
            evaluated |= evaluate.WORKLOAD_KINDS
        try:
            signals += evaluate.agent_signals(read_status(log.backend), env)
            evaluated.add("agent_offline")
        except Exception as exc:  # noqa: BLE001 - agent records unreadable: their incidents stay as they are
            _log.warning("Couldn't read agent records; their incidents stay as they are: %s", describe(exc))
        changes = log.sync(signals, evaluated=evaluated, by=f"KratosForge ({self._incident_user()})")
        out: dict[str, Any] = {"env": env, "evaluated": bool(evaluated & evaluate.WORKLOAD_KINDS), "signals": len(signals),
                               "changes": [{"change": c["change"], "id": c["incident"]["id"], "title": c["incident"]["title"]}
                                           for c in changes]}
        if not_set_up:
            out["not_set_up"] = not_set_up
        if notify:
            url = self._settings().get("teams_webhook", "")  # type: ignore[attr-defined]
            outbox = Outbox(self.root / "alerts_outbox.sqlite")  # type: ignore[attr-defined]
            out["notified"] = send(self._not_told_before(env, changes), single(url), outbox, "KratosForge", post_teams)
        out["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        return out

    def _not_told_before(self, env: str, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Leave out an incident opening that an older app already posted as a late or failed
        table (freshness_alerts.json, no longer written), so updating doesn't post it twice. Its
        recovery is still posted."""
        try:
            told = json.loads((self.root / "freshness_alerts.json").read_text(encoding="utf-8"))  # type: ignore[attr-defined]
        except FileNotFoundError:
            return changes
        except (OSError, ValueError) as exc:  # unreadable: post, a second post beats none
            _log.warning("Couldn't read the older Teams alert record: %s", describe(exc))
            return changes
        if not isinstance(told, dict):
            return changes

        def legacy_key(inc: dict[str, Any]) -> str:
            name = str(inc.get("name") or "")
            prefix = {"late": "late", "run_failed": "failed"}.get(inc.get("kind", ""))
            evidence = str(inc.get("evidence_id") or "").removeprefix("late:")
            return f"{prefix}:{env}:{name}:{evidence}" if prefix and name and evidence else ""

        return [c for c in changes if c["change"] != "opened" or legacy_key(c["incident"]) not in told]

    def incidents_watch(self, notify: bool = False) -> dict[str, Any]:
        """The app's own check while it is open (every few minutes, from the UI): each environment
        that a running agent already checks is only read; any other is evaluated here, and with
        ``notify`` its changes go to this PC's Teams webhook. So a team with an agent hears about a
        problem once, from the agent, and a team without one still hears about it from the app.
        An environment that isn't set up or can't be read is reported, never evaluated."""
        envs = []
        for env in ENVIRONMENTS:
            try:
                log = self._incident_log(env)
            except Exception as exc:  # noqa: BLE001
                state, message = model.classify_error(exc)
                envs.append({"env": env, "mode": "unreadable", "state": state, "detail": message, "incidents": []})
                continue
            agents = self._incident_agents(env)
            checking = [a["agent"] for a in agents["agents"] if a["state"] == "live" and (a["incidents"] or {}).get("enabled")]
            row: dict[str, Any] = {"env": env, "mode": "agent" if checking else "app", "agents": checking}
            try:
                if not checking:
                    res = self.incidents_evaluate(env, notify=notify)
                    row["changes"] = res["changes"]
                    if "notified" in res:
                        row["notified"] = res["notified"]
                for i in (items := log.active()):
                    i["kind_label"] = KIND_LABELS.get(i["kind"], i["kind"])
                row["incidents"] = items
            except Exception as exc:  # noqa: BLE001
                state, message = model.classify_error(exc)
                row.update(mode="unreadable", state=state, detail=message, incidents=[])
            envs.append(row)
        settings = self._settings()  # type: ignore[attr-defined]
        return {"envs": envs, "alerts": bool(settings.get("teams_webhook")),
                "alerts_unreadable": sealed_settings.unreadable(settings, "teams_webhook")}

    def incident_acknowledge(self, env: str, id: str, note: str = "") -> dict[str, Any]:
        return self._incident_log(env).acknowledge(id, self._incident_user(), note)

    def incident_assign(self, env: str, id: str, owner: str) -> dict[str, Any]:
        return self._incident_log(env).assign(id, owner, self._incident_user())

    def incident_note(self, env: str, id: str, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("Write a note first.")
        return self._incident_log(env).note(id, text.strip(), self._incident_user())

    def incident_resolve(self, env: str, id: str, note: str = "") -> dict[str, Any]:
        return self._incident_log(env).resolve(id, self._incident_user(), note)
