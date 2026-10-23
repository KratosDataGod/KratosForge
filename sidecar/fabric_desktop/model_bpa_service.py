"""Best practices check of a Data mesh semantic model, run in Fabric (``model_bpa``).

``model_bpa_status`` reads what the last check found (and picks up a finished one); ``model_bpa_risk``
is the risk check a check in Fabric shows first; ``model_bpa_check`` starts one, after the person has
confirmed and, when the risk check warns, acknowledged it. A check publishes ``nb_fabric_desktop_bpa``
to the environment's workspace when it changed, runs it there (capacity), and is logged with the other
Fabric runs, so Monitor shows it.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from . import model_bpa, risk
from .fabric_compute import RESULTS_DIR
from .fabric_run import TERMINAL
from .jsonfile import read_json, write_json
from .meta.store import META_LAKEHOUSE, OneLakeStore
from .onelake import FileStore

KIND = "Best practices check"
FABRIC_APP = "https://app.fabric.microsoft.com"
# One check starts at a time, so two quick clicks can't both pass the "already being checked" test.
_STARTING = threading.Lock()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class ModelBpaMixin:
    @property
    def _bpa_dir(self) -> Path:
        return self.root / "model_bpa"  # type: ignore[attr-defined]

    def _bpa_target(self, env: str, layer: str | None, name: str | None) -> dict[str, Any]:
        """The model a check is about: its workspace, id, and the environment's LH_Meta."""
        project = self._project()  # type: ignore[attr-defined]
        layer = self._model_layer(project, layer)  # type: ignore[attr-defined]
        cfg = self._env_cfg(project, env)  # type: ignore[attr-defined]
        store = self._store(project, env)  # type: ignore[attr-defined]
        if not isinstance(store, OneLakeStore):
            raise ValueError(f"{env.title()} keeps its tables on this PC, so it has no semantic model in Fabric to check. "
                             "Check an environment that runs on Fabric, with its model created there.")
        meta_id = store.lakehouse_ids.get(META_LAKEHOUSE)
        if not meta_id:
            raise LookupError(f"{META_LAKEHOUSE} doesn't exist in the {env} workspace yet; promote to {env} first")
        name = (name or f"SM_{layer.title()}").strip()
        ws = store.workspace_id
        model = next((m for m in self.fabric.list_semantic_models(ws)  # type: ignore[attr-defined]
                      if m["displayName"] == name), None)
        if model is None:
            raise LookupError(f"{name} isn't in {cfg.get('workspace_name') or 'the workspace'} yet. "
                              "Create the model first, then check it.")
        return {"env": env, "layer": layer, "name": name, "model_id": model["id"], "workspace_id": ws,
                "workspace_name": cfg.get("workspace_name", ""), "meta_lakehouse_id": meta_id}

    def _bpa_file(self, workspace_id: str, model_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9-]", "", f"{workspace_id}_{model_id}")
        return self._bpa_dir / f"{safe}.json"

    def _bpa_runs(self, model_id: str) -> list[dict[str, Any]]:
        """This model's checks, newest first, as last known."""
        return [r for r in self.fabric_runs(200, refresh=False)  # type: ignore[attr-defined]
                if r.get("kind") == KIND and r.get("model_id") == model_id]

    def _bpa_newest(self, model_id: str) -> dict[str, Any] | None:
        """This model's newest check, with its status asked of Fabric while it hasn't finished."""
        runs = self._bpa_runs(model_id)
        newest = runs[0] if runs else None
        if newest and newest.get("status") not in TERMINAL:
            fresh = self.fabric_runs(200, job_ids=[newest["job_id"]])  # type: ignore[attr-defined]
            newest = next((r for r in fresh if r.get("request_id") == newest["request_id"]), newest)
        return newest

    def _bpa_workload(self, t: dict[str, Any]) -> dict[str, Any]:
        """The check as the risk check sees a workload: one action, in Fabric, in the model's workspace."""
        runs = [{"where": "fabric", "env": t["env"], "job_id": r.get("job_id"), "status": r.get("status"),
                 "started_at": r.get("started_at"), "finished_at": r.get("finished_at")} for r in self._bpa_runs(t["model_id"])]
        return {"id": f"model_bpa:{t['workspace_id']}:{t['model_id']}", "name": f"Best practices check of {t['name']}",
                "kind": "model_bpa", "errors": [], "runs": runs,
                "deployments": [{"env": t["env"], "env_label": t["env"].title(), "actions": ["run_in_fabric"],
                                 "location": {"kind": "fabric", "workspace_id": t["workspace_id"],
                                              "workspace_name": t["workspace_name"]}}]}

    def _bpa_risk(self, t: dict[str, Any], refresh: bool = False) -> dict[str, Any]:
        w = self._bpa_workload(t)
        if refresh:
            return risk.analyze(self._risk_facts(w, "run_in_fabric", t["env"], True), self._risk_settings())  # type: ignore[attr-defined]
        return self._risk_or_unknown(lambda: w, "run_in_fabric", t["env"], None)  # type: ignore[attr-defined]

    # ---- what the app calls

    def model_bpa_status(self, env: str = "test", layer: str | None = None, name: str | None = None) -> dict[str, Any]:
        """The model's last check: its findings, and a check still running. Reads the result file
        of a check that finished since. Changes nothing in Fabric."""
        t = self._bpa_target(env, layer, name)
        path = self._bpa_file(t["workspace_id"], t["model_id"])
        last = read_json(path, None)
        newest = self._bpa_newest(t["model_id"])
        if newest and newest.get("status") in TERMINAL and (last or {}).get("request_id") != newest["request_id"]:
            data = FileStore(lambda: self.auth.storage_token()).read_bytes(  # type: ignore[attr-defined]
                t["workspace_id"], t["meta_lakehouse_id"], f"{RESULTS_DIR}/{newest['request_id']}.json")
            if data:
                last = model_bpa.findings(json.loads(data))
                write_json(path, last)
        running = newest if newest and newest.get("status") not in TERMINAL else None
        failed = newest if newest and newest.get("status") in TERMINAL and (last or {}).get("request_id") != newest["request_id"] else None
        return {"model": t["name"], "model_id": t["model_id"], "env": env, "workspace_id": t["workspace_id"],
                "workspace_name": t["workspace_name"], "last": last, "running": running,
                "failed": {"status": failed.get("status"), "message": failed.get("message", ""), "url": failed.get("url", "")} if failed else None}

    def model_bpa_risk(self, env: str = "test", layer: str | None = None, name: str | None = None,
                       refresh: bool = False) -> dict[str, Any]:
        """The risk check for checking this model in Fabric: capacity, policy and earlier checks. Read only."""
        return self._bpa_risk(self._bpa_target(env, layer, name), refresh)

    def model_bpa_check(self, env: str = "test", layer: str | None = None, name: str | None = None,
                        risk_id: str = "") -> dict[str, Any]:
        """Check the model in Fabric. The risk check runs again first: a blocked one stops here, and a
        warning goes ahead only with the ``risk_id`` of the assessment the person acknowledged."""
        t = self._bpa_target(env, layer, name)
        current = self._bpa_risk(t)
        if current["result"] == risk.BLOCKED:
            raise PermissionError("The risk check blocked this: " + current["summary"])
        if current["result"] == risk.WARNING:
            if current.get("id") != risk_id:
                raise ValueError("The risk check's findings changed since you looked. Check them again before you go ahead.")
            self._bpa_acknowledge(t, current)
        if not _STARTING.acquire(blocking=False):
            raise RuntimeError("A best practices check is already starting; wait for it, then try again.")
        try:
            return self._bpa_start(t, env)
        finally:
            _STARTING.release()

    def _bpa_start(self, t: dict[str, Any], env: str) -> dict[str, Any]:
        newest = self._bpa_newest(t["model_id"])
        if newest and newest.get("status") not in TERMINAL:
            raise RuntimeError(f"{t['name']} is already being checked; wait for that check to finish.")
        notebook_id = self._bpa_notebook(t)
        request_id = f"bpa-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        job_id = self.fabric_jobs.run_item_job(t["workspace_id"], notebook_id, "RunNotebook",  # type: ignore[attr-defined]
                                               model_bpa.run_parameters(t["model_id"], t["workspace_id"], request_id))
        run = self._log_fabric_run({  # type: ignore[attr-defined]
            "kind": KIND, "name": t["name"], "env": env, "workspace_id": t["workspace_id"],
            "workspace_name": t["workspace_name"], "item_id": notebook_id, "job_id": job_id,
            "request_id": request_id, "meta_lakehouse_id": t["meta_lakehouse_id"], "model_id": t["model_id"],
            "url": f"{FABRIC_APP}/groups/{t['workspace_id']}/synapsenotebooks/{notebook_id}?experience=fabric-developer"})
        return {"model": t["name"], "env": env, "request_id": request_id, "job_id": job_id, "status": run["status"],
                "message": f"Checking {t['name']} in Fabric on the {env.title()} workspace's capacity"}

    def _bpa_acknowledge(self, t: dict[str, Any], current: dict[str, Any]) -> None:
        """Kept with the other risk acknowledgements (risk_acknowledgements)."""
        record = {"at": _now(), "risk_id": current["id"], "workload_id": f"model_bpa:{t['workspace_id']}:{t['model_id']}",
                  "workload": current.get("workload") or t["name"], "action": "run_in_fabric", "env": t["env"],
                  "result": current["result"], "user": self._wl_user(),  # type: ignore[attr-defined]
                  "warnings": [f["title"] for f in current["findings"] if f["outcome"] == risk.WARNING], "note": ""}
        self._risk_ack_write(record)  # type: ignore[attr-defined]

    def _bpa_notebook(self, t: dict[str, Any]) -> str:
        """Publish the check notebook to the workspace unless the same one is already there."""
        ipynb = model_bpa.bpa_ipynb(t["workspace_id"], t["meta_lakehouse_id"])
        digest = model_bpa.notebook_hash(ipynb)
        state_path = self._bpa_dir / "notebooks.json"
        state = read_json(state_path, {})
        known = state.get(t["workspace_id"]) or {}
        if known.get("hash") == digest and known.get("id"):
            return known["id"]
        fabric = self.fabric  # type: ignore[attr-defined]
        item = fabric.save_notebook(t["workspace_id"], model_bpa.NOTEBOOK, json.dumps(ipynb).encode())
        notebook_id = item.get("id") or (self._notebook_item(model_bpa.NOTEBOOK, t["workspace_id"]) or {}).get("id")  # type: ignore[attr-defined]
        if not notebook_id:
            raise LookupError(f"{model_bpa.NOTEBOOK} was published but Fabric didn't return its id; try again")
        state[t["workspace_id"]] = {"hash": digest, "id": notebook_id, "at": _now()}
        write_json(state_path, state)
        return notebook_id
