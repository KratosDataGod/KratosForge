"""A project's place in the data mesh: its domain, its data products, its links to other teams.

    project.toml                     the project's name, domain, subdomain and team
    metadata/products/<name>.toml    a data product: which entities other teams may read (its ports)
    metadata/links/<alias>.toml      a link to another team's product port, read with [source] link

Layers can live in different workspaces by designation (``workspace = "ingest"`` in layers.toml;
the workspaces are in environments.toml). Entities can carry starter catalog text in [catalog],
which the platform's catalog can add to or override without a pull request.

All of it is optional: a project without these files works exactly as before.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .project import Project

PROJECT_FILE = "project.toml"
FORMAT = 2
DESIGNATIONS = ("ingest", "product", "serve", "sandbox")
DESIGNATION_LABELS = {"ingest": "Ingest", "product": "Product", "serve": "Serve", "sandbox": "Sandbox"}
ACCESS = ("public", "domain", "private")
STATUS = ("draft", "published", "deprecated")
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
ENVIRONMENTS = ("dev", "test", "prod")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,60}$")
_PROJECT = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


@dataclass
class Product:
    name: str
    spec: dict
    path: Path

    @property
    def access(self) -> str:
        return self.spec.get("access", "private")

    @property
    def status(self) -> str:
        return self.spec.get("status", "draft")

    @property
    def ports(self) -> list[dict[str, str]]:
        return [p for p in self.spec.get("port", []) if isinstance(p, dict)]

    def summary(self) -> dict[str, Any]:
        s = self.spec
        return {"name": self.name, "title": s.get("title") or self.name.replace("_", " ").title(),
                "description": s.get("description", ""), "owner": s.get("owner", ""), "access": self.access,
                "status": self.status, "version": str(s.get("version", 1)), "deprecated_on": s.get("deprecated_on", ""),
                "ports": [{"name": p.get("name", ""), "entity": p.get("entity", "")} for p in self.ports],
                "path": str(self.path)}


@dataclass
class Link:
    alias: str
    spec: dict
    path: Path

    @property
    def product(self) -> str:
        return str(self.spec.get("product", ""))

    @property
    def port(self) -> str:
        return str(self.spec.get("port", ""))

    def reads(self, env: str) -> str:
        """Which environment of the producer this environment reads (Dev often reads Test or Prod)."""
        return str(self.spec.get("reads", {}).get(env, env))

    def location(self, env: str) -> str:
        """A table address pinned for this environment, used instead of asking the platform."""
        return str(self.spec.get("location", {}).get(env, {}).get("table", ""))

    @property
    def columns(self) -> dict[str, str]:
        return dict(self.spec.get("columns", {}))

    def summary(self) -> dict[str, Any]:
        return {"alias": self.alias, "product": self.product, "port": self.port,
                "reads": {env: self.reads(env) for env in ENVIRONMENTS},
                "pinned": {env: self.location(env) for env in ENVIRONMENTS if self.location(env)},
                "columns": self.columns, "description": self.spec.get("description", ""), "path": str(self.path)}


def _load_dir(folder: Path, key: str) -> tuple[dict[str, tuple[dict, Path]], list[str]]:
    out: dict[str, tuple[dict, Path]] = {}
    errors: list[str] = []
    for p in sorted(folder.glob("*.toml")):
        try:
            spec = tomllib.loads(p.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            errors.append(f"{folder.name}/{p.name}: not valid TOML ({exc})")
            continue
        name = spec.get(key) or p.stem
        if name != p.stem:
            errors.append(f"{folder.name}/{p.name}: file name must match its {key} ({name})")
        out[name] = (spec, p)
    return out, errors


def load(root: Path) -> tuple[dict[str, Any], dict[str, Product], dict[str, Link], list[str]]:
    root = Path(root)
    errors: list[str] = []
    info: dict[str, Any] = {}
    pf = root / PROJECT_FILE
    if pf.exists():
        try:
            info = tomllib.loads(pf.read_text(encoding="utf-8")).get("project", {})
        except tomllib.TOMLDecodeError as exc:
            errors.append(f"{PROJECT_FILE}: not valid TOML ({exc})")
    products_raw, e1 = _load_dir(root / "metadata" / "products", "name")
    links_raw, e2 = _load_dir(root / "metadata" / "links", "alias")
    products = {n: Product(n, s, p) for n, (s, p) in products_raw.items()}
    links = {n: Link(n, s, p) for n, (s, p) in links_raw.items()}
    return info, products, links, errors + e1 + e2


def project_name(project: Project) -> str:
    """The project's name in the catalog: project.toml's name, else its folder's name."""
    name = str(project.info.get("name") or project.root.name).strip().lower().replace(" ", "-")
    return name or "project"


def identity(project: Project) -> dict[str, Any]:
    i = project.info
    return {"name": project_name(project), "domain": i.get("domain", ""), "subdomain": i.get("subdomain", ""),
            "team": i.get("team", ""), "description": i.get("description", ""),
            "contacts": list(i.get("contacts", [])), "upgraded": bool(i), "format": int(i.get("format", 1) or 1)}


def landing_readers(project: Project) -> dict[str, list[str]]:
    """Which landing zone sources the project reads, and the entities that read each:
    [source] landing = "sap/customer/*.csv" reads source sap."""
    out: dict[str, list[str]] = {}
    for e in project.entities.values():
        pattern = str(e.spec.get("source", {}).get("landing") or "").strip("/")
        if pattern:
            out.setdefault(pattern.split("/")[0], []).append(e.name)
    return {k: sorted(v) for k, v in sorted(out.items())}


def write_project_file(root: Path, fields: dict[str, Any]) -> Path:
    def q(v: Any) -> str:
        import json
        return json.dumps(v)

    lines = ["# This project's place in your organization: its domain and the team that owns it.",
             "# Domains come from your platform (Metadata > Mesh > Domains and workspaces).", "", "[project]"]
    for key in ("name", "domain", "subdomain", "team", "description"):
        if fields.get(key):
            lines.append(f"{key} = {q(str(fields[key]))}")
    if fields.get("contacts"):
        lines.append(f"contacts = {q([str(c) for c in fields['contacts']])}")
    lines.append(f"format = {FORMAT}")
    path = Path(root) / PROJECT_FILE
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_product(root: Path, p: dict[str, Any]) -> Path:
    import json
    name = str(p.get("name", "")).strip()
    if not _NAME.match(name):
        raise ValueError("A product name is lowercase letters, digits or _, like customer_360")
    lines = ["# A data product: the tables (ports) other teams may read, and the promise that comes with them.",
             f"name = {json.dumps(name)}"]
    for key in ("title", "description", "owner", "access", "status", "deprecated_on"):
        if p.get(key):
            lines.append(f"{key} = {json.dumps(str(p[key]))}")
    lines.append(f"version = {int(p.get('version') or 1)}")
    for port in p.get("ports", []):
        lines += ["", "[[port]]", f"name = {json.dumps(str(port.get('name', '')))}",
                  f"entity = {json.dumps(str(port.get('entity', '')))}"]
    path = Path(root) / "metadata" / "products" / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_link(root: Path, l: dict[str, Any]) -> Path:
    import json
    alias = str(l.get("alias", "")).strip()
    if not _NAME.match(alias):
        raise ValueError("A link's name is lowercase letters, digits or _, like sales_customer")
    lines = ["# A link to another team's data product. Entities read it with [source] link = \"<alias>\".",
             f"alias = {json.dumps(alias)}", f"product = {json.dumps(str(l.get('product', '')))}",
             f"port = {json.dumps(str(l.get('port', '')))}"]
    if l.get("description"):
        lines.append(f"description = {json.dumps(str(l['description']))}")
    reads = {env: str(v) for env, v in (l.get("reads") or {}).items() if env in ENVIRONMENTS and v}
    if reads:
        lines += ["", "# Which environment of the product each of your environments reads.", "[reads]"]
        lines += [f"{env} = {json.dumps(v)}" for env, v in reads.items()]
    if l.get("columns"):
        lines += ["", "# The columns you rely on: the save gate checks entities against these.", "[columns]"]
        lines += [f"{c} = {json.dumps(str(t))}" for c, t in l["columns"].items()]
    for env, table in (l.get("pinned") or {}).items():
        if env in ENVIRONMENTS and table:
            lines += ["", f"[location.{env}]", f"table = {json.dumps(str(table))}"]
    path = Path(root) / "metadata" / "links" / f"{alias}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def entity_catalog(e: Any) -> dict[str, Any]:
    """The starter catalog text kept with the entity's code: [catalog] and [catalog.columns.<name>]."""
    c = e.spec.get("catalog", {}) if isinstance(e.spec.get("catalog"), dict) else {}
    cols = c.get("columns", {}) if isinstance(c.get("columns"), dict) else {}
    return {"description": c.get("description", ""), "tags": list(c.get("tags", [])),
            "classification": c.get("classification", ""), "terms": list(c.get("terms", [])),
            "steward": c.get("steward", ""), "columns": {k: dict(v) for k, v in cols.items() if isinstance(v, dict)}}


def products_of(project: Project, entity: str) -> list[dict[str, str]]:
    return [{"product": p.name, "port": port.get("name", "")} for p in project.products.values()
            for port in p.ports if port.get("entity") == entity]


def validate(project: Project) -> list[str]:
    """The save gate's mesh rules: product, link, designation and catalog files make sense."""
    errors = list(project.mesh_errors)
    info = project.info
    if info:
        name = str(info.get("name") or "")
        if name and not _PROJECT.match(name):
            errors.append(f"{PROJECT_FILE}: name {name!r} must be lowercase letters, digits, -, _ or .")
        if info.get("subdomain") and not info.get("domain"):
            errors.append(f"{PROJECT_FILE}: a subdomain needs its domain")
    for l in project.layers:
        if l.workspace and l.workspace not in DESIGNATIONS:
            errors.append(f"layer {l.name}: workspace must be one of {', '.join(DESIGNATIONS)}")
    for env in ENVIRONMENTS:
        for d in (project.environments.get(env, {}).get("workspaces") or {}):
            if d not in DESIGNATIONS:
                errors.append(f"environments.toml: {env} has an unknown workspace designation {d}")
    for p in project.products.values():
        where = f"products/{p.name}"
        if not _NAME.match(p.name):
            errors.append(f"{where}: name must be lowercase letters, digits or _")
        if p.access not in ACCESS:
            errors.append(f"{where}: access must be one of {', '.join(ACCESS)}")
        if p.status not in STATUS:
            errors.append(f"{where}: status must be one of {', '.join(STATUS)}")
        if not p.ports:
            errors.append(f"{where}: a product needs at least one [[port]]")
        seen = set()
        for port in p.ports:
            pn, ent = port.get("name", ""), port.get("entity", "")
            if not _NAME.match(str(pn)):
                errors.append(f"{where}: port name {pn!r} must be lowercase letters, digits or _")
            if pn in seen:
                errors.append(f"{where}: port {pn} is listed twice")
            seen.add(pn)
            if ent not in project.entities:
                errors.append(f"{where}: port {pn} names {ent or '?'}, which is not a managed entity")
        if p.status == "deprecated" and not p.spec.get("deprecated_on"):
            errors.append(f"{where}: a deprecated product needs deprecated_on, the date it stops")
    for l in project.links.values():
        where = f"links/{l.alias}"
        if not _NAME.match(l.alias):
            errors.append(f"{where}: name must be lowercase letters, digits or _")
        if "/" not in l.product or not l.port:
            errors.append(f"{where}: product is \"<project>/<product>\" and port names one of its ports")
        for env, v in l.spec.get("reads", {}).items():
            if env not in ENVIRONMENTS or v not in ENVIRONMENTS:
                errors.append(f"{where}: reads maps your environment to the product's (dev, test or prod)")
        for env, loc in l.spec.get("location", {}).items():
            table = str((loc or {}).get("table", ""))
            if env not in ENVIRONMENTS or not table.startswith(("abfss://", "https://")):
                errors.append(f"{where}: location.{env} table must be a OneLake address (abfss://...)")
    for e in project.entities.values():
        link = e.spec.get("source", {}).get("link")
        if link is not None and link not in project.links:
            errors.append(f"{e.name}: reads link {link}, which isn't in metadata/links")
        cat = e.spec.get("catalog")
        if cat is None:
            continue
        if not isinstance(cat, dict):
            errors.append(f"{e.name}: catalog must be a [catalog] table")
            continue
        if cat.get("classification") and cat["classification"] not in CLASSIFICATIONS:
            errors.append(f"{e.name}: classification must be one of {', '.join(CLASSIFICATIONS)}")
        for col, meta in (cat.get("columns") or {}).items():
            if col not in e.columns and col not in e.derived and col not in e.hook_adds and col not in e.mode_columns():
                errors.append(f"{e.name}: catalog describes column {col}, which the table doesn't have")
            if isinstance(meta, dict) and meta.get("classification") and meta["classification"] not in CLASSIFICATIONS:
                errors.append(f"{e.name}: column {col} classification must be one of {', '.join(CLASSIFICATIONS)}")
    return errors


def merged_catalog(project: Project, e: Any, saved: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What a reader sees: the catalog's saved text where there is some, else the starter text in Git."""
    base = entity_catalog(e)
    key = f"{project_name(project)}:{e.name}"
    top = saved.get(key, {})
    out = {f: (top.get(f) or base[f]) for f in ("description", "tags", "classification", "terms", "steward")}
    out["certified"] = top.get("certified", "")
    columns = list(dict.fromkeys(list(e.columns) + e.derived + e.hook_adds + sorted(e.mode_columns())))
    out["columns"] = []
    for c in columns:
        b = base["columns"].get(c, {})
        s = saved.get(f"{key}#{c}", {})
        out["columns"].append({"name": c, "type": e.columns.get(c, ""),
                               "description": s.get("description") or b.get("description", ""),
                               "classification": s.get("classification") or b.get("classification", ""),
                               "terms": s.get("terms") or list(b.get("terms", []))})
    out["target"] = key
    return out


def publish_problems(project: Project, saved: dict[str, dict[str, Any]]) -> list[str]:
    """Promote's rule for products other teams read: every port documented and owned."""
    errors = []
    for p in project.products.values():
        if p.status != "published" or p.access == "private":
            continue
        if not p.spec.get("description"):
            errors.append(f"products/{p.name}: a {p.access} product needs a description")
        if not p.spec.get("owner"):
            errors.append(f"products/{p.name}: a {p.access} product needs an owner")
        for port in p.ports:
            e = project.entities.get(port.get("entity", ""))
            if e is None:
                continue
            cat = merged_catalog(project, e, saved)
            if not cat["description"]:
                errors.append(f"{e.name}: port {p.name}.{port.get('name')} needs a table description")
            missing = [c["name"] for c in cat["columns"] if not c["description"]]
            if missing:
                errors.append(f"{e.name}: port {p.name}.{port.get('name')} needs a description on "
                              f"{len(missing)} column{'s' if len(missing) != 1 else ''} ({', '.join(missing[:5])})")
    return errors
