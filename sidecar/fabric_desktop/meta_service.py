"""UI actions for the metadata framework, mixed into ``Service``."""

from __future__ import annotations

import datetime as dt
import json
import logging
import tomllib
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import polars as pl

from . import duties, lakehouse, sealed_settings
from .meta import keys as keys_mod
from .meta import lineage, release
from .meta import mesh
from .meta import Engine, GateError, Layer, LocalStore, OneLakeStore, Project, impact, promote
from .meta.engine import RunResult
from .errors import NotSetUp
from .leases import Backend, Leases, LocalBackend, OneLakeBackend, lease_timeout, onelake_table_lease, table_key
from .onelake import storage_options
from .publish import WRITER
from .meta.project import DEFAULT_RETENTION_DAYS, ENVIRONMENTS, entity_name_problem, validate
from .meta import diff as diff_mod
from .meta import snapshot
from .meta.promote import promote_checks, releases, rollback
from .meta.store import LANDING_LAKEHOUSE, META_LAKEHOUSE, Store, ran_on
from .meta.templates import TEMPLATES, describe, entity_text, init_project
from .redact import describe as describe_error
from .jsonfile import read_json, write_json, write_text

_log = logging.getLogger(__name__)
_seal_failed: list[bool] = []  # the warning that settings.json couldn't be sealed was logged


def _json_rows(df: pl.DataFrame, limit: int) -> list[dict[str, Any]]:
    return json.loads(df.head(limit).write_json())


def _preview(store: Any, lakehouse: str, schema: str, table: str, limit: int) -> dict[str, Any] | None:
    """The first ``limit`` rows and the row count from the Delta log: only those rows are read,
    however large the table (HD-033). None when there's no table."""
    lf = store.scan(lakehouse, schema, table)
    if lf is None:
        return None
    df = lf.head(limit).collect()
    return {"columns": df.columns, "rows": json.loads(df.write_json()), "total": store.count(lakehouse, schema, table)}


def _env_workspaces(cfg: dict[str, Any]) -> set[str]:
    """The workspaces an environment's lakehouses live in: its own and each designation's."""
    ids = {cfg.get("workspace_id") or ""} | {(w or {}).get("id") or "" for w in (cfg.get("workspaces") or {}).values()}
    return ids - {""}


def _clashes(envs: dict[str, dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Every pair of environments that name the same workspace, as (first, second, workspace)."""
    spaces = {env: _env_workspaces(envs.get(env, {})) for env in ENVIRONMENTS}
    return [(a, b, ws) for i, a in enumerate(ENVIRONMENTS) for b in ENVIRONMENTS[i + 1:]
            for ws in sorted(spaces[a] & spaces[b])]


def shared_workspaces(envs: dict[str, dict[str, Any]], before: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Why these environments can't be saved or used: two of them name the same workspace, so a run
    in one would write the other's tables (LH_Meta, LH_Bronze, ... have the same names in each).
    With ``before``, only clashes it didn't already have, so a file that shares a workspace can be
    fixed one environment at a time."""
    old = set(_clashes(before)) if before is not None else set()
    return [f"{a.title()} and {b.title()} both use workspace {ws}. Each environment needs its own "
            "workspace, or a run in one writes the other's tables." for a, b, ws in _clashes(envs) if (a, b, ws) not in old]


def _toml(v: Any) -> str:
    """A TOML value; tables are written inline: { ingest = { id = "...", name = "..." } }."""
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml(x)}" for k, x in v.items()) + " }" if v else "{}"
    if isinstance(v, list):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    return json.dumps(v)


def _days(value: Any) -> Any:
    """A whole number of days from the UI; anything else is left for the save gate to refuse."""
    if value in (None, ""):
        return DEFAULT_RETENTION_DAYS
    try:
        return int(value) if float(value) == int(float(value)) else value
    except (TypeError, ValueError):
        return value


def _project_folder(path: str) -> Path:
    """A project's folder as the app's folder picker gives it: a full path. A relative one would be
    taken from wherever the engine happened to start (#1173)."""
    folder = Path(path)
    if not folder.is_absolute():
        raise ValueError(f"Choose the project's folder by its full path, such as C:\\Users\\you\\Documents\\metadata, not {path!r}")
    return folder


class NoMetadataProject(NotSetUp):
    """No metadata project is open: not set up, as opposed to a project that couldn't be read."""


class MetaMixin:
    root: Path

    # ---- which project is open

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    def _settings(self) -> dict[str, Any]:
        settings = read_json(self.settings_file, {})
        settings = settings if isinstance(settings, dict) else {}
        if sealed_settings.needs_sealing(settings):  # saved in the clear by an earlier version
            try:
                write_json(self.settings_file, sealed_settings.seal(settings))
            except OSError as exc:  # tried again on each read, but said once per process
                if not _seal_failed:
                    _seal_failed.append(True)
                    _log.warning("Couldn't encrypt the saved Teams addresses: %s", describe_error(exc))
        return sealed_settings.unseal(settings)

    def _save_settings(self, **changes: Any) -> None:
        settings = self._settings()
        kept = {k: v for k, v in (settings.get(sealed_settings.UNREADABLE) or {}).items() if k not in changes}
        write_json(self.settings_file, sealed_settings.seal(settings | changes | {sealed_settings.UNREADABLE: kept}))

    def _source_options(self) -> dict[str, str]:
        """OneLake access for source tables outside the environment (adopted lakehouse tables)."""
        return storage_options(self.auth.storage_token())  # type: ignore[attr-defined]

    def _project(self) -> Project:
        path = self._settings().get("meta_project")
        if path and not Path(path).is_absolute():  # saved before full paths were required: where it points depends on the start folder
            raise NoMetadataProject(f"The metadata project was saved as {path!r}, not a full path. Open its folder again.")
        if not path or not (Path(path) / "layers.toml").exists():
            raise NoMetadataProject("No metadata project is open. Create one or open its folder.")
        # A new Project each call, never a reload of the last one: a job thread may still be reading
        # that one (the agent validates and runs it while its loop asks for the project again), and a
        # reload empties its entities before refilling them.
        project = Project(path)
        self._install_guard(project)
        return project

    def _install_guard(self, project: Project) -> None:
        def guard(lakehouse_name: str, schema: str, table: str) -> str | None:
            for e in project.entities.values():
                if e.layer in project.layer_names() and project.layer(e.layer).lakehouse == lakehouse_name \
                        and e.schema == schema and e.table == table:
                    return (f"{e.name} is managed by the metadata project, so only a framework run can write it. "
                            f"Write to a scratch_ table to explore, or change {e.path.name}.")
            return None
        lakehouse.write_guard = guard

    def _job_guard(self, workspace_id: str, lakehouse_name: str, schema: str, table: str) -> str | None:
        """Why a job may not write this table: the metadata project manages it, or the workspace is
        Test's or Prod's, which change only by promote. None when no project is open or it may."""
        try:
            project = self._project()
        except LookupError:
            return None
        for env in ("test", "prod"):
            if workspace_id in _env_workspaces(project.environments.get(env, {})):
                return (f"This workspace is {env.title()}'s, which changes only by promote. "
                        "Point the job at a Dev or scratch workspace.")
        return lakehouse.write_guard(lakehouse_name, schema, table) if lakehouse.write_guard else None

    def meta_project(self) -> dict[str, Any]:
        try:
            project = self._project()
        except LookupError:
            return {"path": None, "default_path": str(Path.home() / "Documents" / "FabricDesktop" / "metadata"),
                    **describe()}
        return project.summary() | describe()

    def meta_init(self, path: str, preset: str = "3", sample: bool = True) -> dict[str, Any]:
        owner = ""
        try:
            owner = (self.auth.signed_in_user() or "").split("@")[0]  # type: ignore[attr-defined]
        except Exception as exc:  # not signed in: the owner can be filled in later
            _log.info("Signed-in user unknown; the owner is left blank: %s", describe_error(exc))
        init_project(_project_folder(path), preset, owner, sample)
        self._save_settings(meta_project=str(Path(path)))
        return self.meta_project()

    def meta_open(self, path: str) -> dict[str, Any]:
        if not (_project_folder(path) / "layers.toml").exists():
            raise FileNotFoundError(f"{path} isn't a metadata project (it has no layers.toml)")
        self._save_settings(meta_project=str(Path(path)))
        return self.meta_project()

    # ---- environments and stores

    def _store(self, project: Project, env: str, create: bool = False) -> Store:
        if env not in ENVIRONMENTS:
            raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
        cfg = project.environments.get(env, {})
        if cfg.get("target", "onelake" if env != "dev" else "local") == "local":
            store: Store = LocalStore(project.local_data / env, env, landing=self._landing_dir(env))
            store.landing_zones = self._landing_resolver(local=True)  # type: ignore[attr-defined]
            return store
        ws = cfg.get("workspace_id")
        if not ws:
            raise NotSetUp(f"Assign a workspace to {env.title()} first (Data mesh > Deploy > Environments and promote).")
        # environments.toml can be edited by hand: never read or write one environment's tables as another's.
        if clash := [(a, b, w) for a, b, w in _clashes(project.environments) if env in (a, b)]:
            a, b, w = clash[0]
            raise ValueError(f"{a.title()} and {b.title()} both use workspace {w}, so neither is read or written "
                             f"until one gets its own workspace (Data mesh > Deploy > Environments and promote).")
        fabric = self.fabric  # type: ignore[attr-defined]
        # Each layer's lakehouse goes in its designation's workspace, else the environment's own.
        spaces = cfg.get("workspaces") or {}
        where = {l.lakehouse: (spaces.get(l.workspace) or {}).get("id") or ws for l in project.layers}
        where[META_LAKEHOUSE] = ws
        existing: dict[str, str] = {}
        for space in dict.fromkeys(where.values()):
            found = {lh["displayName"]: lh["id"] for lh in fabric.list_lakehouses(space)}
            existing.update({name: found[name] for name, w in where.items() if w == space and name in found})
        if create:
            for name, space in where.items():
                if name not in existing:
                    existing[name] = fabric.create_lakehouse(
                        space, name, "Managed by KratosForge's metadata framework")["id"]
        # The organization's landing zone is the platform's, never created here. With the central landing
        # workspace (Preview), each source's zone is looked up on the first landing read instead.
        platform = self._platform_config().get(env, {})  # type: ignore[attr-defined]
        if platform.get("landing_id"):
            existing[LANDING_LAKEHOUSE] = platform["landing_id"]
            where[LANDING_LAKEHOUSE] = platform["workspace_id"]
        store = OneLakeStore(env, ws, existing, lambda: self.auth.storage_token(),  # type: ignore[attr-defined]
                             self.root / "onelake-cache",
                             lakehouse_workspaces={n: w for n, w in where.items() if w != ws})
        store.landing_zones = self._landing_resolver()  # type: ignore[attr-defined]
        return store

    def _landing_dir(self, env: str) -> Path:
        """This PC's landing zone for a local environment, shared by every project on the PC."""
        return self.root / "platform" / f"landing-{env}"

    def _keys_store(self, project: Project, env: str, create: bool = False) -> Store:
        """Where an environment's surrogate keys are: the platform's SQL database once they've moved
        there (keys = "platform"), else LH_Keys, on this PC for a local environment or in its Keys workspace."""
        if env not in ENVIRONMENTS:
            raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
        cfg = project.environments.get(env, {})
        if self._keys_in_platform(project, env):
            return self._platform_keys(env)  # type: ignore[attr-defined,return-value]
        if cfg.get("target", "onelake" if env != "dev" else "local") == "local":
            return LocalStore(project.local_data / f"keys-{env}", env)
        ws = cfg.get("keys_workspace_id")
        if not ws:
            raise LookupError(f"Pick a Keys workspace for {env.title()} first (Data mesh > Design > Surrogate keys).")
        fabric = self.fabric  # type: ignore[attr-defined]
        existing = {lh["displayName"]: lh["id"] for lh in fabric.list_lakehouses(ws)}
        if create and keys_mod.KEYS_LAKEHOUSE not in existing:
            existing[keys_mod.KEYS_LAKEHOUSE] = fabric.create_lakehouse(
                ws, keys_mod.KEYS_LAKEHOUSE, "Surrogate keys and source systems, managed by KratosForge")["id"]
        return OneLakeStore(env, ws, existing, lambda: self.auth.storage_token(),  # type: ignore[attr-defined]
                            self.root / "onelake-cache")

    def _keys_database(self, project: Project, env: str) -> str:
        """The SQL database an environment's keys come from once they're in the platform, else "".
        A Spark MERGE into LH_Keys would issue keys that database never sees, so there's none then."""
        if not self._keys_in_platform(project, env):
            return ""
        return "SQL_Keys" if self._keys_config().get("shared") else "SQL_Platform"  # type: ignore[attr-defined]

    def _keys_in_platform(self, project: Project, env: str) -> bool:
        """One key system for everything: once an environment's platform is in Fabric, its keys come
        from SQL_Platform, unless the project already issues keys in LH_Keys (those move first, with
        their numbers, from Surrogate keys > Move keys)."""
        cfg = project.environments.get(env, {})
        if cfg.get("keys") in ("platform", "lakehouse"):
            return cfg["keys"] == "platform"
        if not self._keys_in_fabric(env):  # type: ignore[attr-defined]
            return False
        local = cfg.get("target", "onelake" if env != "dev" else "local") == "local"
        has_lh_keys = (project.local_data / f"keys-{env}").exists() if local else bool(cfg.get("keys_workspace_id"))
        return not has_lh_keys

    def _uses_keys(self, project: Project, names: list[str] | None = None) -> bool:
        return any(keys_mod.entity_keys(e) for n, e in project.entities.items() if names is None or n in names)

    def _write_environments(self, project: Project, envs: dict[str, dict[str, Any]]) -> None:
        if errors := shared_workspaces(envs, before=project.environments):
            raise ValueError(errors[0])
        lines = ["# Each environment's workspace, and where its tables live: target = \"onelake\" (the workspace's",
                 "# lakehouses, LH_<Layer>, created when first needed) or \"local\" (offline, in .local).",
                 "# compute = \"local\" runs on this PC, \"fabric\" on the workspace's capacity.",
                 "# keys_workspace_id is the workspace that holds LH_Keys, the surrogate keys.", ""]
        lines += ["# workspaces: a workspace per designation (ingest, product, serve, sandbox); layers pick one",
                  "# with workspace = \"...\" in layers.toml. keys = \"platform\": surrogate keys are in SQL_Platform.",
                  "# require_commit: Test and Prod in OneLake only take files a pushed Git commit holds;",
                  "# false lets a promote go ahead with a note, true asks it of an environment on this PC too.", ""]
        for name in ENVIRONMENTS:
            lines.append(f"[{name}]")
            lines += [f"{k} = {_toml(v)}" for k, v in envs.get(name, {}).items()]
            lines.append("")
        write_text(project.root / "environments.toml", "\n".join(lines))

    def meta_set_environment(self, env: str, workspace_id: str | None = None, workspace_name: str | None = None,
                             target: str | None = None) -> dict[str, Any]:
        """Assign an environment's workspace, and say where its tables live.

        Every environment keeps its workspace, whichever computer runs it. ``target`` is
        "onelake" (the workspace's lakehouses, read and written from this PC or Fabric) or
        "local" (offline: Delta tables in the project's .local folder). Assigning a workspace
        without a target points the environment at it."""
        if env not in ENVIRONMENTS:
            raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
        if target not in (None, "local", "onelake"):
            raise ValueError("target is onelake (the workspace) or local (this PC only)")
        project = self._project()
        envs = {k: dict(v) for k, v in project.environments.items()}
        cfg = envs.setdefault(env, {})
        if workspace_id is not None:
            cfg["workspace_id"] = workspace_id
            cfg["workspace_name"] = workspace_name or ""
            target = target or ("onelake" if workspace_id else "local")
        if target == "onelake" and not cfg.get("workspace_id"):
            raise NotSetUp(f"Assign a workspace to {env.title()} first.")
        if target:
            cfg["target"] = target
        if cfg.get("target", "onelake" if env != "dev" else "local") == "local":
            cfg.pop("compute", None)  # Fabric can't reach tables on this PC
        self._write_environments(project, envs)
        return self.meta_project()

    def meta_create_workspace(self, env: str, name: str, capacity_id: str = "") -> dict[str, Any]:
        """Create a Fabric workspace with the user's own sign-in and assign it to the environment."""
        if env not in ENVIRONMENTS:
            raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
        name = name.strip()
        if not name:
            raise ValueError("Give the workspace a name.")
        ws = self.fabric.create_workspace(  # type: ignore[attr-defined]
            name, capacity_id, f"{env.title()} environment, managed by KratosForge's metadata framework")
        return self.meta_set_environment(env, ws["id"], ws.get("displayName") or name) | {
            "created": {"id": ws["id"], "displayName": ws.get("displayName") or name}}

    def meta_set_keys_workspace(self, env: str, workspace_id: str, workspace_name: str = "") -> dict[str, Any]:
        """Point an environment at the workspace that holds its LH_Keys."""
        project = self._project()
        if env not in ENVIRONMENTS:
            raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
        envs = dict(project.environments)
        envs[env] = dict(envs.get(env, {})) | {"keys_workspace_id": workspace_id,
                                               "keys_workspace_name": workspace_name}
        self._write_environments(project, envs)
        return self.meta_keys(env)

    # ---- entities

    def meta_entity(self, name: str, env: str = "dev") -> dict[str, Any]:
        project = self._project()
        e = project.entities[name]
        runs: list[dict[str, Any]] = []
        preview: dict[str, Any] | None = None
        try:
            store = self._store(project, env)
            log = store.meta_where("run_log", pl.col("entity") == name).reverse()
            runs = _json_rows(log.drop("steps"), 10)
            if e.layer in project.layer_names():
                preview = _preview(store, project.layer(e.layer).lakehouse, e.schema, e.table, 50)
        except Exception as exc:  # the config is still useful when the environment can't be read
            runs = [{"status": "unavailable", "message": str(exc)}]
        errors = [x for x in project.summary()["errors"] if x.startswith(name + ":")]
        keys_sql, keys_from = None, self._keys_database(project, env)
        if keys_mod.entity_keys(e) and not errors and "entity" in e.spec.get("source", {}) and not keys_from:
            keys_sql = keys_mod.merge_sql(project, e, project.environments.get(env, {}).get("keys_workspace_name", ""))
        return e.summary() | {"text": e.path.read_text(encoding="utf-8"), "impact": impact(project, name),
                              "runs": runs, "preview": preview, "errors": errors, "keys_sql": keys_sql,
                              "keys_from": keys_from}

    def meta_save_entity(self, name: str, text: str) -> dict[str, Any]:
        """Save an entity file. TOML that doesn't parse is refused; other problems are listed."""
        project = self._project()
        try:
            spec = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Not saved: the file isn't valid TOML ({exc})") from exc
        new_name = spec.get("name")
        if not new_name:
            raise ValueError("Not saved: the file needs a name, like name = \"silver.orders\"")
        for n in (name, new_name):
            if problem := entity_name_problem(n):
                raise ValueError(f"Not saved: {n}: {problem}")
        path = project.entities_dir / f"{new_name}.toml"
        if new_name != name:
            if path.exists():
                raise ValueError(f"Not saved: {new_name} already exists")
            downstream = impact(project, name) if name in project.entities else []
            if downstream:
                raise GateError("save", [f"can't rename {name}: {', '.join(downstream)} read it"])
        write_text(path, text)
        if new_name != name:  # only once the new file is in place, so a failed save loses neither
            (project.entities_dir / f"{name}.toml").unlink(missing_ok=True)
        return self.meta_entity(new_name)

    def meta_new_entity(self, template: str, name: str, source: str = "", keys: list[str] | None = None,
                        owner: str = "") -> dict[str, Any]:
        project = self._project()
        if template not in TEMPLATES:
            raise ValueError(f"template must be one of {', '.join(TEMPLATES)}")
        if name in project.entities:
            raise ValueError(f"{name} already exists")
        if problem := entity_name_problem(name):
            raise ValueError(f"{name}: {problem}")
        layer = name.split(".")[0]
        if layer not in project.layer_names():
            raise ValueError(f"{layer} is not a layer; use one of {', '.join(project.layer_names())}")
        sample = None
        if template in ("files", "landing") and source:
            try:
                store = self._store(project, "dev")
                # Dev reads no zone it doesn't read in a run: the contract then starts with a placeholder.
                where = (Engine(project, store).landing_lakehouse(source) if template == "landing"
                         else project.layer(layer).lakehouse)
                sample, _ = store.read_files(where, source)
            except Exception:  # no files yet: the contract starts with a placeholder column
                sample = None
        if not owner:
            try:
                owner = (self.auth.signed_in_user() or "").split("@")[0]  # type: ignore[attr-defined]
            except Exception:
                owner = ""
        text, files = entity_text(project, template, name, owner, source, keys, sample)
        for rel, content in files.items():
            p = project.root / rel
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                write_text(p, content)
        write_text(project.entities_dir / f"{name}.toml", text)
        return self.meta_entity(name)

    def meta_delete_entity(self, name: str) -> dict[str, Any]:
        project = self._project()
        downstream = impact(project, name)
        if downstream:
            raise GateError("save", [f"can't delete {name}: {', '.join(downstream)} read it"])
        project.entities[name].path.unlink()
        return {"deleted": name}

    def meta_set_layers(self, layers: list[dict[str, Any]]) -> dict[str, Any]:
        project = self._project()
        project.set_layers([Layer(name=l["name"].strip().lower(), drift=l.get("drift", "ignore_new"),
                                  required_checks=list(l.get("required_checks", [])),
                                  reads_from=list(l.get("reads_from", [])), description=l.get("description", ""),
                                  require_keys=bool(l.get("require_keys", False)),
                                  retention_days=_days(l.get("retention_days")),
                                  workspace=str(l.get("workspace") or ""))
                            for l in layers])
        return self.meta_project()

    # ---- runs

    def meta_run(self, names: list[str] | None = None, env: str = "dev", backfill: str | None = None,
                 cascade: bool = False) -> list[dict[str, Any]]:
        project = self._project()
        if self._compute(project, env) == "fabric":  # type: ignore[attr-defined]
            store = self._store(project, env, create=env == "dev")
            return self._run_in_fabric(project, env, store, names, backfill, cascade)  # type: ignore[attr-defined]
        return self._run_here(project, env, names, backfill, cascade)

    def meta_preflight(self, name: str, env: str = "dev", backfill: str | None = None) -> dict[str, Any]:
        """What running ``name`` on this PC would read and whether it fits in memory, from the Delta
        log only: for the run dialog."""
        project = self._project()
        store = self._store(project, env)
        project = self._released(project, env, store)
        engine = Engine(project, store, source_options=self._source_options,
                        link_table=self._link_table(project, env))  # type: ignore[attr-defined]
        return engine.preflight(name, backfill)

    def _released(self, project: Project, env: str, store: Store) -> Project:
        """What ``env`` runs: in Test and Prod, the files of the release it is on, as it keeps them
        (meta/snapshot.py), whatever this folder holds now. An environment last promoted before
        releases kept their files runs this folder, which must then be on that release."""
        if env == "dev":
            return project
        deps = store.meta("deployments").filter(pl.col("env") == env)
        if not deps.height or deps["version"][-1] == project.version:
            return project
        try:
            return snapshot.restore(project, store, deps["version"][-1])
        except LookupError:
            return project  # Engine.check_deployed says the folder isn't on the release

    def lease_backend(self, project: Project, env: str, store: Store, ttl: float = 120) -> Backend:
        """Where an environment's per-table leases live: its LH_Meta, or a local folder (leases.py)."""
        if isinstance(store, OneLakeStore):
            meta_id = store.lakehouse_ids.get(META_LAKEHOUSE)
            if not meta_id:
                raise NotSetUp(f"{META_LAKEHOUSE} doesn't exist in the {env} workspace yet; promote to {env} first")
            return OneLakeBackend(store.workspace_id, meta_id, self.auth.storage_token,  # type: ignore[attr-defined]
                                  timeout=lease_timeout(ttl))
        if isinstance(store, LocalStore):
            return LocalBackend(store.root / "_leases")
        raise TypeError(f"no lease backend for {type(store).__name__}")

    def _table_lock(self, project: Project, env: str, store: Store, owner: str = WRITER, seconds: float = 120,
                    guard: Callable[[], None] | None = None) -> Callable[[Any], AbstractContextManager[None]]:
        """The Engine's ``table_lock``: each table is written under the environment's lease on it and,
        in OneLake, the lease in the table's own lakehouse that notebooks and jobs hold too.
        ``guard`` runs once both are held, before anything is written."""
        leases = Leases(self.lease_backend(project, env, store, ttl=seconds), owner=owner, ttl=seconds, wait=seconds)

        @contextmanager
        def table_lock(e: Any) -> Iterator[None]:
            lakehouse = project.layer(e.layer).lakehouse
            with ExitStack() as held:
                held.enter_context(leases.hold(table_key(lakehouse, e.schema, e.table)))
                if isinstance(store, OneLakeStore) and (lakehouse_id := store.lakehouse_ids.get(lakehouse)):
                    held.enter_context(onelake_table_lease(
                        store.workspace_of(lakehouse), lakehouse_id, e.schema, e.table,
                        self.auth.storage_token, owner=owner, ttl=seconds, wait=seconds))  # type: ignore[attr-defined]
                if guard:
                    guard()
                yield
        return table_lock

    def _run_here(self, project: Project, env: str, names: list[str] | None = None, backfill: str | None = None,
                  cascade: bool = False) -> list[dict[str, Any]]:
        """Run on this PC, whatever the environment's compute (schedules on this PC use it)."""
        store = self._store(project, env, create=env == "dev")
        project = self._released(project, env, store)
        keys_store = self._keys_store(project, env, create=env == "dev") if self._uses_keys(project, names) else None
        engine = Engine(project, store, keys_store=keys_store, source_options=self._source_options,
                        link_table=self._link_table(project, env),  # type: ignore[attr-defined]
                        table_lock=self._table_lock(project, env, store))
        if cascade and names and len(names) == 1:
            results: list[RunResult] = engine.backfill(names[0])
        else:
            results = engine.run_many(names, backfill=backfill)
        out = [r.to_dict() for r in results]
        self._after_run(project, env, store, out)  # type: ignore[attr-defined]
        return out

    def meta_runs(self, env: str = "dev", limit: int = 100) -> list[dict[str, Any]]:
        project = self._project()
        log = self._store(project, env).meta("run_log").reverse()
        return [r | {"ran_on": ran_on(r)} for r in _json_rows(log, limit)]

    def meta_run_detail(self, run_id: str, env: str = "dev") -> dict[str, Any]:
        project = self._project()
        store = self._store(project, env)
        run = store.meta_where("run_log", pl.col("run_id") == run_id)
        if not run.height:
            raise KeyError(f"no run {run_id} in {env}")
        row = _json_rows(run, 1)[0]
        row["ran_on"] = ran_on(row)
        row["steps"] = json.loads(row.get("steps") or "[]")
        row["dq"] = _json_rows(store.meta_where("dq_results", pl.col("run_id") == run_id), 100)
        return row

    def meta_preview(self, name: str, env: str = "dev", limit: int = 100) -> dict[str, Any]:
        project = self._project()
        e = project.entities[name]
        out = _preview(self._store(project, env), project.layer(e.layer).lakehouse, e.schema, e.table, limit)
        return out or {"columns": [], "rows": [], "total": 0}

    # ---- promote

    def meta_promote_check(self, source: str, target: str) -> dict[str, Any]:
        project = self._project()
        try:
            errors = promote_checks(project, self._store(project, source), target, dt.date.today())
        except LookupError as exc:
            errors = [str(exc)]
        test_errors, tests = self._test_gate(project)  # type: ignore[attr-defined]
        errors += test_errors
        cfg = project.environments.get(target, {})
        if (project.key_tables or project.source_systems) and cfg.get("target", "onelake") != "local" \
                and not cfg.get("keys_workspace_id") and not self._keys_in_platform(project, target):
            errors.append(f"Pick a Keys workspace for {target.title()} first (Data mesh > Design > Surrogate keys)")
        errors += self._product_gate(project)
        breaking, notes = self._breaking_gate(project, target)
        errors += breaking
        deprecated, link_notes = self._deprecation_gate(project)
        errors += deprecated
        notes += link_notes
        git = release.status(project.root)
        unreleased, more = self._release_gate(project, target, git)
        errors += unreleased
        duty_errors, duty_notes = self._duties_gate(project, source, target)
        errors += duty_errors
        policies = self.governance_promote_gate(target)  # type: ignore[attr-defined]
        errors += policies["errors"]
        return {"source": source, "target": target, "version": project.version, "errors": errors, "tests": tests,
                "notes": notes + more + duty_notes + policies["notes"], "git": git,
                "policy_exceptions": policies["excepted"]}

    def meta_promote(self, source: str, target: str) -> dict[str, Any]:
        project = self._project()
        src = self._store(project, source)
        errors = promote_checks(project, src, target, dt.date.today())
        errors += self._test_gate(project)[0]  # type: ignore[attr-defined]  # custom code is tested before it moves on
        errors += self._product_gate(project)
        breaking, notes = self._breaking_gate(project, target)
        errors += breaking
        deprecated, link_notes = self._deprecation_gate(project)
        errors += deprecated
        notes += link_notes
        git = release.status(project.root)
        unreleased, release_notes = self._release_gate(project, target, git)
        errors += unreleased
        errors += self._duties_gate(project, source, target, src)[0]
        policies = self.governance_promote_gate(target)  # type: ignore[attr-defined]  # Block policies (GOV-003)
        errors += policies["errors"]
        if errors:
            raise GateError("promote", errors)
        target_store = self._store(project, target, create=True)
        uses_keys = bool(project.key_tables or project.source_systems)
        keys_store = self._keys_store(project, target, create=True) if uses_keys else None  # fails before promoting
        result = promote(project, src, target_store, dt.date.today(),
                         release=release.record(project, source, target, git, self._signed_in_object_id()))
        if keys_store is not None:
            # Each environment issues its own keys; promote copies the source systems and seeds new key tables.
            result["keys"] = keys_mod.sync(project, keys_store)
        if project.source_systems and project.environments.get(target, {}).get("workspace_id"):
            try:  # so notebooks in the target read its own connections from VL_Sources
                self._publish_varlib(project, target)  # type: ignore[attr-defined]
                result["variable_library"] = "updated"
            except Exception as exc:  # the promote itself worked; publish it from Source systems
                result["variable_library"] = f"not updated: {exc}"
        if notes:
            result["breaking"] = notes
        if release_notes:
            result["release"]["notes"] = release_notes
        if policies["excepted"] or policies["notes"]:
            result["policies"] = {"excepted": policies["excepted"], "notes": policies["notes"]}
        if project.products:
            try:  # so other teams' links find this environment's tables
                published = self.mesh_publish(target)  # type: ignore[attr-defined]
                result["products"] = published["published"]
                if published.get("problems"):
                    result["product_problems"] = published["problems"]
            except Exception as exc:  # the promote itself worked; publish again from Data mesh > Products and links > Data products
                result["products"] = f"not published: {exc}"
        if self._compute(project, target) == "fabric":  # type: ignore[attr-defined]
            try:  # so scheduled runs in Fabric pick up the promoted version
                self._fabric_bundle(project, target, target_store)  # type: ignore[attr-defined]
                result["fabric"] = "synced"
            except Exception as exc:  # the promote itself worked; the next run uploads again
                result["fabric"] = f"not synced: {exc}"
        return result

    def meta_lineage(self, name: str, column: str | None = None) -> dict[str, Any]:
        """Column lineage from the project's files: every column of ``name``, or, for one column,
        the steps back to where it starts and every column built from it further on."""
        project = self._project()
        if name not in project.entities:
            raise LookupError(f"No entity {name}")
        if column is None:
            return {"entity": name, "columns": lineage.entity_columns(project, name)}
        return {"entity": name, "column": column, "upstream": lineage.upstream(project, name, column),
                "downstream": lineage.downstream(project, name, column)}

    def meta_git_status(self) -> dict[str, Any]:
        """Where the project's files stand in Git (read with this PC's git; no network)."""
        return release.status(self._project().root)

    def meta_releases(self, env: str) -> dict[str, Any]:
        """Every release to ``env``, newest first, and whether each can be rolled back to. Reads only."""
        project = self._project()
        try:
            history = releases(self._store(project, env))
        except Exception as exc:  # noqa: BLE001 - the environment can't be read: say so
            return {"env": env, "releases": [], "problem": str(exc)}
        return {"env": env, "releases": history, "problem": None}

    def meta_rollback_preview(self, env: str, version: str) -> dict[str, Any]:
        """What rolling ``env`` back to ``version`` changes, by meaning, entity by entity. Reads only."""
        project = self._project()
        store = self._store(project, env)
        history = releases(store)
        if not history:
            raise LookupError(f"Nothing has been promoted to {env.title()} yet.")
        configs = store.meta("entities")

        def texts(v: str) -> dict[str, str]:
            rows = configs.filter(pl.col("version") == v).unique("name", keep="last")
            return {r["name"]: r["config"] for r in rows.iter_rows(named=True)}
        changes = diff_mod.project_changes(texts(history[0]["version"]), texts(version))
        return {"env": env, "from": history[0]["version"], "to": version, "entities": changes}

    def meta_rollback(self, env: str, version: str) -> dict[str, Any]:
        """Put Test or Prod back on an earlier release, from the files that environment keeps. The
        workspace role check of a promote applies; tables keep their rows until their next run."""
        if env == "dev":
            raise ValueError("Dev runs this folder; roll back with Git instead.")
        project = self._project()
        errors, _ = self._duties_gate(project, env, env, separation=False)  # undoing a release isn't a second promote
        if errors:
            raise GateError("rollback", errors)
        store = self._store(project, env)
        result = rollback(project, store, version, duties.current_user(), self._signed_in_object_id())
        released = result.pop("project")
        if self._compute(project, env) == "fabric":  # type: ignore[attr-defined]
            try:  # so scheduled runs in Fabric run the release rolled back to
                self._fabric_bundle(released, env, store)  # type: ignore[attr-defined]
                result["fabric"] = "synced"
            except Exception as exc:  # the rollback itself worked; the next run uploads again
                result["fabric"] = f"not synced: {exc}"
        return result

    def meta_last_release(self, env: str) -> dict[str, Any]:
        """The last release promoted to ``env`` from its ``meta.deployments`` row, and whether the
        stored receipt still hashes to its id. Reads only."""
        project = self._project()
        try:
            deps = self._store(project, env).meta("deployments")
        except Exception as exc:  # noqa: BLE001 - the environment can't be read: say so
            return {"env": env, "release": None, "problem": str(exc)}
        rows = [r for r in deps.iter_rows(named=True) if r.get("env") == env] if deps.height else []
        if not rows:
            return {"env": env, "release": None, "problem": None}
        row = rows[-1]
        keep = ("version", "source_env", "at", "commit", "branch", "dirty", "pushed", "engine_version", "lock_hash",
                "by", "receipt_id")
        out = {k: row.get(k) for k in keep}
        receipt = row.get("receipt")
        out["verified"] = release.verified(receipt, row.get("receipt_id"))
        out["receipt"] = receipt  # the canonical record, for Copy record
        return {"env": env, "release": out, "problem": None}

    def _release_gate(self, project: Project, target: str, git: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Promoting files no pushed commit holds. Chris decided (D1, 2026-10-25) that Test and Prod
        only take released files, so this blocks when the target's tables are in OneLake. An environment
        kept on this PC is a note, as is any environment with ``require_commit = false`` in
        environments.toml; ``require_commit = true`` blocks on this PC too."""
        found = release.problems(git)
        if not found or target == "dev":
            return [], []
        cfg = project.environments.get(target, {})
        if cfg.get("require_commit", cfg.get("target", "onelake") != "local"):
            errors = [f"{target.title()} only takes released files: {p}" for p in found]
            if "require_commit" not in cfg:  # the default, not the team's own choice: say how to go ahead without Git
                errors[-1] = errors[-1].rstrip(".") + (f". To promote without a pushed commit, set require_commit = false under [{target}] "
                               "in environments.toml")
            return errors, []
        return [], [f"The release record can't name a pushed commit that holds these files: {'; '.join(found)}"]

    def _signed_in_object_id(self) -> str | None:
        """The signed-in person's Entra object id, for release records; None when not signed in.
        Never starts a sign-in."""
        auth = getattr(self, "_auth", None)
        try:
            return getattr(auth, "signed_in_object_id", lambda: None)()
        except Exception as exc:  # noqa: BLE001 - the record then names the PC login only
            _log.info("No signed-in object id for the release record: %s", describe_error(exc))
            return None

    def _duties_gate(self, project: Project, source: str, target: str,
                     src: Any = None, separation: bool = True) -> tuple[list[str], list[str]]:
        """Before writing to Test or Prod: the person's workspace roles there, and with
        ``separate_promoter = true`` on the target, that they didn't promote this version to the
        source themselves (FD-130). Reads only; see duties.py."""
        cfg = project.environments.get(target, {})
        if target == "dev":
            return [], []
        errors: list[str] = []
        notes: list[str] = []
        if cfg.get("separate_promoter") and separation:
            try:
                store = src or self._store(project, source)
                deps = store.meta("deployments")
                rows = [r for r in deps.iter_rows(named=True)
                        if r.get("env") == source and r.get("version") == project.version] if deps.height else []
            except Exception:  # noqa: BLE001 - promote_checks already says when the source can't be read
                rows = []
            last = rows[-1] if rows else {}
            their_id, my_id = duties.recorded_object_id(last.get("receipt")), self._signed_in_object_id()
            problem = duties.separation_problem(source, target, project.version, last.get("by"),
                                                duties.current_user(), their_id, my_id)
            if problem:
                errors.append(problem)
            if last and (note := duties.separation_note(their_id, my_id)):
                notes.append(note)
        if cfg.get("target", "onelake") == "local":
            return errors, notes  # tables on this PC: no workspace roles to check
        auth = getattr(self, "_auth", None)  # already signed in by the store; never start a sign-in here
        me = {v.lower() for v in (getattr(auth, "signed_in_object_id", lambda: None)(),
                                  getattr(auth, "signed_in_user", lambda: None)()) if v}
        spaces = {cfg.get("workspace_id"): cfg.get("workspace_name")} | {
            (s or {}).get("id"): (s or {}).get("name") for s in (cfg.get("workspaces") or {}).values()}
        for ws, name in spaces.items():
            if not ws:
                continue
            label = f"{target.title()} workspace {name or ws}"
            try:
                assignments, error = self.fabric.list_role_assignments(ws), None  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - reading roles can need more than writing tables
                assignments, error = None, str(exc)
            e, n = duties.role_problems(label, assignments, me, error)
            errors += e
            notes += n
        return errors, notes

    def _breaking_gate(self, project: Project, target: str) -> tuple[list[str], list[str]]:
        """Columns other projects read in ``target`` may not be dropped or retyped without a new
        product version (mesh.breaking_changes). Unreachable catalog: a note, not a block."""
        if not any(p.status != "draft" for p in project.products.values()):
            return [], []
        try:
            published = self._catalog().call("list_products")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the promote itself doesn't need the catalog
            return [], [f"couldn't check what other projects read ({exc}); check the products' consumers before promoting"]
        return mesh.breaking_changes(project, published, target, mesh.utc_today())

    def _deprecation_gate(self, project: Project) -> tuple[list[str], list[str]]:
        """A link to a product deprecated on a date that has come blocks the promote; one with a date
        ahead is a note (mesh.deprecated_links). Runs aren't stopped. Unreachable catalog: a note."""
        if not project.links:
            return [], []
        try:
            published = self._catalog().call("list_products")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the promote itself doesn't need the catalog
            return [], [f"couldn't check whether the products this project links to are deprecated ({exc})"]
        return mesh.deprecated_links(project, published, mesh.utc_today())

    def _product_gate(self, project: Project) -> list[str]:
        """Products other teams read must be documented and owned before they move on."""
        if not project.products:
            return []
        try:
            saved = self._catalog().call("enrichment", project=mesh.project_name(project))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - the catalog is unreachable: judge on the text in Git alone
            saved = {}
        return mesh.publish_problems(project, saved)

    # ---- surrogate keys and source systems

    def meta_keys(self, env: str = "dev", platform: bool = False) -> dict[str, Any]:
        """The project's key tables and source systems. With ``platform``, also the platform's one
        list of source systems and how the project's own compare with it (Sources this project reads)."""
        project = self._project()
        summary = project.summary()
        cfg = project.environments.get(env, {})
        local = cfg.get("target", "onelake" if env != "dev" else "local") == "local"
        tables, problem = summary["key_tables"], None
        try:
            store = self._keys_store(project, env)
            tables = [t | keys_mod.key_stats(store, project.key_tables[t["name"]]) for t in tables]
        except Exception as exc:  # the definitions are still useful when LH_Keys can't be read
            problem = str(exc)
        central = None  # the platform's key tables, when this environment's keys are there
        if self._keys_in_platform(project, env):
            try:
                central = self._keys_platform(env).call("list_key_tables")  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                problem = problem or str(exc)
        return {"env": env, "local": local, "path": str(project.local_data / f"keys-{env}") if local else None,
                "workspace_id": cfg.get("keys_workspace_id", ""), "workspace_name": cfg.get("keys_workspace_name", ""),
                "lakehouse": self._keys_database(project, env) or keys_mod.KEYS_LAKEHOUSE,
                "platform": self._keys_in_platform(project, env), "moved_at": cfg.get("keys_moved_at", ""),
                "platform_available": self._keys_in_fabric(env),  # type: ignore[attr-defined]
                "shared": bool(self._keys_config().get("shared")),  # type: ignore[attr-defined]
                "key_tables": tables, "central_key_tables": central,
                "source_systems": summary["source_systems"], "system_types": list(keys_mod.SYSTEM_TYPES),
                "connection_fields": list(keys_mod.CONNECTION_FIELDS), "problem": problem,
                "errors": [x for x in summary["errors"] if x.startswith(("keys/", "source system"))]} \
            | (self._platform_sources_view(summary["source_systems"]) if platform else {})

    def _read_platform_sources(self) -> list[dict[str, Any]]:
        try:
            return self._catalog().call("list_sources")  # type: ignore[attr-defined]
        except NotSetUp:
            raise  # its own words say what to do; Finish or repair wouldn't help
        except LookupError as exc:
            raise LookupError(f"{exc} After an update, a platform admin runs Platform > Settings > Finish or repair.") from exc

    def _platform_sources_view(self, systems: list[dict[str, Any]]) -> dict[str, Any]:
        """Mark each of the project's source systems with the platform source it is (same code),
        and say where the two disagree on the id, which business keys and loaded rows depend on."""
        try:
            listed = self._read_platform_sources()
        except Exception as exc:  # noqa: BLE001 - the project's own sources still show
            return {"platform_sources": None, "platform_problem": f"The platform's sources couldn't be read: {exc}",
                    "source_problems": []}
        by_name = {p["name"]: p for p in listed}
        problems = []
        for s in systems:
            p = by_name.get(s["code"].lower())
            s["platform"] = {k: p.get(k) for k in ("name", "source_id", "code", "title", "owner", "system_type",
                                                    "description")} if p else None
            if p and p.get("source_id") is not None and p["source_id"] != s["id"]:
                problems.append(f"{s['code']} is id {p['source_id']} in the platform but {s['id']} in this project. "
                                "Rows already loaded keep this project's id; ask a platform admin which one stays.")
        codes = {s["code"].lower() for s in systems}
        return {"platform_sources": [p | {"read": p["name"] in codes} for p in listed], "platform_problem": None,
                "source_problems": problems}

    def meta_source_read(self, name: str, env: str = "dev") -> dict[str, Any]:
        """Start reading one of the platform's source systems: the project gets it with the
        platform's id, code and name, and its own connection in each environment."""
        project = self._project()
        src = next((p for p in self._read_platform_sources() if p["name"] == str(name).strip().lower()), None)
        if not src:
            raise ValueError(f"{name} isn't one of the platform's source systems")
        if any(s.code.lower() == src["name"] for s in project.source_systems):
            raise ValueError(f"This project already reads {src['code']}")
        clash = next((s for s in project.source_systems if s.id == src.get("source_id")), None)
        if clash:
            raise ValueError(f"id {clash.id} is {clash.code} in this project. Move sources to the platform first, "
                             "so every project uses the same ids.")
        system = {"id": src["source_id"], "code": src["code"], "name": src["title"], "system_type": src["system_type"],
                  "owner": src["owner"], "description": src["description"], "active": src["active"]}
        return self.meta_save_source_system(system, None, env)

    def meta_sources_move(self, env: str = "dev") -> dict[str, Any]:
        """Move sources to the platform: copy the project's own source systems into the platform's
        one list, keeping their ids (like Move keys). Connections stay in the project."""
        project = self._project()
        systems = [{k: v for k, v in s.to_dict().items() if k != "connections"} for s in project.source_systems]
        res = self._catalog().call("adopt_sources", systems=systems, project=project.root.name.lower())  # type: ignore[attr-defined]
        return {"moved": res["moved"], "already": res["already"], "conflicts": res["conflicts"],
                "keys": self.meta_keys(env, platform=True)}

    def meta_new_key_table(self, name: str, description: str = "", env: str = "dev") -> dict[str, Any]:
        project = self._project()
        name = name.strip().lower()
        if name in project.key_tables:
            raise ValueError(f"key table {name} already exists")
        if self._keys_in_platform(project, env):
            # One key system: the project uses a key table the platform has, it doesn't make its own.
            central = {t["name"] for t in self._keys_platform(env).call("list_key_tables")}  # type: ignore[attr-defined]
            if name not in central:
                raise GateError("save", [f"{name} isn't one of the platform's key tables. Request it in "
                                         "Platform > Set up > Business keys, or pick one from the list."])
        owner = ""
        try:
            owner = self.auth.signed_in_user() or ""  # type: ignore[attr-defined]
        except Exception as exc:
            _log.info("Signed-in user unknown; the key table's owner is left blank: %s", describe_error(exc))
        keys_mod.write_key_table(project.root, name, owner, description)
        return self.meta_keys(env)

    def meta_delete_key_table(self, name: str, env: str = "dev") -> dict[str, Any]:
        project = self._project()
        users = [e.name for e in project.entities.values() if name in [k["table"] for k in keys_mod.entity_keys(e)]]
        if users:
            raise GateError("save", [f"can't delete key table {name}: {', '.join(users)} use it"])
        project.key_tables[name].path.unlink()
        return self.meta_keys(env)

    def meta_key_lookup(self, table: str, value: str, env: str = "dev") -> dict[str, Any] | None:
        project = self._project()
        return keys_mod.lookup(self._keys_store(project, env), project.key_tables[table], value)

    def meta_save_source_system(self, system: dict[str, Any], original_id: int | None = None,
                                env: str = "dev") -> dict[str, Any]:
        project = self._project()
        new = keys_mod.source_system_from_dict(system)
        # A source the platform lists is described there, once for every project: only its
        # connections are the project's own.
        try:
            p = next((p for p in self._read_platform_sources() if p["name"] == new.code.lower()), None)
        except Exception:  # noqa: BLE001 - without the platform the project's own fields stand
            p = None
        if p:
            new.code, new.name, new.system_type = p["code"], p["title"], p["system_type"]
            new.owner, new.description, new.active = p["owner"], p["description"], p["active"]
        # original_id is the id being edited; without it this is a new source system.
        others = [s for s in project.source_systems if original_id is None or s.id != int(original_id)]
        if original_id is not None and new.id != int(original_id):
            raise ValueError("A source system's id never changes once it is saved")
        if any(s.id == new.id for s in others):
            raise ValueError(f"id {new.id} is already used by another source system")
        if any(s.code.upper() == new.code.upper() for s in others):
            raise ValueError(f"code {new.code} is already used by another source system")
        path = keys_mod.keys_dir(project.root) / keys_mod.SOURCE_SYSTEMS_FILE
        old = path.read_text(encoding="utf-8") if path.exists() else None
        keys_mod.write_source_systems(project.root, others + [new])
        project.reload()
        problems = [x for x in keys_mod.validate_keys(project) if x.startswith(("source system", "keys/source"))]
        if problems:
            if old is None:
                path.unlink()
            else:
                write_text(path, old)
            raise GateError("save", problems)
        return self.meta_keys(env)

    def meta_delete_source_system(self, id: int, env: str = "dev") -> dict[str, Any]:
        project = self._project()
        keys_mod.write_source_systems(project.root, [s for s in project.source_systems if s.id != int(id)])
        return self.meta_keys(env)

    def meta_keys_sync(self, env: str = "dev") -> dict[str, Any]:
        """Write the source system tables and create any missing key tables in the environment's LH_Keys."""
        project = self._project()
        errors = [x for x in validate(project, dt.date.today()) if x.startswith(("keys/", "source system"))]
        if errors:
            raise GateError("save", errors)
        return keys_mod.sync(project, self._keys_store(project, env, create=True))

    def meta_key_notebook(self, name: str, env: str = "dev") -> dict[str, Any]:
        """A template notebook for the entity whose first cell is the surrogate key MERGE."""
        project = self._project()
        e = project.entities[name]
        errors = [x for x in validate(project, dt.date.today()) if x.startswith(name + ":")]
        if errors:
            raise GateError("save", errors)
        if not keys_mod.entity_keys(e):
            raise ValueError(f"{name} has no [[keys]] block, so there is no key MERGE to start its notebook with")
        if db := self._keys_database(project, env):
            raise GateError("save", [f"{env.title()}'s keys come from {db}, so a notebook that merges into LH_Keys "
                                     "would give rows keys the rest of the platform doesn't have. Run "
                                     f"{name} from Data mesh instead: its run takes the keys from {db}."])
        src = project.entities[e.spec["source"]["entity"]]
        nb_name = f"nb_{src.layer}_to_{e.layer}_{e.table}"
        cells = keys_mod.template_cells(project, e, project.environments.get(env, {}).get("keys_workspace_name", ""))
        self.notebooks.save(nb_name, cells)  # type: ignore[attr-defined]
        return {"name": nb_name}
