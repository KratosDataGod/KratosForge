"""Generation (AS-10): a description becomes a plan from the person's model, and an approved plan a specification.

The model never writes the specification. It answers ``PLAN_SCHEMA``: pages and, on each, components that name
a type, a title, at most one measure and at most one field, using only the measures and columns in the
metadata it was given. ``check_plan`` keeps only what the target can show and the metadata has, says what it
left out, and refuses a plan with nothing left. ``to_spec`` turns a checked plan into a specification with
ids, layout and sources made here, then validates it (spec.check) before anything is saved.

What the model sees is ``context``: sanitized metadata (discovery.from_bim, or a template's sample fields),
never rows. Measure formulas (DAX) are included only when the person asks for them, because a formula can
hold literal business values (a customer name, a threshold, a price); the consent text says so.
"""

from __future__ import annotations

import re
from typing import Any

from .. import redact
from . import spec as specs

MAX_PAGES = 10
MAX_COMPONENTS_PER_PAGE = 12
TITLE_LIMIT = 120

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "purpose", "pages"],
    "properties": {
        "name": {"type": "string", "maxLength": 120},
        "purpose": {"type": "string", "maxLength": 500},
        "audience": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 5},
        "pages": {"type": "array", "maxItems": MAX_PAGES, "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "components"],
            "properties": {
                "title": {"type": "string", "maxLength": TITLE_LIMIT},
                "components": {"type": "array", "maxItems": MAX_COMPONENTS_PER_PAGE, "items": {
                    "type": "object", "additionalProperties": False, "required": ["type", "title"],
                    "properties": {
                        "type": {"type": "string"},
                        "title": {"type": "string", "maxLength": 200},
                        "measure": {"type": "string", "description": "A measure name from DATA, or empty"},
                        "field": {"type": "string", "description": "A column from DATA as Table[Column], or empty"},
                    }}}}}},
        "questions": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 5},
    },
}

INSTRUCTION = (
    "Plan a report application for the person's description. Use only the measures and columns listed in DATA: "
    "a component's measure is a measure name exactly as listed, and its field is a column written Table[Column]. "
    "Component types: {types}. A card shows one measure; a slicer shows one field and no measure; text shows its "
    "title. Charts and tables show one measure by one field. Keep to at most {pages} pages and {per} components a "
    "page. If the description asks for something DATA can't support, leave it out and say so in questions.")

# How much room each type takes on the 12-column grid: (width, height).
_SIZE = {"card": (3, 2), "slicer": (3, 2), "text": (12, 1)}
_DEFAULT_SIZE = (6, 4)


def instruction(target: str) -> str:
    types = ", ".join(sorted(specs.TARGETS[target]["components"]))
    return INSTRUCTION.format(types=types, pages=MAX_PAGES, per=MAX_COMPONENTS_PER_PAGE)


def context(metadata: dict[str, Any]) -> dict[str, Any]:
    """The metadata block for providers.build_messages: visible tables, their columns and measures."""
    tables = []
    for t in metadata.get("tables") or []:
        if t.get("hidden"):
            continue
        row: dict[str, Any] = {"name": t["name"],
                               "columns": [{k: c[k] for k in ("name", "type", "description") if k in c}
                                           for c in t.get("columns") or [] if not c.get("hidden")],
                               "measures": [{k: m[k] for k in ("name", "description", "format", "expression") if k in m}
                                            for m in t.get("measures") or [] if not m.get("hidden")]}
        if t.get("description"):
            row["description"] = t["description"]
        tables.append(row)
    return {"kind": "metadata", "data": {"tables": tables, "relationships": metadata.get("relationships") or []}}


def summary(metadata: dict[str, Any]) -> dict[str, Any]:
    """What a context sends, in counts, for the person to see before and after."""
    data = context(metadata)["data"]
    return {"tables": len(data["tables"]),
            "columns": sum(len(t["columns"]) for t in data["tables"]),
            "measures": sum(len(t["measures"]) for t in data["tables"]),
            "expressions": any("expression" in m for t in data["tables"] for m in t["measures"]),
            "rows": 0}


def _clean_text(value: Any, limit: int) -> str:
    return redact.one_line(value, limit) if isinstance(value, str) else ""


def check_plan(plan: Any, metadata: dict[str, Any], target: str) -> dict[str, Any]:
    """``{"plan": clean, "left_out": [...]}``: only components the target can show, with measures and fields the
    metadata has. Raises SpecError when nothing usable is left."""
    if target not in specs.TARGETS:
        raise specs.SpecError([f"target must be one of: {', '.join(sorted(specs.TARGETS))}."])
    if not isinstance(plan, dict) or not isinstance(plan.get("pages"), list):
        raise specs.SpecError(["The model's plan has no pages."])
    allowed = specs.TARGETS[target]["components"]
    measures = {m["name"] for t in metadata.get("tables") or [] for m in t.get("measures") or []}
    columns = {f"{t['name']}[{c['name']}]" for t in metadata.get("tables") or [] for c in t.get("columns") or []}
    quoted = {f"'{t['name']}'[{c['name']}]": f"{t['name']}[{c['name']}]"
              for t in metadata.get("tables") or [] for c in t.get("columns") or []}
    left_out: list[str] = []
    pages = []
    for i, page in enumerate(plan["pages"][:MAX_PAGES]):
        if not isinstance(page, dict):
            continue
        title = _clean_text(page.get("title"), TITLE_LIMIT) or f"Page {i + 1}"
        comps = []
        raw = page.get("components")
        given: list[Any] = raw if isinstance(raw, list) else []
        for c in given[:MAX_COMPONENTS_PER_PAGE]:
            if not isinstance(c, dict):
                continue
            ctype, ctitle = c.get("type"), _clean_text(c.get("title"), 200)
            name = ctitle or str(ctype)[:40]
            if ctype not in allowed:
                left_out.append(f"{title}: {name} ({str(ctype)[:40]} isn't available for a {specs.TARGETS[target]['label']})")
                continue
            measure: str = c["measure"] if isinstance(c.get("measure"), str) else ""
            field: str = c["field"] if isinstance(c.get("field"), str) else ""
            field = quoted.get(field, field)
            if measure and measure not in measures:
                left_out.append(f"{title}: {name} (the model has no measure {measure[:80]!r})")
                continue
            if field and field not in columns:
                left_out.append(f"{title}: {name} (the model has no column {field[:120]!r})")
                continue
            if ctype == "slicer" and (not field or measure):
                left_out.append(f"{title}: {name} (a slicer needs one field and no measure)")
                continue
            if ctype == "card" and not measure:
                left_out.append(f"{title}: {name} (a card needs a measure)")
                continue
            if ctype not in ("card", "slicer", "text") and not (measure and field):
                left_out.append(f"{title}: {name} (a {ctype} needs a measure and a field)")
                continue
            comps.append({"type": ctype, "title": ctitle, "measure": measure if ctype != "text" else "",
                          "field": field if ctype not in ("card", "text") else ""})
        if comps:
            pages.append({"title": title, "components": comps})
        elif page.get("components"):
            left_out.append(f"{title}: the whole page (nothing on it could be used)")
    if not pages:
        raise specs.SpecError(["Nothing in the model's plan could be used with this data.", *left_out[:20]])
    audience = [a for a in (_clean_text(x, 120) for x in plan.get("audience") or [] if isinstance(x, str)) if a][:5]
    questions = [q for q in (_clean_text(x, 300) for x in plan.get("questions") or [] if isinstance(x, str)) if q][:5]
    return {"plan": {"name": _clean_text(plan.get("name"), 120) or "New app", "purpose": _clean_text(plan.get("purpose"), 500),
                     "audience": audience, "pages": pages, "questions": questions},
            "left_out": left_out}


def _ident(text: str) -> str:
    """Table names as a FIELD reference wants them: quoted when they hold anything but letters, digits and _."""
    return text if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text) else f"'{text}'"


def _field_ref(field: str) -> str:
    table, col = field[:-1].split("[", 1)
    return f"{_ident(table)}[{col}]"


def to_spec(plan: dict[str, Any], metadata: dict[str, Any], target: str, source: dict[str, Any], name: str = "") -> dict[str, Any]:
    """The specification for a checked plan. Ids and layout are made here; measures come from the metadata.
    ``source`` is the spec source entry (its id is set here). Raises SpecError when the result isn't valid."""
    doc = specs.new(name or plan["name"], target, plan.get("purpose", ""))
    doc["audience"] = list(plan.get("audience") or [])
    doc["sources"] = [{**{k: v for k, v in source.items() if k != "id"}, "id": "src_1"}]
    home = {m["name"]: m for t in metadata.get("tables") or [] for m in t.get("measures") or []}
    ids: dict[str, str] = {}
    doc["measures"] = []
    for page in plan["pages"]:
        for c in page["components"]:
            m = c.get("measure")
            if m and m not in ids:
                ids[m] = f"m_{len(ids) + 1}"
                # A measure the model already has is referenced by name; its formula stays in the model.
                doc["measures"].append({"id": ids[m], "name": m, "source": "src_1",
                                        "expression": home[m].get("expression") or f"[{m}]"})
    pages = []
    n = 0
    for p, page in enumerate(plan["pages"]):
        x = y = row_h = 0
        comps = []
        for c in page["components"]:
            n += 1
            w, h = _SIZE.get(c["type"], _DEFAULT_SIZE)
            if x + w > specs.GRID_COLUMNS:
                x, y, row_h = 0, y + row_h, 0
            comps.append({"id": f"c_{n}", "type": c["type"], "title": c["title"],
                          "measures": [ids[c["measure"]]] if c.get("measure") else [],
                          "fields": [_field_ref(c["field"])] if c.get("field") else [],
                          "layout": {"x": x, "y": y, "w": w, "h": h},
                          "props": {"text": c["title"]} if c["type"] == "text" else {}})
            x, row_h = x + w, max(row_h, h)
        pages.append({"id": f"pg_{p + 1}", "title": page["title"], "components": comps})
    doc["pages"] = pages
    return specs.check(doc)


def sample_metadata() -> dict[str, Any]:
    """Metadata for the templates' sample data, so a plan can be made before any semantic model is connected."""
    from . import templates

    tables: dict[str, dict[str, Any]] = {}
    for key in templates.TEMPLATES:
        doc = templates.build(key, "Sample")
        for c in (c for p in doc["pages"] for c in p["components"]):
            for f in c["fields"]:
                table, col = f[:-1].split("[", 1)
                cols = tables.setdefault(table.strip("'"), {"name": table.strip("'"), "columns": [], "measures": []})["columns"]
                if all(x["name"] != col for x in cols):
                    cols.append({"name": col, "type": "string"})
        for m in doc["measures"]:
            home = tables.setdefault("Measures", {"name": "Measures", "columns": [], "measures": []})["measures"]
            if all(x["name"] != m["name"] for x in home):
                home.append({"name": m["name"], "format": "#,0"})
    return {"tables": sorted(tables.values(), key=lambda t: t["name"]), "relationships": [], "truncated": False}
