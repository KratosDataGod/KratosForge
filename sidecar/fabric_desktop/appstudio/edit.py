"""Conversational edits (AS-11): a request becomes a change plan from the person's model, then patch operations.

The model never sends patch operations or JSON for the specification. It answers ``CHANGE_SCHEMA`` with a
short list of named changes ("set_title", "add_component", ...) that point at ids it was shown. ``to_ops``
turns each into patch operations here, against the current specification and the source's metadata: a
measure is named, and must be one the app already has or the model holds; a field must be a column the
model holds; a type must be one the target can show. A change that doesn't fit is left out and listed. The
operations are then applied to a copy (patch.apply), which validates the whole result; only that is offered
for approval, and saving it goes through project.change with its inverse, so Undo takes it back.
Everything the request didn't name stays exactly as it was.
"""

from __future__ import annotations

from typing import Any

from .. import redact
from . import generate
from . import patch as patches
from . import spec as specs

MAX_CHANGES = 30
WIDTHS = (2, 3, 4, 6, 8, 9, 12)
ACTIONS = ("set_title", "set_type", "set_measure", "set_field", "set_width", "remove", "add_component", "add_page", "rename_app")

CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "changes"],
    "properties": {
        "summary": {"type": "string", "maxLength": 300},
        "changes": {"type": "array", "maxItems": MAX_CHANGES, "items": {
            "type": "object", "additionalProperties": False, "required": ["action"],
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "id": {"type": "string", "description": "The component or page id from APP"},
                "page": {"type": "string", "description": "add_component: the page id"},
                "type": {"type": "string"},
                "title": {"type": "string", "maxLength": 200},
                "measure": {"type": "string", "description": "A measure name from DATA"},
                "field": {"type": "string", "description": "A column from DATA as Table[Column]"},
                "width": {"type": "integer"},
            }}},
        "questions": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 3},
    },
}

INSTRUCTION = (
    "Change the application in APP as the person asks, and nothing else. Answer with a short summary and a list of "
    "changes. Each change names one action: set_title (id, title), set_type (id, type), set_measure (id, measure), "
    "set_field (id, field), set_width (id, width of {widths} columns), remove (id), add_component (page, type, title, "
    "measure, field), add_page (title) or rename_app (title). Ids come from APP. Measures are names from DATA; fields "
    "are columns from DATA written Table[Column]. Component types: {types}. If the request can't be done with this "
    "data, make no change for it and say why in questions.")


def instruction(target: str) -> str:
    return INSTRUCTION.format(widths=", ".join(map(str, WIDTHS)), types=", ".join(sorted(specs.TARGETS[target]["components"])))


def app_view(spec: dict[str, Any]) -> dict[str, Any]:
    """What the model is shown of the app: ids, types, titles, and measures by name. No formulas, no sources."""
    names = {m["id"]: m["name"] for m in spec["measures"]}
    return {"name": spec["name"], "target": spec["target"], "pages": [
        {"id": p["id"], "title": p["title"], "components": [
            {"id": c["id"], "type": c["type"], "title": c.get("title", ""),
             "measure": names.get((c.get("measures") or [""])[0], ""), "field": (c.get("fields") or [""])[0],
             "width": c["layout"]["w"]} for c in p["components"]]} for p in spec["pages"]]}


def context(spec: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    data = generate.context(metadata)["data"]
    return {"kind": "metadata", "data": {"app": app_view(spec), **data}}


def _text(value: Any, limit: int) -> str:
    return redact.one_line(value, limit) if isinstance(value, str) else ""


def _new_id(prefix: str, taken: set[str]) -> str:
    n = 1
    while f"{prefix}_{n}" in taken:
        n += 1
    taken.add(f"{prefix}_{n}")
    return f"{prefix}_{n}"


def _field_ref(field: str, columns: dict[str, str]) -> str | None:
    """The spec reference for a column the metadata has (quoted or not), or None."""
    return columns.get(field)


def to_ops(reply: Any, spec: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """``{"ops", "summary", "questions", "done": [what each kept change does], "left_out": [...]}``.
    Raises SpecError when nothing in the reply can be used."""
    if not isinstance(reply, dict) or not isinstance(reply.get("changes"), list):
        raise specs.SpecError(["The model's answer has no list of changes."])
    target = spec["target"]
    allowed = specs.TARGETS[target]["components"]
    kinds = {id_: kind for id_, kind, _ in specs._ids(spec)}
    taken = set(kinds)
    model_measures = {m["name"] for t in metadata.get("tables") or [] for m in t.get("measures") or []}
    expressions = {m["name"]: m.get("expression") for t in metadata.get("tables") or [] for m in t.get("measures") or []}
    measure_ids = {m["name"]: m["id"] for m in spec["measures"]}
    columns: dict[str, str] = {}
    for t in metadata.get("tables") or []:
        for c in t.get("columns") or []:
            spec_ref = f"{generate._ident(t['name'])}[{c['name']}]"
            for spelled in (f"{t['name']}[{c['name']}]", f"'{t['name']}'[{c['name']}]"):
                columns[spelled] = spec_ref
    source = spec["sources"][0]["id"] if spec["sources"] else ""
    ops: list[dict[str, Any]] = []
    done: list[str] = []
    left_out: list[str] = []
    page_len = {p["id"]: len(p["components"]) for p in spec["pages"]}
    layout = {c["id"]: c["layout"] for p in spec["pages"] for c in p["components"]}

    def measure_id(name: str) -> str | None:
        if name in measure_ids:
            return measure_ids[name]
        if name not in model_measures or not source:
            return None
        mid = _new_id("m", taken)
        ops.append({"op": "add", "kind": "measure", "value": {"id": mid, "name": name, "source": source,
                                                               "expression": expressions.get(name) or f"[{name}]"}})
        measure_ids[name] = mid
        return mid

    for ch in reply["changes"][:MAX_CHANGES]:
        if not isinstance(ch, dict):
            continue
        action = ch.get("action")
        id_: str = ch["id"] if isinstance(ch.get("id"), str) else ""
        what = f"{action} {id_}".strip()[:80]
        kind = kinds.get(id_)
        if action in ("set_title", "set_type", "set_measure", "set_field", "set_width", "remove") and kind not in ("component", "page"):
            left_out.append(f"{what}: there's no component or page with that id")
            continue
        if action == "set_title":
            title = _text(ch.get("title"), 200)
            if not title:
                left_out.append(f"{what}: no title given")
                continue
            ops.append({"op": "set", "id": id_, "field": "title", "value": title})
            done.append(f"Rename {id_} to {title!r}")
        elif action == "set_type" and kind == "component":
            if ch.get("type") not in allowed:
                left_out.append(f"{what}: {str(ch.get('type'))[:40]} isn't available for a {specs.TARGETS[target]['label']}")
                continue
            ops.append({"op": "set", "id": id_, "field": "type", "value": ch["type"]})
            done.append(f"Show {id_} as a {ch['type']}")
        elif action == "set_measure" and kind == "component":
            found = measure_id(_text(ch.get("measure"), 128))
            if not found:
                left_out.append(f"{what}: the model has no measure {str(ch.get('measure'))[:80]!r}")
                continue
            ops.append({"op": "set", "id": id_, "field": "measures", "value": [found]})
            done.append(f"Show {ch['measure']} in {id_}")
        elif action == "set_field" and kind == "component":
            col = _field_ref(str(ch.get("field") or ""), columns)
            if not col:
                left_out.append(f"{what}: the model has no column {str(ch.get('field'))[:120]!r}")
                continue
            ops.append({"op": "set", "id": id_, "field": "fields", "value": [col]})
            done.append(f"Show {id_} by {col}")
        elif action == "set_width" and kind == "component":
            w = ch.get("width")
            if w not in WIDTHS:
                left_out.append(f"{what}: width must be one of {', '.join(map(str, WIDTHS))}")
                continue
            lay = layout[id_]
            new = {**lay, "w": w, "x": min(lay["x"], specs.GRID_COLUMNS - w)}
            ops.append({"op": "set", "id": id_, "field": "layout", "value": new})
            layout[id_] = new
            done.append(f"Make {id_} {w} columns wide")
        elif action == "remove":
            ops.append({"op": "remove", "id": id_})
            done.append(f"Remove {id_}")
        elif action == "add_component":
            page: str = str(ch.get("page")) if kinds.get(str(ch.get("page"))) == "page" else ""
            ctype = str(ch.get("type"))
            if not page or ctype not in allowed:
                left_out.append(f"{what}: needs a page id from the app and a type the target can show")
                continue
            mid: str | None = measure_id(_text(ch.get("measure"), 128)) if ch.get("measure") else None
            ref: str | None = _field_ref(str(ch.get("field") or ""), columns) if ch.get("field") else None
            if (ch.get("measure") and not mid) or (ch.get("field") and not ref):
                left_out.append(f"{what}: its measure or field isn't in the model")
                continue
            w, h = generate._SIZE.get(ctype, generate._DEFAULT_SIZE)
            bottom = max((lay["y"] + lay["h"] for cid, lay in layout.items()
                          if any(c["id"] == cid for p in spec["pages"] if p["id"] == page for c in p["components"])), default=0)
            cid = _new_id("c", taken)
            ctitle = _text(ch.get("title"), 200) or ctype
            value = {"id": cid, "type": ctype, "title": ctitle, "measures": [mid] if mid else [], "fields": [ref] if ref else [],
                     "layout": {"x": 0, "y": bottom, "w": w, "h": h}, "props": {"text": ctitle} if ctype == "text" else {}}
            ops.append({"op": "add", "kind": "component", "page": page, "index": page_len[page], "value": value})
            page_len[page] += 1
            layout[cid] = value["layout"]
            done.append(f"Add a {ctype} {ctitle!r}")
        elif action == "add_page":
            title = _text(ch.get("title"), 120)
            if not title:
                left_out.append(f"{what}: no title given")
                continue
            pid = _new_id("pg", taken)
            ops.append({"op": "add", "kind": "page", "value": {"id": pid, "title": title, "components": []}})
            kinds[pid] = "page"
            page_len[pid] = 0
            done.append(f"Add page {title!r}")
        elif action == "rename_app":
            title = _text(ch.get("title"), 120)
            if not title:
                left_out.append(f"{what}: no name given")
                continue
            ops.append({"op": "set", "id": "app", "field": "name", "value": title})
            done.append(f"Rename the app to {title!r}")
        else:
            left_out.append(f"{what}: not a change App Studio makes")
    if not ops:
        raise specs.SpecError(["Nothing in the model's answer could be changed in this app.", *left_out[:10]])
    questions = [q for q in (_text(x, 300) for x in reply.get("questions") or [] if isinstance(x, str)) if q][:3]
    return {"ops": ops, "summary": _text(reply.get("summary"), 300) or "; ".join(done)[:300], "questions": questions,
            "done": done, "left_out": left_out}


def preview(spec: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """The operations applied to a copy: ``{"spec", "changed"}``. Raises SpecError when the result isn't valid."""
    done = patches.apply(spec, ops)
    return {"spec": done["spec"], "changed": done["changed"]}
