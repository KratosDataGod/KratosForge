"""A project's place in the data mesh: its domain, its data products, its links to other teams.

    project.toml                     the project's name, domain, subdomain and team
    metadata/products/<name>.toml    a data product: which entities other teams may read (its ports)
    metadata/links/<alias>.toml      a link to another team's product port, read with [source] link, with the
                                     product versions it accepts ("2", "2-3" or "2+")

Layers can live in different workspaces by designation (``workspace = "ingest"`` in layers.toml;
the workspaces are in environments.toml). Entities can carry starter catalog text in [catalog],
which the platform's catalog can add to or override without a pull request.

All of it is optional: a project without these files works exactly as before.
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..jsonfile import write_text
from ..redact import one_line

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
_ACCEPTS = re.compile(r"^(\d+)(?:\s*-\s*(\d+)|(\+))?$")


def parse_accepts(value: Any) -> tuple[int, int | None]:
    """A link's ``accepts``: "2" (that version), "2-3" (a range) or "2+" (that version or later), as
    (lowest, highest), highest None for "or later". ValueError, in words a person can act on, otherwise."""
    text = str(value).strip()
    m = _ACCEPTS.match(text)
    if not m or isinstance(value, bool):
        raise ValueError(f"accepts is a version (\"2\"), a range (\"2-3\") or a version and later (\"2+\"), not {text!r}")
    low = int(m.group(1))
    high = None if m.group(3) else int(m.group(2) or low)
    if high is not None and high < low:
        raise ValueError(f"accepts {text!r} runs backwards: write the lower version first, like \"{high}-{low}\"")
    return low, high


def accepts_version(accepts: Any, version: Any) -> bool:
    """Whether a link that accepts ``accepts`` can read product ``version``. A link without accepts
    reads any version, as links did before versions were declared. A version that isn't a whole
    number can't be checked against a range, so it isn't accepted."""
    if accepts in (None, ""):
        return True
    low, high = parse_accepts(accepts)
    try:
        v = int(str(version).strip())
    except ValueError:
        return False
    return low <= v and (high is None or v <= high)


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
                "description": s.get("description", ""), "owner": s.get("owner", ""), "steward": s.get("steward", ""),
                "access": self.access,
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

    @property
    def accepts(self) -> str:
        """The product versions this link can read ("2", "2-3", "2+"); "" for any, as before versions."""
        value = self.spec.get("accepts")
        return "" if value is None else str(value).strip()

    def summary(self) -> dict[str, Any]:
        return {"alias": self.alias, "product": self.product, "port": self.port, "accepts": self.accepts,
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
            errors.append(f"{folder.name}/{p.name}: file name must match its {key} ({one_line(name, 80)})")
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
             "# Domains come from your platform (Data mesh > Domains).", "", "[project]"]
    for key in ("name", "domain", "subdomain", "team", "description"):
        if fields.get(key):
            lines.append(f"{key} = {q(str(fields[key]))}")
    if fields.get("contacts"):
        lines.append(f"contacts = {q([str(c) for c in fields['contacts']])}")
    lines.append(f"format = {FORMAT}")
    path = Path(root) / PROJECT_FILE
    write_text(path, "\n".join(lines) + "\n")
    return path


def write_product(root: Path, p: dict[str, Any]) -> Path:
    import json
    name = str(p.get("name", "")).strip()
    if not _NAME.match(name):
        raise ValueError("A product name is lowercase letters, digits or _, like customer_360")
    lines = ["# A data product: the tables (ports) other teams may read, and the promise that comes with them.",
             f"name = {json.dumps(name)}"]
    for key in ("title", "description", "owner", "steward", "access", "status", "deprecated_on"):
        if p.get(key):
            lines.append(f"{key} = {json.dumps(str(p[key]))}")
    lines.append(f"version = {int(p.get('version') or 1)}")
    for port in p.get("ports", []):
        lines += ["", "[[port]]", f"name = {json.dumps(str(port.get('name', '')))}",
                  f"entity = {json.dumps(str(port.get('entity', '')))}"]
    path = Path(root) / "metadata" / "products" / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, "\n".join(lines) + "\n")
    return path


def write_link(root: Path, l: dict[str, Any]) -> Path:
    import json
    alias = str(l.get("alias", "")).strip()
    if not _NAME.match(alias):
        raise ValueError("A link's name is lowercase letters, digits or _, like sales_customer")
    lines = ["# A link to another team's data product. Entities read it with [source] link = \"<alias>\".",
             f"alias = {json.dumps(alias)}", f"product = {json.dumps(str(l.get('product', '')))}",
             f"port = {json.dumps(str(l.get('port', '')))}"]
    if str(l.get("accepts") or "").strip():
        lines.append(f"accepts = {json.dumps(str(l['accepts']).strip())}  # the product versions this link can read")
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
    write_text(path, "\n".join(lines) + "\n")
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
    for link in project.links.values():
        where = f"links/{link.alias}"
        if not _NAME.match(link.alias):
            errors.append(f"{where}: name must be lowercase letters, digits or _")
        if "/" not in link.product or not link.port:
            errors.append(f"{where}: product is \"<project>/<product>\" and port names one of its ports")
        if link.accepts:
            try:
                parse_accepts(link.spec.get("accepts"))
            except ValueError as exc:
                errors.append(f"{where}: {exc}")
        for env, v in link.spec.get("reads", {}).items():
            if env not in ENVIRONMENTS or v not in ENVIRONMENTS:
                errors.append(f"{where}: reads maps your environment to the product's (dev, test or prod)")
        for env, loc in link.spec.get("location", {}).items():
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


# Type changes every reader can still read without a change on their side.
WIDENING = {("int16", "int32"), ("int16", "int64"), ("int32", "int64"), ("float32", "float64"), ("int32", "float64")}


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def utc_today() -> dt.date:
    """Today in UTC, the day the catalog (central/core.py) judges deprecation dates by, so this PC and
    the platform agree near midnight."""
    return dt.datetime.now(dt.timezone.utc).date()


def _stops(product: dict[str, Any]) -> dt.date | None:
    """When a catalog product deprecated with a date stops; None when it isn't, or the date can't be read."""
    if product.get("status") != "deprecated":
        return None
    try:
        return dt.date.fromisoformat(str(product.get("deprecated_on") or "").strip()[:10])
    except ValueError:
        return None


def _shut_out(link: dict[str, Any], version: str) -> bool:
    """Whether a catalog link's accepts leaves ``version`` out. Accepts the catalog holds but this app
    can't read is the consumer's to fix, so it counts as leaving it out."""
    try:
        return not accepts_version(link.get("accepts"), version)
    except ValueError:
        return True


def breaking_changes(project: Project, published: list[dict[str, Any]], env: str,
                     today: dt.date | None = None) -> tuple[list[str], list[str]]:
    """Promote's rule for products other teams read: a port may not drop or change the type of a
    column it published in ``env`` while another project reads that environment, unless the
    product's ``version`` goes up. ``published`` is the catalog's list_products.

    A reader that stored the columns it relies on (its link's [columns]) is only broken by a change
    to one of those. When the version goes up, a reader whose ``accepts`` leaves the new version out
    still blocks, unless the product was deprecated with a date that has passed: its readers were told.

    Returns (errors, notes): an error blocks the promote; a note says what a new version breaks."""
    me = project_name(project)
    today = today or utc_today()
    errors, notes = [], []
    by_id = {p["id"]: p for p in published}
    for p in project.products.values():
        if p.status == "draft":
            continue
        current = by_id.get(f"{me}/{p.name}")
        if current is None:
            continue  # never published: nobody can read it yet
        links = [c for c in current.get("consumers", [])
                 if env in (c.get("reads") or {}).values() and c["consumer_project"] != me]
        if not links:
            continue
        version = str(p.spec.get("version", 1))
        raised = version != str(current.get("version") or "1")
        stops = _stops(current)
        told = stops is not None and stops <= today
        for port in p.ports:
            e = project.entities.get(port.get("entity", ""))
            was = next((x for x in current.get("ports", []) if x["port"] == port.get("name") and x["env"] == env), None)
            if e is None or was is None:
                continue
            old, new = dict(was.get("columns") or {}), e.columns
            changed = {c: f"drops {c}" for c in old if c not in new}
            changed |= {c: f"changes {c} from {old[c]} to {new[c]}" for c in old
                        if c in new and new[c] != old[c] and (old[c], new[c]) not in WIDENING}
            if not changed:
                continue
            on_port = [c for c in links if c.get("port") in (None, "", port.get("name"))]
            # A reader that listed its columns relies on those alone; one that didn't relies on every column.
            hit = [c for c in on_port if not c.get("columns") or set(c["columns"]) & set(changed)]
            readers = sorted({c["consumer_project"] for c in hit})
            if not readers:
                if on_port:
                    spared = sorted({c["consumer_project"] for c in on_port})
                    notes.append(f"products/{p.name}: port {port.get('name')} {', '.join(changed.values())}, which "
                                 f"none of the {len(spared)} other {_plural(len(spared), 'project', 'projects')} "
                                 f"reading it in {env} rel{_plural(len(spared), 'ies', 'y')} on ({', '.join(spared)})")
                continue
            what = (f"products/{p.name}: port {port.get('name')} {', '.join(changed.values())}, and "
                    f"{len(readers)} other {_plural(len(readers), 'project', 'projects')} "
                    f"read{_plural(len(readers), 's', '')} it in {env} ({', '.join(readers)})")
            if not raised:
                errors.append(f"{what}. Keep the column{_plural(len(changed), '', 's')}, or raise version in "
                              f"products/{p.name}.toml to publish a breaking version")
                continue
            shut = [c for c in hit if _shut_out(c, version)]
            out = sorted({f"{c['consumer_project']} (link {c.get('alias') or '?'} accepts {c.get('accepts')})" for c in shut})
            if not out:
                notes.append(f"{what}; publishing it as version {version}")
            elif told:
                notes.append(f"{what}; publishing it as version {version}. {len(out)} "
                             f"{_plural(len(out), 'link doesn', 'links don')}'t accept it ({'; '.join(out)}), but "
                             f"the product was deprecated on {stops.isoformat()}, so "
                             f"{_plural(len(out), 'its reader', 'their readers')} had notice")
            else:
                try:
                    wider = f"{parse_accepts(shut[0].get('accepts'))[0]}-{version}"
                except ValueError:
                    wider = version
                errors.append(f"{what}. Version {version} is outside what {len(out)} "
                              f"{_plural(len(out), 'link accepts', 'links accept')}: {'; '.join(out)}. "
                              f"{_plural(len(out), 'Its project', 'Their projects')} can widen accepts in the link's file "
                              f"(for example accepts = \"{wider}\") and promote, once {_plural(len(out), 'it reads', 'they read')} "
                              f"the new version, or keep the column{_plural(len(changed), '', 's')}")
    return errors, notes


def deprecated_links(project: Project, published: list[dict[str, Any]],
                     today: dt.date | None = None) -> tuple[list[str], list[str]]:
    """Promote's rule for a project's links: a link to a product deprecated on a date that has come
    blocks the promote; one deprecated with a date still ahead is a note saying when. Runs aren't
    checked here: a running pipeline keeps reading until its team moves it."""
    today = today or utc_today()
    by_id = {p["id"]: p for p in published}
    errors, notes = [], []
    for l in project.links.values():
        product = by_id.get(l.product) or {}
        if product.get("status") != "deprecated":
            continue
        stops = _stops(product)
        if stops is None and not str(product.get("deprecated_on") or "").strip():
            notes.append(f"links/{l.alias}: {l.product} is deprecated, with no stop date set yet; "
                         "ask its owner what replaces it")
        elif stops is None:
            notes.append(f"links/{l.alias}: {l.product} is deprecated (its stop date {product['deprecated_on']} "
                         "can't be read); ask its owner what replaces it")
        elif stops <= today:
            errors.append(f"links/{l.alias}: {l.product} was deprecated on {stops.isoformat()} and its owner no longer "
                          "supports it. Move the link to what replaces it, or remove it, before promoting")
        else:
            notes.append(f"links/{l.alias}: {l.product} is deprecated and stops on {stops.isoformat()}; "
                         "move the link to what replaces it before then")
    return errors, notes
