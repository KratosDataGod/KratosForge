"""The canonical application specification (AS-02): its shape, its validation and its dependency index.

A specification is plain JSON. Everything a person or a patch can point at has an ``id`` that is unique
in the whole specification and never reused, so an edit names exactly what it changes::

    {"format": 1, "id": "app", "name": "Sales overview", "purpose": "...", "audience": ["Sales leads"],
     "target": "powerbi-report",
     "sources":      [{"id": "src_sales", "kind": "semantic-model", "name": "Sales", "workspace_id": "...", "item_id": "..."}],
     "measures":     [{"id": "m_revenue", "name": "Revenue", "source": "src_sales", "expression": "SUM(Sales[Amount])"}],
     "theme":        {"colors": {"primary": "#1F6FEB"}, "font": "Segoe UI"},
     "pages":        [{"id": "pg_overview", "title": "Overview", "components": [
                        {"id": "c_rev", "type": "card", "title": "Revenue", "measures": ["m_revenue"], "fields": [],
                         "layout": {"x": 0, "y": 0, "w": 3, "h": 2}, "props": {}}]}],
     "filters":      [{"id": "f_region", "field": "Region[Name]", "pages": "all"}],
     "interactions": [{"id": "i_drill", "kind": "drillthrough", "from": "c_rev", "to": "pg_customers"}],
     "permissions":  {"roles": [], "writeback": False},
     "refresh":      {"mode": "live"},
     "tests":        []}

Fields are ``Table[Column]`` references; measures are referenced by id. ``validate`` returns every problem
with where it is, so a model's output or a hand-edited file is refused with reasons, never half used.
"""

from __future__ import annotations

import copy
import re
from typing import Any

FORMAT = 1

# Application targets (docs/architecture/app-studio.md, Application targets). Each lists the component
# types it can express; a component outside that list is refused, not simulated.
TARGETS: dict[str, dict[str, Any]] = {
    "powerbi-report": {"label": "Power BI report", "components": {"card", "bar", "column", "line", "area", "pie", "donut",
                                                                  "table", "matrix", "slicer", "text", "waterfall",
                                                                  "scatter", "map"},
                       "interactions": {"cross-filter", "drillthrough", "bookmark"}, "writeback": False},
    "kratosforge-app": {"label": "KratosForge app", "components": {"card", "bar", "column", "line", "area", "pie", "donut",
                                                                  "table", "slicer", "text"},
                        "interactions": {"cross-filter"}, "writeback": False},
}
SOURCE_KINDS = {"semantic-model", "lakehouse-table", "local-table", "sample"}
REFRESH_MODES = {"live", "scheduled", "manual"}

ID = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")
FIELD = re.compile(r"^(?:'[^'\]\[]{1,128}'|[A-Za-z_][A-Za-z0-9_ ]{0,127})\[[^\]\[]{1,128}\]$")
COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
GRID_COLUMNS = 12
MAX_TEXT = 2000
MAX_PAGES = 50
MAX_COMPONENTS = 400

# The collections whose entries have ids, as (key, kind).
COLLECTIONS = (("sources", "source"), ("measures", "measure"), ("pages", "page"), ("filters", "filter"),
               ("interactions", "interaction"))
APP_FIELDS = {"name", "purpose", "audience", "target", "theme", "permissions", "refresh", "tests"}


class SpecError(ValueError):
    """The specification can't be used as it is; ``problems`` lists every reason with where it is."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def new(name: str, target: str = "powerbi-report", purpose: str = "") -> dict[str, Any]:
    """An empty application with one page, ready for a plan or a template to fill."""
    return {"format": FORMAT, "id": "app", "name": name, "purpose": purpose, "audience": [], "target": target,
            "sources": [], "measures": [], "theme": {"colors": {}, "font": ""},
            "pages": [{"id": "pg_1", "title": "Page 1", "components": []}],
            "filters": [], "interactions": [], "permissions": {"roles": [], "writeback": False},
            "refresh": {"mode": "live"}, "tests": []}


def _text(value: Any, where: str, problems: list[str], required: bool = False, limit: int = MAX_TEXT) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or (required and not value.strip()):
        problems.append(f"{where} must be {'non-empty ' if required else ''}text.")
    elif len(value) > limit:
        problems.append(f"{where} is longer than {limit} characters.")
    elif any(ord(c) < 32 and c not in "\n\t" for c in value):
        problems.append(f"{where} has control characters.")


def _ids(spec: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every (id, kind, where) in document order."""
    out = [(str(spec.get("id")), "app", "id")]
    for key, kind in COLLECTIONS:
        for i, entry in enumerate(spec.get(key) or []):
            if isinstance(entry, dict):
                out.append((str(entry.get("id")), kind, f"{key}[{i}]"))
                if kind == "page":
                    for j, comp in enumerate(entry.get("components") or []):
                        if isinstance(comp, dict):
                            out.append((str(comp.get("id")), "component", f"{key}[{i}].components[{j}]"))
    return out


def validate(spec: Any) -> list[str]:
    """Every problem with ``spec``; an empty list means it can be saved, previewed and compiled."""
    if not isinstance(spec, dict):
        return ["The specification must be a JSON object."]
    p: list[str] = []
    if spec.get("format") != FORMAT:
        p.append(f"format must be {FORMAT}.")
    if spec.get("id") != "app":
        p.append('id must be "app".')
    unknown = set(spec) - APP_FIELDS - {"format", "id"} - {k for k, _ in COLLECTIONS}
    if unknown:
        p.append(f"Unknown top-level fields: {', '.join(sorted(map(str, unknown)))}.")
    _text(spec.get("name"), "name", p, required=True, limit=120)
    _text(spec.get("purpose"), "purpose", p)
    audience = spec.get("audience", [])
    if not isinstance(audience, list) or not all(isinstance(a, str) and 0 < len(a) <= 120 for a in audience):
        p.append("audience must be a list of short texts.")
    target = spec.get("target")
    if target not in TARGETS:
        p.append(f"target must be one of: {', '.join(sorted(TARGETS))}.")
    caps = TARGETS.get(str(target), {"components": set(), "interactions": set(), "writeback": False})
    for key, _kind in COLLECTIONS:
        if not isinstance(spec.get(key), list) or not all(isinstance(e, dict) for e in spec.get(key) or []):
            p.append(f"{key} must be a list of objects.")
    if p and any(m.endswith("list of objects.") for m in p):
        return p

    seen: dict[str, str] = {}
    for id_, _kind, where in _ids(spec):
        if not ID.fullmatch(id_):
            p.append(f"{where}.id must start with a letter and use letters, digits, _ or - (64 at most).")
        elif id_ in seen:
            p.append(f"{where}.id {id_!r} is already used at {seen[id_]}.")
        else:
            seen[id_] = where
    kinds = {id_: kind for id_, kind, _ in _ids(spec)}

    def ref(value: Any, kind: str, where: str) -> None:
        if kinds.get(str(value)) != kind:
            p.append(f"{where} must name a {kind} in this specification.")

    for i, s in enumerate(spec["sources"]):
        if s.get("kind") not in SOURCE_KINDS:
            p.append(f"sources[{i}].kind must be one of: {', '.join(sorted(SOURCE_KINDS))}.")
        _text(s.get("name"), f"sources[{i}].name", p, required=True, limit=256)
        for k in ("workspace_id", "item_id", "table"):
            _text(s.get(k), f"sources[{i}].{k}", p, limit=256)
    for i, m in enumerate(spec["measures"]):
        _text(m.get("name"), f"measures[{i}].name", p, required=True, limit=128)
        _text(m.get("expression"), f"measures[{i}].expression", p, required=True, limit=8000)
        _text(m.get("description"), f"measures[{i}].description", p)
        ref(m.get("source"), "source", f"measures[{i}].source")
    pages = spec["pages"]
    if not pages:
        p.append("An application needs at least one page.")
    if len(pages) > MAX_PAGES:
        p.append(f"An application has at most {MAX_PAGES} pages.")
    total = 0
    for i, page in enumerate(pages):
        _text(page.get("title"), f"pages[{i}].title", p, required=True, limit=120)
        comps = page.get("components")
        if not isinstance(comps, list) or not all(isinstance(c, dict) for c in comps):
            p.append(f"pages[{i}].components must be a list of objects.")
            continue
        total += len(comps)
        for j, c in enumerate(comps):
            where = f"pages[{i}].components[{j}]"
            if c.get("type") not in caps["components"]:
                p.append(f"{where}.type {c.get('type')!r} isn't available for a {caps.get('label', 'this target')}.")
            _text(c.get("title"), f"{where}.title", p, limit=200)
            fields = c.get("fields", [])
            if not isinstance(fields, list) or not all(isinstance(f, str) and FIELD.fullmatch(f) for f in fields):
                p.append(f"{where}.fields must be a list of Table[Column] references.")
            measures = c.get("measures", [])
            if not isinstance(measures, list):
                p.append(f"{where}.measures must be a list of measure ids.")
            else:
                for n, m in enumerate(measures):
                    ref(m, "measure", f"{where}.measures[{n}]")
            lay = c.get("layout", {})
            if not isinstance(lay, dict) or not all(isinstance(lay.get(k), int) and not isinstance(lay.get(k), bool)
                                                    for k in ("x", "y", "w", "h")):
                p.append(f"{where}.layout needs whole numbers x, y, w and h.")
            elif lay["x"] < 0 or lay["y"] < 0 or lay["w"] < 1 or lay["h"] < 1 or lay["x"] + lay["w"] > GRID_COLUMNS:
                p.append(f"{where}.layout must sit inside the {GRID_COLUMNS}-column grid.")
            if not isinstance(c.get("props", {}), dict):
                p.append(f"{where}.props must be an object.")
    if total > MAX_COMPONENTS:
        p.append(f"An application has at most {MAX_COMPONENTS} components.")
    for i, f in enumerate(spec["filters"]):
        if not (isinstance(f.get("field"), str) and FIELD.fullmatch(f["field"])):
            p.append(f"filters[{i}].field must be a Table[Column] reference.")
        scope = f.get("pages", "all")
        if scope != "all":
            if not isinstance(scope, list) or not scope:
                p.append(f'filters[{i}].pages must be "all" or a list of page ids.')
            else:
                for n, pid in enumerate(scope):
                    ref(pid, "page", f"filters[{i}].pages[{n}]")
    for i, it in enumerate(spec["interactions"]):
        if it.get("kind") not in caps["interactions"]:
            p.append(f"interactions[{i}].kind {it.get('kind')!r} isn't available for a {caps.get('label', 'this target')}.")
        ref(it.get("from"), "component", f"interactions[{i}].from")
        ref(it.get("to"), "page" if it.get("kind") == "drillthrough" else "component", f"interactions[{i}].to")
    theme = spec.get("theme", {})
    if not isinstance(theme, dict) or not isinstance(theme.get("colors", {}), dict):
        p.append("theme.colors must be an object.")
    else:
        for k, v in theme.get("colors", {}).items():
            if not (isinstance(v, str) and COLOR.fullmatch(v)):
                p.append(f"theme.colors.{str(k)[:40]} must be a #RRGGBB colour.")
        _text(theme.get("font"), "theme.font", p, limit=80)
    perms = spec.get("permissions", {})
    if not isinstance(perms, dict) or not isinstance(perms.get("roles", []), list):
        p.append("permissions.roles must be a list.")
    elif perms.get("writeback") and not caps["writeback"]:
        p.append("Writeback isn't available for this target yet.")
    refresh = spec.get("refresh", {})
    if not isinstance(refresh, dict) or refresh.get("mode") not in REFRESH_MODES:
        p.append(f"refresh.mode must be one of: {', '.join(sorted(REFRESH_MODES))}.")
    if not isinstance(spec.get("tests", []), list):
        p.append("tests must be a list.")
    return p


def check(spec: Any) -> dict[str, Any]:
    """``spec`` when it is valid; SpecError with every problem otherwise."""
    problems = validate(spec)
    if problems:
        raise SpecError(problems)
    return spec


def find(spec: dict[str, Any], id_: str) -> tuple[str, dict[str, Any], list[dict[str, Any]] | None]:
    """(kind, entry, the list holding it) for an id; the app itself has no list. KeyError if absent."""
    if id_ == "app":
        return "app", spec, None
    for key, kind in COLLECTIONS:
        for entry in spec.get(key) or []:
            if entry.get("id") == id_:
                return kind, entry, spec[key]
            if kind == "page":
                for comp in entry.get("components") or []:
                    if comp.get("id") == id_:
                        return "component", comp, entry["components"]
    raise KeyError(id_)


def page_of(spec: dict[str, Any], component_id: str) -> str | None:
    for page in spec.get("pages") or []:
        if any(c.get("id") == component_id for c in page.get("components") or []):
            return str(page["id"])
    return None


def dependencies(spec: dict[str, Any]) -> dict[str, list[str]]:
    """For each id, the ids it uses (a component uses its measures and the sources behind them, a measure its
    source, an interaction both ends, a filter its pages). ``dependents`` turns this round."""
    out: dict[str, list[str]] = {}
    measure_source = {m["id"]: m.get("source") for m in spec.get("measures") or []}
    for m in spec.get("measures") or []:
        out[m["id"]] = [m["source"]] if m.get("source") else []
    for page in spec.get("pages") or []:
        out[page["id"]] = []
        for c in page.get("components") or []:
            uses = list(c.get("measures") or [])
            sources = [measure_source.get(m) for m in uses]
            out[c["id"]] = list(dict.fromkeys(uses + [s for s in sources if s]))
    for f in spec.get("filters") or []:
        out[f["id"]] = [] if f.get("pages", "all") == "all" else list(f["pages"])
    for it in spec.get("interactions") or []:
        out[it["id"]] = [it.get("from"), it.get("to")]
    return out


def dependents(spec: dict[str, Any], id_: str) -> list[str]:
    """Ids that use ``id_`` directly, in document order: what an edit or a removal of it affects."""
    return [k for k, uses in dependencies(spec).items() if id_ in uses]


def copy_of(spec: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(spec)
