"""Incidents in the app: read, evaluate and handle the incidents of an environment.

The same evaluation as the agent's (workloads/evaluate.py), over the same run log, written to the
same document in the environment's control area (workloads/incidents.py), so the app and the
agent never open two incidents for one problem. The app never evaluates on its own schedule:
it does when asked (``incidents_evaluate``), so nothing polls a tenant in the background.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .auth import STORAGE_SCOPES
from .dataops_service import post_teams
from .meta.project import ENVIRONMENTS
from .meta import mesh as mesh_mod
from .workloads import evaluate, model
from .workloads.incidents import KIND_LABELS, IncidentLog
from .workloads.agents import read_status
from .workloads.notify import Outbox, send, single


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
        return {"env": env, "incidents": items, "observation": model.observation("Incidents", "live", model.now())}

    def incidents_evaluate(self, env: str, notify: bool = False) -> dict[str, Any]:
        """Evaluate the environment now and update its incidents; with ``notify``, tell Teams."""
        project = self._project()  # type: ignore[attr-defined]
        log = self._incident_log(env)
        crit = {a_id.split(":", 2)[2]: a["criticality"]["value"]
                for a_id, a in self.workload_store.assignments().items()  # type: ignore[attr-defined]
                if a_id.startswith(f"meta:{mesh_mod.project_name(project)}:") and a.get("criticality")}
        evaluated: set[str] = set()
        try:
            runs = self.meta_runs(env, 2000)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - can't read it: that is the finding
            state, message = model.classify_error(exc)
            signals = [evaluate.cant_evaluate(env, state, message)]
        else:
            specs = {n: e.spec for n, e in project.entities.items()}
            signals = evaluate.signals(mesh_mod.project_name(project), specs, runs, env, criticality=crit,
                                       team=mesh_mod.identity(project)["team"])
            evaluated |= evaluate.WORKLOAD_KINDS
        try:
            signals += evaluate.agent_signals(read_status(log.backend), env)
            evaluated.add("agent_offline")
        except Exception:  # noqa: BLE001 - agent records unreadable: their incidents stay as they are
            pass
        changes = log.sync(signals, evaluated=evaluated, by=f"Fabric Desktop by KratosBI ({self._incident_user()})")
        out: dict[str, Any] = {"env": env, "evaluated": bool(evaluated & evaluate.WORKLOAD_KINDS), "signals": len(signals),
                               "changes": [{"change": c["change"], "id": c["incident"]["id"], "title": c["incident"]["title"]}
                                           for c in changes]}
        if notify:
            url = self._settings().get("teams_webhook", "")  # type: ignore[attr-defined]
            outbox = Outbox(self.root / "alerts_outbox.sqlite")  # type: ignore[attr-defined]
            out["notified"] = send(changes, single(url), outbox, "Fabric Desktop by KratosBI", post_teams)
        out["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        return out

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
