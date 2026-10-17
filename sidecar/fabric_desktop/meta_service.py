"""UI actions for the metadata framework, mixed into ``Service``."""

from __future__ import annotations

import datetime as dt
import json
import tomllib
from pathlib import Path
from typing import Any

import polars as pl

from . import duties, lakehouse, receipts
from .meta import keys as keys_mod
from .meta import lineage, release
from .meta import mesh
from .meta import Engine, GateError, Layer, LocalStore, OneLakeStore, Project, impact, promote
from .meta.engine import RunResult
from .onelake import storage_options
from .meta.project import DEFAULT_RETENTION_DAYS, ENVIRONMENTS, validate
from .meta.promote import promote_checks
from .meta.store import LANDING_LAKEHOUSE, META_LAKEHOUSE, Store, ran_on
from .meta.templates import TEMPLATES, describe, entity_text, init_project


def _json_rows(df: pl.DataFrame, limit: int) -> list[dict[str, Any]]:
    return json.loads(df.head(limit).write_json())


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


class MetaMixin:
    root: Path

    # ---- which project is open

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    def _settings(self) -> dict[str, Any]:
        try:
            return json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _save_settings(self, **changes: Any) -> None:
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps(self._settings() | changes, indent=2), encoding="utf-8")

    def _source_options(self) -> dict[str, str]:
        """OneLake access for source tables outside the environment (adopted lakehouse tables)."""
        return storage_options(self.auth.storage_token())  # type: ignore[attr-defined]

    def _project(self) -> Project:
        path = self._settings().get("meta_project")
        if not path or not (Path(path) / "layers.toml").exists():
            raise LookupError("No metadata project is open. Create one or open its folder.")
        project = getattr(self, "_meta_project", None)
        if project is None or str(project.root) != path:
            project = Project(path)
            self._meta_project = project
        else:
            project.reload()
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
        except Exception:  # not signed in: the owner can be filled in later
            pass
        init_project(Path(path), preset, owner, sample)
        self._save_settings(meta_project=str(Path(path)))
        return self.meta_project()

    def meta_open(self, path: str) -> dict[str, Any]:
        if not (Path(path) / "layers.toml").exists():
            raise FileNotFoundError(f"{path} isn't a metadata project (it has no layers.toml)")
        self._save_settings(meta_project=str(Path(path)))
        return self.meta_project()

    # ---- environments and stores

    def _store(self, project: Project, env: str, create: bool = False) -> Store:
        if env not in ENVIRONMENTS:
            raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
        cfg = project.environments.get(env, {})
        if cfg.get("target", "onelake" if env != "dev" else "local") == "local":
            return LocalStore(project.local_data / env, env, landing=self._landing_dir(env))
        ws = cfg.get("workspace_id")
        if not ws:
            raise LookupError(f"Assign a workspace to {env.title()} first (Metadata > Environments and promote).")
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
                        space, name, "Managed by Fabric Desktop by KratosBI's metadata framework")["id"]
        # The organization's landing zone is the platform's, never created here.
        platform = self._platform_config().get(env, {})  # type: ignore[attr-defined]
        if platform.get("landing_id"):
            existing[LANDING_LAKEHOUSE] = platform["landing_id"]
            where[LANDING_LAKEHOUSE] = platform["workspace_id"]
        return OneLakeStore(env, ws, existing, lambda: self.auth.storage_token(),  # type: ignore[attr-defined]
                            self.root / "onelake-cache",
                            lakehouse_workspaces={n: w for n, w in where.items() if w != ws})

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
            raise LookupError(f"Pick a Keys workspace for {env.title()} first (Metadata > Surrogate keys).")
        fabric = self.fabric  # type: ignore[attr-defined]
        existing = {lh["displayName"]: lh["id"] for lh in fabric.list_lakehouses(ws)}
        if create and keys_mod.KEYS_LAKEHOUSE not in existing:
            existing[keys_mod.KEYS_LAKEHOUSE] = fabric.create_lakehouse(
                ws, keys_mod.KEYS_LAKEHOUSE, "Surrogate keys and source systems, managed by Fabric Desktop by KratosBI")["id"]
        return OneLakeStore(env, ws, existing, lambda: self.auth.storage_token(),  # type: ignore[attr-defined]
                            self.root / "onelake-cache")

    def _keys_in_platform(self, project: Project, env: str) -> bool:
        """One key system for everything: once an environment's platform is in Fabric, its keys come
        from SQL_Platform, unless the project already issues keys in LH_Keys (those move first, with
        their numbers, from Surrogate keys > Move keys)."""
        cfg = project.environments.get(env, {})
        if cfg.get("keys") in ("platform", "lakehouse"):
            return cfg["keys"] == "platform"
        if not self._platform_config().get(env, {}).get("functions_id"):  # type: ignore[attr-defined]
            return False
        local = cfg.get("target", "onelake" if env != "dev" else "local") == "local"
        has_lh_keys = (project.local_data / f"keys-{env}").exists() if local else bool(cfg.get("keys_workspace_id"))
        return not has_lh_keys

    def _uses_keys(self, project: Project, names: list[str] | None = None) -> bool:
        return any(keys_mod.entity_keys(e) for n, e in project.entities.items() if names is None or n in names)

    def _write_environments(self, project: Project, envs: dict[str, dict[str, Any]]) -> None:
        lines = ["# Each environment's workspace, and where its tables live: target = \"onelake\" (the workspace's",
                 "# lakehouses, LH_<Layer>, created when first needed) or \"local\" (offline, in .local).",
                 "# compute = \"local\" runs on this PC, \"fabric\" on the workspace's capacity.",
                 "# keys_workspace_id is the workspace that holds LH_Keys, the surrogate keys.", ""]
        lines += ["# workspaces: a workspace per designation (ingest, product, serve, sandbox); layers pick one",
                  "# with workspace = \"...\" in layers.toml. keys = \"platform\": surrogate keys are in SQL_Platform.",
                  "# require_commit = true: promote here only from files a pushed Git commit holds.", ""]
        for name in ENVIRONMENTS:
            lines.append(f"[{name}]")
            lines += [f"{k} = {_toml(v)}" for k, v in envs.get(name, {}).items()]
            lines.append("")
        (project.root / "environments.toml").write_text("\n".join(lines), encoding="utf-8")

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
            raise LookupError(f"Assign a workspace to {env.title()} first.")
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
            name, capacity_id, f"{env.title()} environment, managed by Fabric Desktop by KratosBI's metadata framework")
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
            log = store.meta("run_log").filter(pl.col("entity") == name).reverse()
            runs = _json_rows(log.drop("steps"), 10)
            df = store.read(project.layer(e.layer).lakehouse, e.schema, e.table) \
                if e.layer in project.layer_names() else None
            if df is not None:
                preview = {"columns": df.columns, "rows": json.loads(df.head(50).write_json()), "total": df.height}
        except Exception as exc:  # the config is still useful when the environment can't be read
            runs = [{"status": "unavailable", "message": str(exc)}]
        errors = [x for x in project.summary()["errors"] if x.startswith(name + ":")]
        keys_sql = None
        if keys_mod.entity_keys(e) and not errors and "entity" in e.spec.get("source", {}):
            keys_sql = keys_mod.merge_sql(project, e, project.environments.get(env, {}).get("keys_workspace_name", ""))
        return e.summary() | {"text": e.path.read_text(encoding="utf-8"), "impact": impact(project, name),
                              "runs": runs, "preview": preview, "errors": errors, "keys_sql": keys_sql}

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
        path = project.entities_dir / f"{new_name}.toml"
        if new_name != name:
            if path.exists():
                raise ValueError(f"Not saved: {new_name} already exists")
            downstream = impact(project, name) if name in project.entities else []
            if downstream:
                raise GateError("save", [f"can't rename {name}: {', '.join(downstream)} read it"])
            (project.entities_dir / f"{name}.toml").unlink(missing_ok=True)
        path.write_text(text, encoding="utf-8")
        return self.meta_entity(new_name)

    def meta_new_entity(self, template: str, name: str, source: str = "", keys: list[str] | None = None,
                        owner: str = "") -> dict[str, Any]:
        project = self._project()
        if template not in TEMPLATES:
            raise ValueError(f"template must be one of {', '.join(TEMPLATES)}")
        if name in project.entities:
            raise ValueError(f"{name} already exists")
        layer = name.split(".")[0]
        if layer not in project.layer_names():
            raise ValueError(f"{layer} is not a layer; use one of {', '.join(project.layer_names())}")
        sample = None
        if template in ("files", "landing") and source:
            try:
                store = self._store(project, "dev")
                where = LANDING_LAKEHOUSE if template == "landing" else project.layer(layer).lakehouse
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
                p.write_text(content, encoding="utf-8")
        (project.entities_dir / f"{name}.toml").write_text(text, encoding="utf-8")
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
        engine = Engine(project, self._store(project, env), source_options=self._source_options,
                        link_table=self._link_table(project, env))  # type: ignore[attr-defined]
        return engine.preflight(name, backfill)

    def _run_here(self, project: Project, env: str, names: list[str] | None = None, backfill: str | None = None,
                  cascade: bool = False) -> list[dict[str, Any]]:
        """Run on this PC, whatever the environment's compute (schedules on this PC use it)."""
        keys_store = self._keys_store(project, env, create=env == "dev") if self._uses_keys(project, names) else None
        store = self._store(project, env, create=env == "dev")
        engine = Engine(project, store, keys_store=keys_store, source_options=self._source_options,
                        link_table=self._link_table(project, env))  # type: ignore[attr-defined]
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
        run = store.meta("run_log").filter(pl.col("run_id") == run_id)
        if not run.height:
            raise KeyError(f"no run {run_id} in {env}")
        row = _json_rows(run, 1)[0]
        row["ran_on"] = ran_on(row)
        row["steps"] = json.loads(row.get("steps") or "[]")
        row["dq"] = _json_rows(store.meta("dq_results").filter(pl.col("run_id") == run_id), 100)
        return row

    def meta_preview(self, name: str, env: str = "dev", limit: int = 100) -> dict[str, Any]:
        project = self._project()
        e = project.entities[name]
        df = self._store(project, env).read(project.layer(e.layer).lakehouse, e.schema, e.table)
        if df is None:
            return {"columns": [], "rows": [], "total": 0}
        return {"columns": df.columns, "rows": json.loads(df.head(limit).write_json()), "total": df.height}

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
            errors.append(f"Pick a Keys workspace for {target.title()} first (Metadata > Surrogate keys)")
        errors += self._product_gate(project)
        breaking, notes = self._breaking_gate(project, target)
        errors += breaking
        git = release.status(project.root)
        unreleased, more = self._release_gate(project, target, git)
        errors += unreleased
        duty_errors, duty_notes = self._duties_gate(project, source, target)
        errors += duty_errors
        return {"source": source, "target": target, "version": project.version, "errors": errors, "tests": tests,
                "notes": notes + more + duty_notes, "git": git}

    def meta_promote(self, source: str, target: str) -> dict[str, Any]:
        project = self._project()
        src = self._store(project, source)
        errors = promote_checks(project, src, target, dt.date.today())
        errors += self._test_gate(project)[0]  # type: ignore[attr-defined]  # custom code is tested before it moves on
        errors += self._product_gate(project)
        breaking, notes = self._breaking_gate(project, target)
        errors += breaking
        git = release.status(project.root)
        unreleased, release_notes = self._release_gate(project, target, git)
        errors += unreleased
        errors += self._duties_gate(project, source, target, src)[0]
        if errors:
            raise GateError("promote", errors)
        target_store = self._store(project, target, create=True)
        uses_keys = bool(project.key_tables or project.source_systems)
        keys_store = self._keys_store(project, target, create=True) if uses_keys else None  # fails before promoting
        result = promote(project, src, target_store, dt.date.today(),
                         release=release.record(project, source, target, git))
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
        if project.products:
            try:  # so other teams' links find this environment's tables
                result["products"] = self.mesh_publish(target)["published"]  # type: ignore[attr-defined]
            except Exception as exc:  # the promote itself worked; publish again from Mesh > Data products
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
        try:
            out["verified"] = bool(receipt) and receipts.receipt_id(json.loads(receipt)) == row.get("receipt_id")
        except (ValueError, KeyError, TypeError):
            out["verified"] = False
        out["receipt"] = receipt  # the canonical record, for Copy record
        return {"env": env, "release": out, "problem": None}

    def _release_gate(self, project: Project, target: str, git: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Promoting files no pushed commit holds: blocked when the target environment sets
        ``require_commit = true`` in environments.toml, otherwise a note (decision D1)."""
        found = release.problems(git)
        if not found or target == "dev":
            return [], []
        if project.environments.get(target, {}).get("require_commit"):
            return [f"{target.title()} only takes released files: {p}" for p in found], []
        return [], [f"The release record can't name a pushed commit that holds these files: {'; '.join(found)}"]

    def _duties_gate(self, project: Project, source: str, target: str,
                     src: Any = None) -> tuple[list[str], list[str]]:
        """Before writing to Test or Prod: the person's workspace roles there, and with
        ``separate_promoter = true`` on the target, that they didn't promote this version to the
        source themselves (FD-130). Reads only; see duties.py."""
        cfg = project.environments.get(target, {})
        if target == "dev":
            return [], []
        errors: list[str] = []
        notes: list[str] = []
        if cfg.get("separate_promoter"):
            try:
                store = src or self._store(project, source)
                deps = store.meta("deployments")
                rows = [r for r in deps.iter_rows(named=True)
                        if r.get("env") == source and r.get("version") == project.version] if deps.height else []
            except Exception:  # noqa: BLE001 - promote_checks already says when the source can't be read
                rows = []
            problem = duties.separation_problem(source, target, project.version,
                                                rows[-1].get("by") if rows else None, duties.current_user())
            if problem:
                errors.append(problem)
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
        return mesh.breaking_changes(project, published, target)

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

    def meta_keys(self, env: str = "dev") -> dict[str, Any]:
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
                central = self._platform(env).call("list_key_tables")  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                problem = problem or str(exc)
        return {"env": env, "local": local, "path": str(project.local_data / f"keys-{env}") if local else None,
                "workspace_id": cfg.get("keys_workspace_id", ""), "workspace_name": cfg.get("keys_workspace_name", ""),
                "lakehouse": "SQL_Platform" if self._keys_in_platform(project, env) else keys_mod.KEYS_LAKEHOUSE,
                "platform": self._keys_in_platform(project, env), "moved_at": cfg.get("keys_moved_at", ""),
                "platform_available": bool(self._platform_config().get(env, {}).get("functions_id")),  # type: ignore[attr-defined]
                "key_tables": tables, "central_key_tables": central,
                "source_systems": summary["source_systems"], "system_types": list(keys_mod.SYSTEM_TYPES),
                "connection_fields": list(keys_mod.CONNECTION_FIELDS), "problem": problem,
                "errors": [x for x in summary["errors"] if x.startswith(("keys/", "source system"))]}

    def meta_new_key_table(self, name: str, description: str = "", env: str = "dev") -> dict[str, Any]:
        project = self._project()
        name = name.strip().lower()
        if name in project.key_tables:
            raise ValueError(f"key table {name} already exists")
        if self._keys_in_platform(project, env):
            # One key system: the project uses a key table the platform has, it doesn't make its own.
            central = {t["name"] for t in self._platform(env).call("list_key_tables")}  # type: ignore[attr-defined]
            if name not in central:
                raise GateError("save", [f"{name} isn't one of the platform's key tables. Request it in "
                                         "Platform > Business keys, or pick one from the list."])
        owner = ""
        try:
            owner = self.auth.signed_in_user() or ""  # type: ignore[attr-defined]
        except Exception:
            pass
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
                path.write_text(old, encoding="utf-8")
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
        src = project.entities[e.spec["source"]["entity"]]
        nb_name = f"nb_{src.layer}_to_{e.layer}_{e.table}"
        cells = keys_mod.template_cells(project, e, project.environments.get(env, {}).get("keys_workspace_name", ""))
        self.notebooks.save(nb_name, cells)  # type: ignore[attr-defined]
        return {"name": nb_name}
