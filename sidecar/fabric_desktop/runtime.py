"""What the headless agent runs: the metadata engine and its OneLake stores, with no desktop UI.

``Runtime`` is built from the same mixins as the desktop ``Service`` (projects, environments,
stores, Fabric maintenance after a run, alerts), so a table runs exactly as it does from the
app. It leaves out the notebook kernel, the UI's settings file as the source of truth and the
interactive sign-in: the project and the identity come from ``agent.toml``.
"""

from __future__ import annotations

import os
import socket
from typing import Any, Callable

from .agent_config import AgentConfig
from .auth import EXECUTE_SCOPES, ONELAKE_SCOPES
from .cloud_service import CloudMixin
from .credentials import Credential, from_config
from .dataops_service import DataOpsMixin
from .fabric_api import FabricClient
from .jsonfile import read_json, write_json
from .meta import Engine, OneLakeStore, Project
from .meta.store import META_LAKEHOUSE
from .meta_service import MetaMixin
from .central.service import PlatformMixin

# Settings that come from agent.toml and are never written to the agent's settings file.
_FROM_CONFIG = ("meta_project", "teams_webhook")


class Runtime(PlatformMixin, MetaMixin, CloudMixin, DataOpsMixin):
    def __init__(self, config: AgentConfig, credential: Credential | None = None):
        self.config = config
        self.root = config.home / "data"
        self._credential = credential

    # ---- identity and clients (the desktop Service has the same names)

    @property
    def auth(self) -> Credential:
        if self._credential is None:
            self._credential = from_config(self.config.auth)
        return self._credential

    @property
    def fabric(self) -> FabricClient:
        return FabricClient(self.auth.fabric_token)

    def onelake_token(self) -> str:
        """A token for OneLake's Fabric APIs (listing shortcuts and data access roles)."""
        return self.auth._token(ONELAKE_SCOPES)

    def environment_config(self, env: str) -> dict[str, Any]:
        """An environment's settings in the project (workspace ids and the rest); {} when it has none."""
        return dict(self._project().environments.get(env, {}))

    @property
    def fabric_jobs(self) -> FabricClient:
        return FabricClient(lambda: self.auth._token(EXECUTE_SCOPES))

    # ---- settings: the project and alert address come from agent.toml

    def _settings(self) -> dict[str, Any]:
        return super()._settings() | {"meta_project": str(self.config.project),
                                      "teams_webhook": self.config.teams_webhook}

    def _save_settings(self, **changes: Any) -> None:
        # read_json sets a torn file aside instead of letting this save drop everything in it (QA-011)
        kept = {k: v for k, v in (read_json(self.settings_file, {}) | changes).items() if k not in _FROM_CONFIG}
        write_json(self.settings_file, kept, indent=2)

    # ---- guard rails

    def check_allowed(self, project: Project, env: str) -> None:
        if env not in self.config.environments:
            raise PermissionError(f"This agent may run {', '.join(self.config.environments)}, not {env} "
                                  f"(agent.environments in {self.config.path})")
        if not self.config.workspaces:
            return
        cfg = project.environments.get(env, {})
        targets = [(key, cfg.get(key)) for key in ("workspace_id", "keys_workspace_id")]
        targets += [(f"{d} workspace", w.get("id")) for d, w in (cfg.get("workspaces") or {}).items()]
        for key, ws in targets:
            if ws and ws not in self.config.workspaces:
                raise PermissionError(f"{env.title()}'s {key} {ws} isn't in agent.workspaces, so this agent won't write there")

    def control_backend(self, env: str) -> Any:
        """Where pipelines leave run requests for ``env``: its LH_Meta, or a local folder (mailbox.py)."""
        from .mailbox import control_backend_for
        project = self._project()
        self.check_allowed(project, env)
        store = self._store(project, env)
        meta_id = store.lakehouse_ids.get(META_LAKEHOUSE) if isinstance(store, OneLakeStore) else None
        return control_backend_for(store, meta_id, self.auth.storage_token)

    def verify_pipeline_run(self, env: str, item_id: str, run_id: str) -> dict[str, Any]:
        """Fabric's record of a pipeline run in ``env``'s workspace, for a request that names it (mailbox.py)."""
        project = self._project()
        self.check_allowed(project, env)
        store = self._store(project, env)
        if not isinstance(store, OneLakeStore):
            raise LookupError(f"{env} runs on this machine, so there is no Fabric workspace to look the run up in")
        return self.fabric.job_instance(store.workspace_id, item_id, run_id)

    def entity_names(self) -> set[str]:
        return set(self._project().entities)

    # ---- running

    def run_environment(self, env: str, entities: list[str] | None = None, backfill: str | None = None,
                        cascade: bool = False, job: dict[str, Any] | None = None,
                        guard: Callable[[], None] | None = None) -> list[dict[str, Any]]:
        """Run an environment's entities on this machine, each table under a write lease. Returns
        one result per entity, as the desktop's Run does. ``guard`` is called once each table's
        lease is held, before anything is written; it raises ``Abandoned`` to stop the run."""
        project = self._project()
        self.check_allowed(project, env)
        # The agent never creates lakehouses: an environment is set up by promoting to it.
        store = self._store(project, env)
        project = self._released(project, env, store)  # the files of the release env is on, not this checkout's
        uses_keys = self._uses_keys(project, entities)
        keys_store = self._keys_store(project, env) if uses_keys else None
        table_lock = self._table_lock(project, env, store, owner=self.config.name,
                                      seconds=self.config.table_lease_seconds, guard=guard)
        job = job or {}

        engine = Engine(project, store, keys_store=keys_store, source_options=self._source_options,
                        link_table=self._link_table(project, env),
                        table_lock=table_lock,
                        run_context={"worker": self.config.name, "host": socket.gethostname(),
                                     "attempt": int(job.get("attempts") or 1), "job_id": job.get("id", "")})
        engine.batch_prefix = "agent-"
        if cascade and entities and len(entities) == 1:
            results = engine.backfill(entities[0])
        else:
            results = engine.run_many(entities or None, backfill=backfill or None)
        out = [r.to_dict() for r in results]
        try:
            self._after_run(project, env, store, out)
        except Exception as exc:  # maintenance is a follow-up; the tables are written
            for r in out:
                r.setdefault("maintenance_message", f"{type(exc).__name__}: {exc}")
        return out

    def check(self) -> dict[str, Any]:
        """What ``fabric-desktop agent check`` reports: the project, and a token for each audience."""
        project = self._project()
        out: dict[str, Any] = {"worker": self.config.name, "home": str(self.config.home), "pid": os.getpid(),
                               "project": str(project.root), "metadata_version": project.version,
                               "entities": len(project.entities), "environments": {}}
        for env in self.config.environments:
            cfg = project.environments.get(env, {})
            out["environments"][env] = {"target": cfg.get("target", "onelake" if env != "dev" else "local"),
                                        "workspace_id": cfg.get("workspace_id", "")}
        needs_cloud = any(v["target"] != "local" for v in out["environments"].values())
        if needs_cloud:
            out["identity"] = self.auth.signed_in_user()
            out["fabric_token"] = bool(self.auth.fabric_token())
            out["storage_token"] = bool(self.auth.storage_token())
        return out
