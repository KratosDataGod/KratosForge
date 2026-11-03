"""The metadata project: a folder of config and code that defines every managed table.

    layers.toml                  ordered layers and each layer's policy
    environments.toml            where Dev, Test and Prod write, and each one's Keys workspace
    exceptions.toml              approved rule exceptions (reason, approver, expiry)
    metadata/entities/*.toml     one file per managed table
    metadata/keys/*.toml         one key table per business key, plus source_systems.toml
    transforms/*.py              custom Python: hooks, custom checks, custom steps
    tests/*.py                   tests that custom code points at

Each layer is its own lakehouse (``LH_<Layer>``) in every environment. An entity is
named ``<layer>.<table>`` or ``<layer>.<schema>.<table>``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import inspect
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from . import freshness as freshness_mod
from . import objectives as objectives_mod
from . import keys as keys_mod
from . import mesh as mesh_mod
from ..jsonfile import write_text
from ..redact import one_line

MAX_LAYERS = 10
# Fabric's table maintenance won't VACUUM with less than 7 days' retention.
DEFAULT_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 3650
SYSTEM_COLUMNS = ["_run_id", "_loaded_at"]
SCD2_COLUMNS = ["valid_from", "valid_to", "is_current", "row_hash"]
DRIFT_POLICIES = ("evolve", "ignore_new", "strict")
REQUIRED_CHECKS = ("key_not_null", "key_unique")
LOAD_MODES = ("full", "incremental", "scd1", "scd2")
# [load] deletes: what an incremental run does with a key the source no longer has. "none" (the
# default) leaves the row as it is; "soft" marks it with DELETE_COLUMNS; "hard" removes it.
DELETE_MODES = ("none", "soft", "hard")
DELETE_COLUMNS = ["is_deleted", "deleted_at"]
# [load] max_deletes: the most live keys one run may mark or remove, as "N%" of them or a count. A
# source that is cut short or whose key type drifted looks like a mass delete; the run stops instead.
DEFAULT_MAX_DELETES = "20%"
_MAX_DELETES = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%\s*$")
_LOOKBACK = re.compile(r"^\s*(\d+)\s*(minute|minutes|min|mins|hour|hours|day|days)\s*$", re.IGNORECASE)
LOGIC_KEYS = ("source", "load", "contract", "clean", "derive", "check", "hooks", "step", "scd", "lookup", "keys")
# Settings that change how a run reads its source, never the rows it writes: changing one isn't a
# logic change, so it doesn't ask whether to rebuild. (A lookback reads rows again; the MERGE on the
# key makes them land once, so the table is the same.)
OPERATIONAL_KEYS = (("source", "new_files_only"), ("load", "memory_guard"), ("load", "batch_by"),
                    ("load", "lookback"), ("load", "max_deletes"))
CUSTOM_RULES = ("owner", "custom_code_owner", "custom_code_test", "key_not_null", "key_unique",
                "surrogate_keys")
ENVIRONMENTS = ("dev", "test", "prod")
# An approved exception that ends inside this window raises a notice before the save gate fails.
EXCEPTION_WARN_DAYS = 14
_LAYER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
# An entity's name is its file's name and, part by part, its table's folders: none may be empty or
# hold a path separator or a drive colon.
_PATH_CHARS = re.compile(r"[/\\:]")
_IGNORED = {".git", ".local", "__pycache__", ".pytest_cache"}
# Where each environment writes and runs is deployment settings, not logic: changing it
# mustn't make an environment look out of date and ask for another promote.
_UNVERSIONED = {"environments.toml"}


class GateError(Exception):
    """A gate refused an action. ``errors`` lists every reason."""

    def __init__(self, gate: str, errors: list[str]):
        self.gate, self.errors = gate, errors
        super().__init__(f"Blocked by the {gate} gate:\n" + "\n".join(f"- {e}" for e in errors))


def parse_lookback(value: Any) -> dt.timedelta | int | None:
    """[load] lookback: how far before the saved watermark an incremental run reads again, so rows
    that arrive late still land. "2 hours", "30 minutes" or "3 days" for a date or datetime
    watermark; a whole number for a numeric one. None when unset; ValueError when it can't be read."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, int):
        if value < 0:
            raise ValueError(value)
        return value
    m = _LOOKBACK.match(value) if isinstance(value, str) else None
    if not m:
        raise ValueError(value)
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit.startswith("min"):
        return dt.timedelta(minutes=n)
    return dt.timedelta(hours=n) if unit.startswith("hour") else dt.timedelta(days=n)


def parse_max_deletes(value: Any) -> tuple[str, float]:
    """[load] max_deletes: ("share", 0..1) for "N%", or ("count", n) for a whole number. ValueError
    when it can't be read."""
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, int):
        if value < 0:
            raise ValueError(value)
        return "count", float(value)
    m = _MAX_DELETES.match(value) if isinstance(value, str) else None
    if not m or float(m.group(1)) > 100:
        raise ValueError(value)
    return "share", float(m.group(1)) / 100


def as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


@dataclass
class Layer:
    name: str
    drift: str = "ignore_new"
    required_checks: list[str] = field(default_factory=list)
    reads_from: list[str] = field(default_factory=list)  # empty: any earlier layer
    description: str = ""
    require_keys: bool = False  # every table in the layer must take surrogate keys from LH_Keys
    retention_days: int = DEFAULT_RETENTION_DAYS  # how far back its tables can be restored (VACUUM keeps this long)
    workspace: str = ""  # workspace designation (ingest, product, ...); empty: the environment's workspace

    @property
    def lakehouse(self) -> str:
        return "LH_" + self.name[:1].upper() + self.name[1:]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "drift": self.drift, "required_checks": self.required_checks,
                "reads_from": self.reads_from, "description": self.description, "lakehouse": self.lakehouse,
                "require_keys": self.require_keys, "retention_days": self.retention_days,
                "workspace": self.workspace}


@dataclass
class Entity:
    name: str
    spec: dict
    path: Path

    @property
    def layer(self) -> str:
        return self.name.split(".")[0]

    @property
    def schema(self) -> str:
        parts = self.name.split(".")
        return parts[1] if len(parts) == 3 else "dbo"

    @property
    def table(self) -> str:
        return self.name.split(".")[-1]

    @property
    def mode(self) -> str:
        return self.spec.get("load", {}).get("mode", "full")

    @property
    def keys(self) -> list[str]:
        return list(self.spec.get("contract", {}).get("keys", []))

    @property
    def columns(self) -> dict[str, str]:
        cols = dict(self.spec.get("contract", {}).get("columns", {}))
        for k in keys_mod.entity_keys(self):
            cols.setdefault(k["column"], "int32")
        return cols

    @property
    def key_columns(self) -> list[str]:
        return [k["column"] for k in keys_mod.entity_keys(self)]

    @property
    def derived(self) -> list[str]:
        return [d["column"] for d in self.spec.get("derive", [])]

    @property
    def hook_adds(self) -> list[str]:
        return list(self.spec.get("hooks", {}).get("adds", []))

    @property
    def deletes(self) -> str:
        """[load] deletes on an incremental load: none (the default), soft or hard."""
        return str(self.spec.get("load", {}).get("deletes", "none"))

    @property
    def max_deletes(self) -> tuple[str, float]:
        """[load] max_deletes, read; the default when unset or unreadable (the save gate says which)."""
        try:
            return parse_max_deletes(self.spec.get("load", {}).get("max_deletes", DEFAULT_MAX_DELETES))
        except ValueError:
            return parse_max_deletes(DEFAULT_MAX_DELETES)

    @property
    def soft_deletes(self) -> bool:
        return self.mode == "incremental" and self.deletes == "soft"

    @property
    def surrogate_key(self) -> str | None:
        return self.spec.get("scd", {}).get("surrogate_key")

    @property
    def date_column(self) -> str | None:
        """The date column that makes this dimension the semantic model's date table ([model] date_column)."""
        model = self.spec.get("model")
        return (model.get("date_column") or None) if isinstance(model, dict) else None

    def logic_hash(self) -> str:
        logic = {k: self.spec.get(k) for k in LOGIC_KEYS}
        for section, key in OPERATIONAL_KEYS:  # how a run reads, not what it produces
            if isinstance(logic.get(section), dict) and key in logic[section]:
                logic[section] = {k: v for k, v in logic[section].items() if k != key}
        return hashlib.sha1(json.dumps(logic, sort_keys=True, default=str).encode()).hexdigest()[:8]

    def sources(self) -> list[str]:
        return as_list(self.spec.get("source", {}).get("entity"))

    def reads(self) -> list[str]:
        """Every entity this one reads, from any part of its config."""
        out = list(self.sources())
        out += self.spec.get("hooks", {}).get("reads", [])
        out += self.spec.get("step", {}).get("reads", [])
        for chk in self.spec.get("check", []):
            if "exists_in" in chk:
                out.append(chk["exists_in"].rsplit(".", 1)[0])
        for lk in self.spec.get("lookup", []):
            out.append(lk["dim"])
        return list(dict.fromkeys(out))

    def mode_columns(self) -> set[str]:
        cols = set()
        if self.mode in ("scd1", "scd2") and self.surrogate_key:
            cols.add(self.surrogate_key)
        if self.mode == "scd2":
            cols |= set(SCD2_COLUMNS)
        for lk in self.spec.get("lookup", []):
            cols.add(lk["as"])
        if self.soft_deletes:
            cols |= set(DELETE_COLUMNS)
        return cols | set(self.key_columns)

    def python_refs(self):
        """(kind, "module:function", settings) for every piece of custom code."""
        hooks = self.spec.get("hooks", {})
        for point in ("after_clean", "before_write"):
            if point in hooks:
                yield f"hook {point}", hooks[point], hooks
        for c in self.spec.get("check", []):
            if "python" in c:
                yield "custom check", c["python"], c
        if "custom" in self.spec.get("step", {}):
            yield "custom step", self.spec["step"]["custom"], self.spec["step"]

    def summary(self) -> dict[str, Any]:
        s = self.spec
        src = s.get("source", {})
        return {
            "name": self.name, "layer": self.layer, "schema": self.schema, "table": self.table,
            "owner": s.get("owner", ""), "mode": self.mode, "watermark": s.get("load", {}).get("watermark"),
            "source": src.get("files") or (f"landing {src['landing']}" if src.get("landing") else "")
            or src.get("from") or src.get("table")
            or (f"link {src['link']}" if src.get("link") else "")
            or ", ".join(self.sources()) or ("custom step" if s.get("step") else ""),
            "clean": len(s.get("clean", [])), "derive": len(s.get("derive", [])), "checks": len(s.get("check", [])),
            "custom": [kind for kind, _, _ in self.python_refs()],
            "keys": self.keys, "columns": self.columns, "surrogate_keys": keys_mod.entity_keys(self), "derived": self.derived, "hook_adds": self.hook_adds,
            "reads": self.reads(), "path": str(self.path), "freshness": freshness_mod.promise(s),
            "objective": objectives_mod.objective(s), "criticality": (s.get("objectives") or {}).get("criticality")
            if isinstance(s.get("objectives"), dict) else None,
            "link": src.get("link"), "description": mesh_mod.entity_catalog(self)["description"],
        }


def write_layers_file(root: Path, layers: list[Layer], rules: dict[str, Any]) -> None:
    lines = ["# Layers, in order. Each one is its own lakehouse (LH_<Layer>) in every environment.", ""]
    if rules:
        lines.append("[rules]")
        lines += [f"{k} = {json.dumps(v)}" for k, v in rules.items()]
        lines.append("")
    for l in layers:
        lines += ["[[layer]]", f"name = {json.dumps(l.name)}", f"drift = {json.dumps(l.drift)}",
                  f"required_checks = {json.dumps(l.required_checks)}"]
        if l.reads_from:
            lines.append(f"reads_from = {json.dumps(l.reads_from)}")
        if l.description:
            lines.append(f"description = {json.dumps(l.description)}")
        if l.require_keys:
            lines.append("require_keys = true")
        if l.retention_days != DEFAULT_RETENTION_DAYS:
            lines.append(f"retention_days = {int(l.retention_days)}")
        if l.workspace:
            lines.append(f"workspace = {json.dumps(l.workspace)}")
        lines.append("")
    write_text(Path(root) / "layers.toml", "\n".join(lines))


class Project:
    def __init__(self, root: Path | str, local_data: Path | None = None):
        self.root = Path(root)
        self._local_data = local_data  # a released copy keeps its environments' local tables with the project's
        self.reload()

    @property
    def entities_dir(self) -> Path:
        return self.root / "metadata" / "entities"

    @property
    def local_data(self) -> Path:
        return self._local_data or self.root / ".local"

    def reload(self) -> None:
        cfg = tomllib.loads((self.root / "layers.toml").read_text(encoding="utf-8"))
        self.layers = [Layer(**{k: v for k, v in l.items() if k != "lakehouse"}) for l in cfg.get("layer", [])]
        self.rules = cfg.get("rules", {})
        self.entities: dict[str, Entity] = {}
        self.load_errors: list[str] = []
        for p in sorted(self.entities_dir.glob("*.toml")):
            try:
                spec = tomllib.loads(p.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                self.load_errors.append(f"{p.name}: not valid TOML ({exc})")
                continue
            name = spec.get("name") or p.stem
            if p.stem != name:
                self.load_errors.append(f"{p.name}: file name must match its name ({one_line(name, 80)})")
            self.entities[name] = Entity(name, spec, p)
        exc_file = self.root / "exceptions.toml"
        self.exceptions = tomllib.loads(exc_file.read_text(encoding="utf-8")).get("exception", []) \
            if exc_file.exists() else []
        env_file = self.root / "environments.toml"
        self.environments = tomllib.loads(env_file.read_text(encoding="utf-8")) if env_file.exists() else {}
        self.key_tables, self.key_errors = keys_mod.load_key_tables(self.root)
        self.source_systems, more = keys_mod.load_source_systems(self.root)
        self.key_errors += more
        for env in ENVIRONMENTS:
            self.environments.setdefault(env, {"target": "local"} if env == "dev" else {"workspace_id": ""})
        self.info, self.products, self.links, self.mesh_errors = mesh_mod.load(self.root)
        self.purge_code()

    def purge_code(self) -> None:
        """Forget imported custom code so edits to transforms/ take effect on the next run."""
        root = str(self.root)
        if root not in sys.path:
            sys.path.insert(0, root)
        for mod in [m for m in sys.modules if m == "transforms" or m.startswith("transforms.")]:
            del sys.modules[mod]
        importlib.invalidate_caches()

    @property
    def version(self) -> str:
        """Content hash of every config and code file, so any edit makes a new version."""
        h = hashlib.sha1()
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root)
            if p.is_file() and not (set(rel.parts) & _IGNORED) and p.suffix not in (".pyc",) \
                    and rel.as_posix() not in _UNVERSIONED:
                h.update(rel.as_posix().encode() + b"\0" + p.read_bytes().replace(b"\r\n", b"\n"))
        return h.hexdigest()[:7]

    def fingerprint(self, e: Entity) -> str:
        """The entity's logic plus any Python it runs, so code changes count as logic changes."""
        h = hashlib.sha1(e.logic_hash().encode())
        if any(True for _ in e.python_refs()):
            for p in sorted((self.root / "transforms").rglob("*.py")):
                h.update(p.relative_to(self.root).as_posix().encode() + p.read_bytes().replace(b"\r\n", b"\n"))
        return h.hexdigest()[:8]

    def layer_names(self) -> list[str]:
        return [l.name for l in self.layers]

    def layer_index(self, name: str) -> int:
        return self.layer_names().index(name)

    def layer(self, name: str) -> Layer:
        return self.layers[self.layer_index(name)]

    # ---- layer management

    def write_layers(self, layers: list[Layer]) -> None:
        write_layers_file(self.root, layers, self.rules)

    def set_layers(self, layers: list[Layer]) -> None:
        """Replace the layer list, keeping the old one if the result breaks any entity."""
        problems = []
        names = [l.name for l in layers]
        if not layers:
            problems.append("a project needs at least one layer")
        if len(layers) > MAX_LAYERS:
            problems.append(f"a project can have at most {MAX_LAYERS} layers")
        if len(set(names)) != len(names):
            problems.append("layer names must be unique")
        for l in layers:
            if not _LAYER_NAME.match(l.name):
                problems.append(f"layer name {l.name!r} must be lowercase letters, digits or _ and start with a letter")
            if l.drift not in DRIFT_POLICIES:
                problems.append(f"layer {l.name}: drift must be one of {', '.join(DRIFT_POLICIES)}")
            if not isinstance(l.retention_days, int) or not DEFAULT_RETENTION_DAYS <= l.retention_days <= MAX_RETENTION_DAYS:
                problems.append(f"layer {l.name}: keep versions for {DEFAULT_RETENTION_DAYS} to {MAX_RETENTION_DAYS} days "
                                f"(Fabric needs at least {DEFAULT_RETENTION_DAYS})")
        for e in self.entities.values():
            if e.layer not in names:
                problems.append(f"layer {e.layer} still has entities: {e.name}")
        if problems:
            raise GateError("save", sorted(set(problems)))
        old = (self.root / "layers.toml").read_text(encoding="utf-8")
        self.write_layers(layers)
        self.reload()
        errors = [e for e in validate(self) if "layer" in e or "reads" in e]
        if errors:
            write_text(self.root / "layers.toml", old)
            self.reload()
            raise GateError("save", errors)

    def active_exception(self, entity: str, rule: str, today: dt.date) -> dict | None:
        for e in self.exceptions:
            if e.get("entity") == entity and e.get("rule") == rule:
                exp = str(e.get("expires", "permanent"))
                if exp == "permanent" or dt.date.fromisoformat(exp) >= today:
                    return e
        return None

    def summary(self, today: dt.date | None = None) -> dict[str, Any]:
        today = today or dt.date.today()
        errors = validate(self, today)
        return {
            "path": str(self.root), "version": self.version,
            "layers": [l.to_dict() | {"entities": sum(e.layer == l.name for e in self.entities.values())}
                       for l in self.layers],
            "entities": [e.summary() for e in sorted(self.entities.values(),
                                                     key=lambda e: (self._layer_pos(e), e.name))],
            "environments": self.environments,
            "exceptions": self.exceptions,
            "exception_notices": describe_exceptions(self, today),
            "key_tables": [kt.summary() | {"used_by": [e.name for e in self.entities.values()
                                                        if kt.name in [k["table"] for k in keys_mod.entity_keys(e)]]}
                           for kt in sorted(self.key_tables.values(), key=lambda k: k.name)],
            "source_systems": [s.to_dict() for s in sorted(self.source_systems, key=lambda s: s.id)],
            "errors": errors,
            "run_order": run_order(self) if not any("cycle" in e for e in errors) else [],
            "project": mesh_mod.identity(self),
            "products": [p.summary() for p in sorted(self.products.values(), key=lambda p: p.name)],
            "links": [l.summary() for l in sorted(self.links.values(), key=lambda l: l.alias)],
            "designations": [{"id": d, "label": mesh_mod.DESIGNATION_LABELS[d]} for d in mesh_mod.DESIGNATIONS],
        }

    def _layer_pos(self, e: Entity) -> int:
        return self.layer_index(e.layer) if e.layer in self.layer_names() else 99


def describe_exceptions(project: Project, today: dt.date | None = None,
                        warn_days: int = EXCEPTION_WARN_DAYS) -> list[dict[str, Any]]:
    """Each approved exception, and whether it is in force, about to end, or already ended.

    ``owner`` is the table's owner. ``notice`` is set when someone should see it: the exception
    ends within ``warn_days`` (including today), has already ended, or its expiry is not a date.
    """
    today = today or dt.date.today()
    rows: list[dict[str, Any]] = []
    for raw in project.exceptions:
        entity = str(raw.get("entity") or "")
        rule = str(raw.get("rule") or "")
        expires = str(raw.get("expires", "permanent"))
        ent = project.entities.get(entity)
        owner = str(ent.spec.get("owner", "")) if ent else ""
        who = entity or "An exception"
        days_left: int | None = None
        notice = ""
        if expires == "permanent":
            status = "permanent"
        else:
            try:
                exp = dt.date.fromisoformat(expires)
            except ValueError:
                status = "invalid"
                notice = f"{who}: the exception for {rule} has an expiry that isn't a date ({expires})"
            else:
                days_left = (exp - today).days
                if days_left < 0:
                    status = "expired"
                    notice = (f"{who}: the exception for {rule} expired on {expires}. "
                              "The save gate fails again until the rule is met or the exception is renewed.")
                elif days_left <= warn_days:
                    status = "expiring"
                    when = "today" if days_left == 0 else f"in {days_left} day" + ("" if days_left == 1 else "s")
                    notice = f"{who}: the exception for {rule} expires {when} ({expires})"
                else:
                    status = "active"
        rows.append({
            "entity": entity, "rule": rule, "reason": str(raw.get("reason") or ""),
            "approver": str(raw.get("approver") or ""), "expires": expires, "owner": owner,
            "status": status, "days_left": days_left, "notice": notice,
        })
    return rows


# --------------------------------------------------------------------------- gate 1: on save


def check_expr(expr: str, available: set[str]) -> list[str]:
    try:
        roots = pl.sql_expr(expr).meta.root_names()
    except Exception as ex:  # noqa: BLE001
        return [f"expression {expr!r} does not parse ({type(ex).__name__})"]
    return [f"uses unknown column {c}" for c in roots if c not in available]


def load_python(ref: str):
    module, fn = ref.split(":")
    return getattr(importlib.import_module(module), fn)


def check_python(ref: str) -> str | None:
    if ":" not in ref or not ref.startswith("transforms."):
        return "must be transforms.<module>:<function>"
    try:
        fn = load_python(ref)
    except Exception as ex:  # noqa: BLE001
        return f"cannot be imported ({type(ex).__name__}: {ex})"
    params = list(inspect.signature(fn).parameters)
    if params[:2] != ["df", "ctx"]:
        return "must take (df, ctx)"
    return None


def source_columns(project: Project, e: Entity) -> set[str]:
    src = e.spec.get("source", {})
    if "files" in src or "landing" in src or "table" in src:
        return set(src.get("columns", e.columns))
    if "link" in src:
        link = project.links.get(src["link"])
        return set(src.get("columns") or (link.columns if link else {}) or e.columns)
    cols: set[str] = set()
    for s in e.sources():
        if s in project.entities:
            up = project.entities[s]
            cols |= set(up.columns) | set(up.derived) | set(up.hook_adds) | up.mode_columns() | set(SYSTEM_COLUMNS)
    return cols


def _batch_by_problems(e: Entity) -> list[str]:
    """[load] batch_by: a column the source gives as is, on a full or incremental load of a Delta source."""
    s = e.spec
    col = s.get("load", {}).get("batch_by")
    if col is None:
        return []
    src = s.get("source", {})
    if not isinstance(col, str) or col not in e.columns:
        return [f"{e.name}: load batch_by names one contract column"]
    if e.mode not in ("full", "incremental"):
        return [f"{e.name}: load batch_by works for full and incremental loads; dimensions are merged by key"]
    if not any(k in src for k in ("table", "link", "entity")) or "custom" in s.get("step", {}):
        return [f"{e.name}: load batch_by needs a Delta source (table, link or entity) and no custom step"]
    if col in e.derived or col in e.hook_adds or col in e.mode_columns():
        return [f"{e.name}: load batch_by must be a column the source has, not one this entity makes"]
    return []


def _late_and_delete_problems(e: Entity) -> list[str]:
    """[load] lookback and deletes: an incremental load of a Delta source, merged on a key."""
    s, errors = e.spec, []
    load, src = s.get("load", {}), s.get("source", {})
    delta_source = any(k in src for k in ("table", "link", "entity"))
    if "lookback" in load:
        try:
            parse_lookback(load["lookback"])
        except ValueError:
            errors.append(f'{e.name}: load lookback is a duration like "2 hours", "30 minutes" or "3 days" '
                          "(or a whole number for a numeric watermark)")
        if e.mode != "incremental" or not load.get("watermark"):
            errors.append(f"{e.name}: load lookback is for incremental loads with a watermark")
        elif not delta_source:
            errors.append(f"{e.name}: load lookback needs a Delta source (table, link or entity); "
                          "file sources read new files instead of a watermark")
        elif not e.keys:
            errors.append(f"{e.name}: load lookback reads rows again, so it needs [contract] keys for them "
                          "to replace themselves instead of landing twice")
    mode = load.get("deletes", "none")
    if mode not in DELETE_MODES:
        errors.append(f"{e.name}: load deletes must be one of {', '.join(DELETE_MODES)}")
    elif mode != "none":
        if e.mode != "incremental":
            errors.append(f"{e.name}: load deletes is for incremental loads")
        elif not delta_source:
            errors.append(f"{e.name}: load deletes needs a Delta source (table, link or entity) to compare keys with")
        elif not e.keys:
            errors.append(f"{e.name}: load deletes compares keys with the source, so it needs [contract] keys")
        elif "custom" in s.get("step", {}):
            errors.append(f"{e.name}: load deletes can't be used with a custom step, which makes its own rows")
        else:
            made = [k for k in e.keys if k in e.derived or k in e.hook_adds or k in e.key_columns]
            if made:
                errors.append(f"{e.name}: load deletes needs keys the source has; {', '.join(made)} "
                              "is made by this entity")
        if "max_deletes" in load:
            try:
                parse_max_deletes(load["max_deletes"])
            except ValueError:
                errors.append(f'{e.name}: load max_deletes is a share like "50%" or a whole number of keys')
        taken = [c for c in DELETE_COLUMNS if c in s.get("contract", {}).get("columns", {})]
        if mode == "soft" and taken:
            errors.append(f'{e.name}: load deletes = "soft" adds {", ".join(taken)} itself; '
                          "take them out of the contract")
    return errors



def entity_name_problem(name: str) -> str | None:
    """Why ``name`` can't name an entity, or None."""
    parts = str(name).split(".")
    if len(parts) not in (2, 3):
        return "name must be <layer>.<table> or <layer>.<schema>.<table>"
    if not all(parts) or _PATH_CHARS.search(name):
        return "each part of the name needs at least one character, and none can hold / \\ or :"
    return None

def validate(project: Project, today: dt.date | None = None) -> list[str]:
    """The save gate. Every problem in the project; an empty list means it is clean."""
    today = today or dt.date.today()
    errors: list[str] = list(project.load_errors)
    names = project.layer_names()
    same_layer = project.rules.get("same_layer_reads", "dag")

    def err(entity: str, rule: str, msg: str) -> None:
        if not project.active_exception(entity, rule, today):
            errors.append(f"{entity}: {msg}")

    for e in project.entities.values():
        s = e.spec
        problem = entity_name_problem(e.name)
        if problem:
            errors.append(f"{e.name}: {problem}")
            continue
        if e.layer not in names:
            errors.append(f"{e.name}: layer {e.layer} does not exist")
            continue
        if not s.get("owner"):
            err(e.name, "owner", "needs an owner")
        if e.mode not in LOAD_MODES:
            errors.append(f"{e.name}: load mode must be one of {', '.join(LOAD_MODES)}")
        if not e.columns and not s.get("step"):
            errors.append(f"{e.name}: contract needs at least one column")
        for k in e.keys:
            if k not in e.columns:
                errors.append(f"{e.name}: key {k} is not a contract column")
        if e.mode == "incremental" and not s.get("load", {}).get("watermark") and "entity" in s.get("source", {}):
            errors.append(f"{e.name}: incremental loads need a watermark column")
        wm, renames = s.get("load", {}).get("watermark"), s.get("source", {}).get("rename") or {}
        if isinstance(renames, dict) and wm in renames and wm not in e.columns:
            errors.append(f"{e.name}: the watermark is a contract column: the source renames {wm} to "
                          f"{renames[wm]}, so use watermark = \"{renames[wm]}\"")
        if e.mode in ("scd1", "scd2"):
            if not e.surrogate_key:
                errors.append(f"{e.name}: dimensions need [scd] surrogate_key")
            if not e.keys:
                errors.append(f"{e.name}: dimensions need a business key in [contract] keys")
            if e.mode == "scd2" and not s.get("scd", {}).get("tracked"):
                errors.append(f"{e.name}: Type 2 dimensions need [scd] tracked columns")
        model = s.get("model", {})
        if not isinstance(model, dict) or not isinstance(model.get("date_column", ""), str):
            errors.append(f'{e.name}: [model] date_column names a date column, like date_column = "date"')
        elif e.date_column:
            if e.mode not in ("scd1", "scd2") or not e.surrogate_key:
                errors.append(f"{e.name}: [model] date_column is for a dimension (scd1 or scd2 with a surrogate key), "
                              "which facts relate to")
            elif str(e.columns.get(e.date_column, "")).lower() not in ("date", "datetime"):
                errors.append(f"{e.name}: [model] date_column {e.date_column} must be a contract column of type date "
                              "or datetime")
        files = s.get("source", {}).get("files", "") or s.get("source", {}).get("landing", "")
        if files and (Path(files).is_absolute() or re.match(r"^[A-Za-z]:", files)):
            errors.append(f"{e.name}: source path {files} only exists on this PC; put the files in the "
                          f"lakehouse Files area and use a relative path")
        errors += freshness_mod.problems(e.name, s, ENVIRONMENTS)
        errors += objectives_mod.problems(e.name, s, ENVIRONMENTS)
        errors += _batch_by_problems(e)
        errors += _late_and_delete_problems(e)
        own_drift = s.get("contract", {}).get("drift")
        if own_drift is not None and own_drift not in DRIFT_POLICIES:
            errors.append(f"{e.name}: contract drift must be one of {', '.join(DRIFT_POLICIES)}")
        rename = s.get("source", {}).get("rename", {})
        if not isinstance(rename, dict) or not all(isinstance(v, str) for v in rename.values()):
            errors.append(f'{e.name}: source rename maps a source column to a contract column, like {{ cust_id = "customer_id" }}')
        if not isinstance(s.get("contract", {}).get("overwrite_schema", False), bool):
            errors.append(f"{e.name}: contract overwrite_schema is true or false")

        # No mixing: never read a later layer; the same layer only when the layer allows it.
        me = project.layer_index(e.layer)
        allowed = project.layer(e.layer).reads_from
        for r in e.reads():
            if r not in project.entities:
                errors.append(f"{e.name}: reads {r}, which is not a managed entity")
                continue
            other = project.entities[r]
            if other.layer not in names:
                continue
            theirs = project.layer_index(other.layer)
            if theirs > me:
                errors.append(f"{e.name}: reads {r} from a later layer ({other.layer})")
            elif theirs == me and same_layer == "never":
                errors.append(f"{e.name}: reads {r} from its own layer")
            elif theirs < me and allowed and other.layer not in allowed:
                errors.append(f"{e.name}: layer {e.layer} may only read from {', '.join(allowed)}, not {other.layer}")

        # Required checks for the layer.
        have = set()
        for c in s.get("check", []):
            if c.get("column") in e.keys and c.get("not_null"):
                have.add("key_not_null")
            if c.get("column") in e.keys and c.get("unique"):
                have.add("key_unique")
        if e.mode in ("scd1", "scd2"):
            have |= {"key_not_null", "key_unique"}
        for req in project.layer(e.layer).required_checks:
            if req not in have:
                err(e.name, req, f"layer {e.layer} requires a {req.replace('_', ' ')} check on the key")

        # Columns used must exist upstream: catches renames, deletes and typos.
        available = source_columns(project, e)
        produced: set[str] = set()
        for d in s.get("derive", []):
            errors += [f"{e.name}: derive {d.get('column')}: {m}"
                       for m in check_expr(d.get("expr", ""), available | produced)]
            produced.add(d.get("column"))
        for c in s.get("clean", []):
            col = c.get("column")
            if col and col not in available:
                errors.append(f"{e.name}: clean rule uses unknown column {col}")
        if not s.get("step") and s.get("source"):
            missing = set(e.columns) - available - produced - set(e.hook_adds) - e.mode_columns()
            for c in sorted(missing):
                errors.append(f"{e.name}: contract column {c} is not in the source and nothing derives it")
        output = set(e.columns) | produced | set(e.hook_adds) | e.mode_columns()
        for c in s.get("check", []):
            if "expr" in c:
                errors += [f"{e.name}: check {c.get('name', '')}: {m}" for m in check_expr(c["expr"], output)]
            if c.get("column") and c["column"] not in output:
                errors.append(f"{e.name}: check on unknown column {c['column']}")
            if c.get("on_fail", "stop") not in ("stop", "quarantine", "warn"):
                errors.append(f"{e.name}: on_fail must be stop, quarantine or warn")
            if "exists_in" in c:
                tgt, col = c["exists_in"].rsplit(".", 1)
                if tgt in project.entities and col not in project.entities[tgt].columns:
                    errors.append(f"{e.name}: check exists_in {c['exists_in']}: no such column")
        for lk in s.get("lookup", []):
            dim = project.entities.get(lk.get("dim", ""))
            if dim and dim.mode not in ("scd1", "scd2"):
                errors.append(f"{e.name}: lookup {dim.name} is not a dimension")
            for col in (lk.get("on"), lk.get("at")):
                if col and col not in available | produced:
                    errors.append(f"{e.name}: lookup uses unknown column {col}")

        # Custom Python: importable, right signature, an owner and a test.
        for kind, ref, settings in e.python_refs():
            problem = check_python(ref)
            if problem:
                errors.append(f"{e.name}: {kind} {ref}: {problem}")
            if not settings.get("owner"):
                err(e.name, "custom_code_owner", f"{kind} {ref} needs an owner")
            test = settings.get("test")
            if not test or not (project.root / test.split("::")[0]).exists():
                err(e.name, "custom_code_test", f"{kind} {ref} needs a test")

    errors += keys_mod.validate_keys(project, today)
    errors += mesh_mod.validate(project)

    cycle = find_cycle(project)
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(cycle))

    for x in project.exceptions:
        exp = str(x.get("expires", "permanent"))
        if exp != "permanent":
            try:
                if dt.date.fromisoformat(exp) < today:
                    errors.append(f"{x.get('entity')}: exception for {x.get('rule')} expired on {exp}")
            except ValueError:
                errors.append(f"{x.get('entity')}: exception expiry {exp} is not a date")
        if not x.get("reason") or not x.get("approver"):
            errors.append(f"{x.get('entity')}: exception for {x.get('rule')} needs a reason and an approver")
    return list(dict.fromkeys(errors))


def _graph(project: Project) -> dict[str, list[str]]:
    return {n: [r for r in e.reads() if r in project.entities and r != n] for n, e in project.entities.items()}


def find_cycle(project: Project) -> list[str] | None:
    graph = _graph(project)
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        stack.append(n)
        for m in graph[n]:
            if state.get(m) == 1:
                return stack[stack.index(m):] + [m]
            if m not in state and (c := visit(m)):
                return c
        stack.pop()
        state[n] = 2
        return None

    for n in sorted(graph):
        if n not in state and (c := visit(n)):
            return c
    return None


def run_order(project: Project) -> list[str]:
    graph = _graph(project)
    out: list[str] = []
    seen: set[str] = set()

    def visit(n: str) -> None:
        if n in seen:
            return
        seen.add(n)
        for m in graph[n]:
            visit(m)
        out.append(n)

    for n in sorted(graph, key=lambda n: (project._layer_pos(project.entities[n]), n)):
        visit(n)
    return out


def impact(project: Project, entity: str) -> list[str]:
    """Everything downstream of an entity, nearest first."""
    out: list[str] = []
    frontier = [entity]
    while frontier:
        nxt = []
        for f in frontier:
            for n, e in project.entities.items():
                if f in e.reads() and n not in out and n != entity:
                    out.append(n)
                    nxt.append(n)
        frontier = nxt
    return out
