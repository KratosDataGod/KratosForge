"""The platform and the data mesh, as the desktop UI calls them.

Platform: which Platform workspace each environment uses (from IT's policy.json, else this
PC's settings), creating or joining one, and moving keys into SQL_Platform. Without a
platform in Fabric, everything runs against a SQLite platform on this PC, so a single
engineer can use domains, products and the catalog before the central team exists.

Mesh: the open project's domain, data products, links and catalog text.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from .. import app_settings
from ..auth import EXECUTE_SCOPES
from ..meta import keys as keys_mod
from ..meta import mesh
from ..meta.project import ENVIRONMENTS, GateError
from ..onelake import TableRef
from . import setup as setup_mod
from .client import FabricPlatform, LocalPlatform, PlatformKeys
from .migrate import migrate

CATALOG_ENV = "prod"  # domains, glossary and catalog text don't depend on the environment: they live in Prod's


class PlatformMixin:
    root: Path

    # ---- which platform

    def _platform_config(self) -> dict[str, dict[str, Any]]:
        policy = app_settings.value("platform") or {}
        mine = self._settings().get("platform", {})  # type: ignore[attr-defined]
        return {env: dict(policy.get(env) or mine.get(env) or {}) for env in ENVIRONMENTS}

    def _platform_source(self) -> str:
        return "policy" if app_settings.load()["platform"]["source"] == "policy" else "user"

    def _user(self) -> str:
        try:
            return self.auth.signed_in_user() or ""  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - offline: changes are recorded without a name
            return ""

    def _platform(self, env: str) -> LocalPlatform | FabricPlatform:
        cfg = self._platform_config().get(env, {})
        if cfg.get("functions_id"):
            return FabricPlatform(lambda: self.auth._token(EXECUTE_SCOPES),  # type: ignore[attr-defined]
                                  cfg["workspace_id"], cfg["functions_id"])
        cached = getattr(self, "_local_platforms", None)
        if cached is None:
            cached = self._local_platforms = {}
        if env not in cached:
            cached[env] = LocalPlatform(self.root / "platform" / f"platform-{env}.sqlite", self._user)
        return cached[env]

    def _catalog(self) -> LocalPlatform | FabricPlatform:
        return self._platform(CATALOG_ENV)

    def _platform_keys(self, env: str) -> PlatformKeys:
        return PlatformKeys(self._platform(env), env)

    def platform_status(self) -> dict[str, Any]:
        cfg = self._platform_config()
        return {"source": self._platform_source(), "default_names": {e: setup_mod.default_name(e) for e in ENVIRONMENTS},
                "environments": {env: {"kind": "fabric" if c.get("functions_id") else "local", **c}
                                 for env, c in cfg.items()},
                "local_path": str(self.root / "platform")}

    def _save_platform(self, env: str, cfg: dict[str, Any]) -> None:
        if self._platform_source() == "policy":
            raise PermissionError("Your organization sets the platform, so it can't be changed here")
        mine = dict(self._settings().get("platform", {}))  # type: ignore[attr-defined]
        if cfg:
            mine[env] = cfg
        else:
            mine.pop(env, None)
        self._save_settings(platform=mine)  # type: ignore[attr-defined]

    def platform_setup(self, env: str, name: str = "", capacity_id: str = "") -> dict[str, Any]:
        """Create (or finish creating) the Platform workspace of one environment."""
        current = self._platform_config().get(env, {})
        out = setup_mod.setup(self.fabric, lambda: self.auth._token(EXECUTE_SCOPES),  # type: ignore[attr-defined]
                              env, name, capacity_id, current)
        self._save_platform(env, out["config"])
        if out["ready"] and env == CATALOG_ENV:
            self._register_open_project()
        return out | {"status": self.platform_status()}

    def platform_join(self, env: str, workspace_id: str) -> dict[str, Any]:
        cfg = setup_mod.join(self.fabric, env, workspace_id)  # type: ignore[attr-defined]
        self._save_platform(env, cfg)
        return self.platform_status()

    def platform_forget(self, env: str) -> dict[str, Any]:
        """Stop using a Platform workspace on this PC. Nothing in Fabric is deleted."""
        self._save_platform(env, {})
        return self.platform_status()

    # ---- domains and glossary (the central team's lists)

    def platform_domains(self) -> list[dict[str, Any]]:
        return self._catalog().call("list_domains")

    def platform_save_domain(self, domain: dict[str, Any]) -> list[dict[str, Any]]:
        self._catalog().call("save_domain", domain=domain)
        return self.platform_domains()

    def platform_delete_domain(self, id: str) -> list[dict[str, Any]]:
        self._catalog().call("delete_domain", did=id)
        return self.platform_domains()

    def platform_glossary(self) -> list[dict[str, Any]]:
        return self._catalog().call("list_glossary")

    def platform_save_term(self, term: dict[str, Any]) -> list[dict[str, Any]]:
        self._catalog().call("save_term", term=term)
        return self.platform_glossary()

    def platform_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._catalog().call("audit", limit=limit)

    # ---- the open project in the mesh

    def _register_open_project(self) -> None:
        try:
            project = self._project()  # type: ignore[attr-defined]
        except LookupError:
            return
        if project.info:
            i = mesh.identity(project)
            self._catalog().call("register_project", project=i)

    def mesh_overview(self) -> dict[str, Any]:
        """Everything the Mesh tabs show: this project, the domains, products here and published elsewhere."""
        project = self._project()  # type: ignore[attr-defined]
        summary = project.summary()
        catalog = self._catalog()
        problems = []
        try:
            domains = catalog.call("list_domains")
            published = catalog.call("list_products")
            projects = catalog.call("list_projects")
        except Exception as exc:  # noqa: BLE001 - the project's own files still show
            domains, published, projects = [], [], []
            problems.append(f"The platform's catalog couldn't be read: {exc}")
        me = mesh.identity(project)
        return {"project": me, "products": summary["products"], "links": summary["links"],
                "layers": summary["layers"], "environments": summary["environments"],
                "designations": summary["designations"], "domains": domains, "published": published,
                "projects": projects, "platform": self.platform_status(), "problems": problems,
                "errors": [e for e in summary["errors"] if e.startswith(("products/", "links/", mesh.PROJECT_FILE, "layer "))]}

    def mesh_set_project(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Write project.toml (the Upgrade), then record the project and its domain in the catalog."""
        project = self._project()  # type: ignore[attr-defined]
        fields = {**mesh.identity(project), **{k: v for k, v in fields.items() if v is not None}}
        fields["name"] = str(fields.get("name") or project.root.name).strip().lower().replace(" ", "-")
        catalog = self._catalog()
        if fields.get("domain"):
            known = {d["id"]: d for d in catalog.call("list_domains")}
            if fields["domain"] not in known:
                raise GateError("save", [f"domain {fields['domain']} isn't one of your organization's domains"])
            if fields.get("subdomain") and known.get(fields["subdomain"], {}).get("parent_id") != fields["domain"]:
                raise GateError("save", [f"{fields['subdomain']} isn't a subdomain of {fields['domain']}"])
        mesh.write_project_file(project.root, fields)
        catalog.call("register_project", project=fields)
        return self.mesh_overview()

    def mesh_save_product(self, product: dict[str, Any], original: str = "") -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        path = project.root / "metadata" / "products" / f"{product.get('name', '')}.toml"
        old = path.read_text(encoding="utf-8") if path.exists() else None
        new_path = mesh.write_product(project.root, product)
        project.reload()
        problems = [e for e in mesh.validate(project) if e.startswith(f"products/{product.get('name')}")]
        if problems:
            if old is None:
                new_path.unlink()
            else:
                new_path.write_text(old, encoding="utf-8")
            raise GateError("save", problems)
        if original and original != product.get("name"):
            (project.root / "metadata" / "products" / f"{original}.toml").unlink(missing_ok=True)
        return self.mesh_overview()

    def mesh_delete_product(self, name: str) -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        (project.root / "metadata" / "products" / f"{name}.toml").unlink(missing_ok=True)
        return self.mesh_overview()

    def _port_location(self, project: Any, env: str, entity: str) -> dict[str, Any]:
        """Where an entity's table is in an environment, as the catalog records a product port."""
        e = project.entities[entity]
        store = self._store(project, env)  # type: ignore[attr-defined]
        lh = project.layer(e.layer).lakehouse
        ws = getattr(store, "lakehouse_workspaces", {}).get(lh) or getattr(store, "workspace_id", "")
        cfg = project.environments.get(env, {})
        return {"entity": entity, "workspace_id": ws, "workspace_name": cfg.get("workspace_name", ""),
                "lakehouse_id": getattr(store, "lakehouse_ids", {}).get(lh, ""), "lakehouse_name": lh,
                "schema": e.schema, "table": e.table, "columns": e.columns}

    def mesh_publish(self, env: str) -> dict[str, Any]:
        """Record every published product's ports, with where they live in ``env``, in the catalog.
        Promote does this; it can be run by hand after changing a product."""
        project = self._project()  # type: ignore[attr-defined]
        saved = self._catalog().call("enrichment", project=mesh.project_name(project))
        problems = mesh.publish_problems(project, saved)
        if problems:
            raise GateError("promote", problems)
        name = mesh.project_name(project)
        done = []
        for p in project.products.values():
            if p.status == "draft":
                continue
            ports = [{"port": port["name"], **self._port_location(project, env, port["entity"])} for port in p.ports]
            info = p.summary() | {"project": name, "domain": project.info.get("domain", "")}
            self._catalog().call("register_product", product=info, ports=ports, env=env)
            done.append(p.name)
        return {"env": env, "published": done}

    def mesh_save_link(self, link: dict[str, Any]) -> dict[str, Any]:
        """Write metadata/links/<alias>.toml and tell the producer's catalog entry who reads it."""
        project = self._project()  # type: ignore[attr-defined]
        reads = {env: (link.get("reads") or {}).get(env, env) for env in ENVIRONMENTS}
        if not link.get("pinned"):
            self._catalog().call("register_link", consumer=mesh.project_name(project), alias=link.get("alias", ""),
                                 product_id=link.get("product", ""), port=link.get("port", ""), reads=reads)
        mesh.write_link(project.root, link | {"reads": reads})
        project.reload()
        problems = [e for e in mesh.validate(project) if e.startswith(f"links/{link.get('alias')}")]
        if problems:
            raise GateError("save", problems)
        return self.mesh_overview()

    def mesh_delete_link(self, alias: str) -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        users = [e.name for e in project.entities.values() if e.spec.get("source", {}).get("link") == alias]
        if users:
            raise GateError("save", [f"can't delete link {alias}: {', '.join(users)} read it"])
        (project.root / "metadata" / "links" / f"{alias}.toml").unlink(missing_ok=True)
        return self.mesh_overview()

    def _link_table(self, project: Any, env: str):
        """For the engine: a link's alias to the OneLake address it reads in ``env``."""
        def resolve(alias: str) -> str:
            link = project.links.get(alias)
            if link is None:
                raise LookupError(f"link {alias} isn't in metadata/links")
            if link.location(env):
                return link.location(env)
            port = self._catalog().call("resolve_port", product_id=link.product, port=link.port, env=link.reads(env))
            if not port or not port.get("lakehouse_id"):
                raise LookupError(f"{link.product} hasn't published port {link.port} in {link.reads(env)} yet. "
                                  f"Its owner publishes it when they promote to {link.reads(env)}.")
            return TableRef(port["workspace_id"], port["lakehouse_id"], port["schema_name"] or "dbo",
                            port["table_name"]).uri
        return resolve

    def mesh_resolve_links(self, env: str = "dev") -> list[dict[str, Any]]:
        project = self._project()  # type: ignore[attr-defined]
        resolve = self._link_table(project, env)
        out = []
        for link in project.links.values():
            try:
                out.append({"alias": link.alias, "table": resolve(link.alias), "ok": True})
            except Exception as exc:  # noqa: BLE001
                out.append({"alias": link.alias, "ok": False, "message": str(exc)})
        return out

    # ---- catalog text (enrichment)

    def mesh_about(self, name: str) -> dict[str, Any]:
        """An entity's catalog text: what the catalog saved, else the starter text in its file."""
        project = self._project()  # type: ignore[attr-defined]
        e = project.entities[name]
        problem = None
        try:
            saved = self._catalog().call("enrichment", project=mesh.project_name(project))
            glossary = [t["term"] for t in self._catalog().call("list_glossary")]
        except Exception as exc:  # noqa: BLE001
            saved, glossary, problem = {}, [], str(exc)
        return mesh.merged_catalog(project, e, saved) | {
            "entity": name, "products": mesh.products_of(project, name), "owner": e.spec.get("owner", ""),
            "classifications": list(mesh.CLASSIFICATIONS), "glossary": glossary, "problem": problem,
            "publish_problems": [x for x in mesh.publish_problems(project, saved) if x.startswith(name + ":")]}

    def mesh_save_about(self, name: str, fields: dict[str, Any], column: str = "") -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        if name not in project.entities:
            raise LookupError(f"{name} isn't an entity in this project")
        target = f"{mesh.project_name(project)}:{name}" + (f"#{column}" if column else "")
        allowed = {"description", "tags", "classification", "terms", "steward", "certified"}
        self._catalog().call("save_enrichment", target=target, fields={k: v for k, v in fields.items() if k in allowed})
        return self.mesh_about(name)

    # ---- workspaces by designation

    def meta_set_designation(self, env: str, designation: str, workspace_id: str = "",
                             workspace_name: str = "") -> dict[str, Any]:
        """Point a workspace designation (ingest, product, ...) of an environment at a workspace.
        An empty workspace_id goes back to the environment's own workspace."""
        if env not in ENVIRONMENTS:
            raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
        if designation not in mesh.DESIGNATIONS:
            raise ValueError(f"designation must be one of {', '.join(mesh.DESIGNATIONS)}")
        project = self._project()  # type: ignore[attr-defined]
        envs = {k: dict(v) for k, v in project.environments.items()}
        spaces = dict(envs.get(env, {}).get("workspaces") or {})
        if workspace_id:
            spaces[designation] = {"id": workspace_id, "name": workspace_name}
        else:
            spaces.pop(designation, None)
        envs.setdefault(env, {})["workspaces"] = spaces
        if not spaces:
            envs[env].pop("workspaces")
        self._write_environments(project, envs)  # type: ignore[attr-defined]
        return self.mesh_overview()

    def meta_create_designation_workspace(self, env: str, designation: str, capacity_id: str = "") -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        i = mesh.identity(project)
        domain = (i["subdomain"] or i["domain"] or i["name"]).replace("-", " ").title()
        name = f"{domain} {mesh.DESIGNATION_LABELS[designation]} - {env.title()}"
        ws = self.fabric.create_workspace(name, capacity_id,  # type: ignore[attr-defined]
                                          f"{mesh.DESIGNATION_LABELS[designation]} workspace, managed by Fabric Desktop")
        return self.meta_set_designation(env, designation, ws["id"], ws.get("displayName") or name)

    # ---- keys into SQL_Platform

    def meta_keys_to_platform(self, env: str) -> dict[str, Any]:
        """Copy this environment's keys from LH_Keys into the platform with the same numbers, check
        every row, and switch the environment to the platform's keys only if all of them match."""
        project = self._project()  # type: ignore[attr-defined]
        cfg = project.environments.get(env, {})
        if cfg.get("keys") == "platform":
            return {"env": env, "ok": True, "already": True, "tables": []}
        lakehouse_keys = self._keys_store(project, env)  # type: ignore[attr-defined]
        report = migrate(project, lakehouse_keys, self._platform_keys(env))
        if report["ok"]:
            envs = {k: dict(v) for k, v in project.environments.items()}
            envs[env]["keys"] = "platform"
            envs[env]["keys_moved_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            self._write_environments(project, envs)  # type: ignore[attr-defined]
        return {"env": env, **report}

    def meta_keys_to_lakehouse(self, env: str) -> dict[str, Any]:
        """Go back to LH_Keys. Refused while the platform holds keys LH_Keys doesn't have: going back
        then would hand out those numbers again to other business keys."""
        project = self._project()  # type: ignore[attr-defined]
        if project.environments.get(env, {}).get("keys") == "platform":
            platform = self._platform_keys(env)
            envs_now = {k: dict(v) for k, v in project.environments.items()}
            envs_now[env].pop("keys", None)
            project.environments = envs_now
            try:
                lakehouse_keys = self._keys_store(project, env)  # type: ignore[attr-defined]
            finally:
                project.reload()
            newer = []
            for kt in project.key_tables.values():
                have = keys_mod.key_stats(lakehouse_keys, kt)
                top = platform.stats(kt.name)
                if top.get("next", 1) > have.get("next", 1):
                    newer.append(f"{kt.name} (keys {have['next']} to {top['next'] - 1})")
            if newer:
                raise GateError("save", [f"The platform issued keys after the move that LH_Keys doesn't have: "
                                         f"{', '.join(newer)}. Going back would reuse those numbers, so keys stay "
                                         "in the platform."])
        envs = {k: dict(v) for k, v in project.environments.items()}
        envs.get(env, {}).pop("keys", None)
        self._write_environments(project, envs)  # type: ignore[attr-defined]
        return {"env": env, "keys": "lakehouse"}

