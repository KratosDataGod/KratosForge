"""Patch operations on the specification (AS-03): the one way anything changes it.

A conversational edit ("change the sales chart to a waterfall") and a visual edit (dragging a chart)
both become a list of these, so they share undo, redo and history. Each operation names what it changes
by id, so everything else in the specification stays exactly as it was::

    {"op": "add",    "kind": "component", "page": "pg_overview", "index": 0, "value": {...}}
    {"op": "remove", "id": "c_rev"}
    {"op": "set",    "id": "c_rev", "field": "type", "value": "waterfall"}
    {"op": "set",    "id": "c_rev", "field": "props.color"}          (no value: the field is removed)
    {"op": "move",   "id": "c_rev", "page": "pg_detail", "index": 2}

``apply`` is all or nothing: it works on a copy, validates the result and returns it with the inverse
operations, which undo it exactly. Nothing is applied when any operation or the result is invalid.
"""

from __future__ import annotations

from typing import Any

from . import spec as specs
from .spec import SpecError

MAX_OPS = 200
MAX_FIELD_DEPTH = 4
_KIND_KEY = {kind: key for key, kind in specs.COLLECTIONS}
_SENTINEL = object()


def _field_path(entry_kind: str, field: Any) -> list[str]:
    if not isinstance(field, str) or not field:
        raise SpecError(["set needs a field."])
    parts = field.split(".")
    if len(parts) > MAX_FIELD_DEPTH or not all(specs.ID.fullmatch(p) or p.isidentifier() for p in parts):
        raise SpecError([f"set field {field[:80]!r} isn't a field path."])
    if parts[0] in ("id", "format", "components") or (entry_kind == "page" and parts[0] == "components"):
        raise SpecError([f"{parts[0]} can't be set; use add, remove or move."])
    if entry_kind == "app" and parts[0] not in specs.APP_FIELDS:
        raise SpecError([f"The application has no field {parts[0]!r} to set."])
    return parts


def _get(entry: dict[str, Any], parts: list[str]) -> Any:
    here: Any = entry
    for part in parts:
        if not isinstance(here, dict) or part not in here:
            return _SENTINEL
        here = here[part]
    return here


def _put(entry: dict[str, Any], parts: list[str], value: Any) -> None:
    here = entry
    for part in parts[:-1]:
        nxt = here.get(part)
        if nxt is None:
            nxt = here[part] = {}
        if not isinstance(nxt, dict):
            raise SpecError([f"{'.'.join(parts)} goes through a value that isn't an object."])
        here = nxt
    if value is _SENTINEL:
        here.pop(parts[-1], None)
    else:
        here[parts[-1]] = value


def _index(value: Any, length: int) -> int:
    if value is None:
        return length
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= length:
        raise SpecError([f"index must be between 0 and {length}."])
    return value


def _page(doc: dict[str, Any], page_id: Any) -> dict[str, Any]:
    try:
        kind, entry, _ = specs.find(doc, str(page_id))
    except KeyError:
        kind, entry = "", {}
    if kind != "page":
        raise SpecError([f"{str(page_id)[:64]!r} isn't a page in this application."])
    return entry


def _one(doc: dict[str, Any], op: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Apply one operation to ``doc`` in place; returns its inverse and the id it touched."""
    name = op.get("op")
    if name == "add":
        kind = op.get("kind")
        value = op.get("value")
        if kind not in _KIND_KEY and kind != "component":
            raise SpecError([f"add kind must be one of: component, {', '.join(sorted(_KIND_KEY))}."])
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise SpecError(["add needs a value object with an id."])
        try:
            specs.find(doc, value["id"])
            raise SpecError([f"id {value['id'][:64]!r} is already used."])
        except KeyError:
            pass
        if kind == "component":
            target = _page(doc, op.get("page"))["components"]
            inverse_page = op.get("page")
        else:
            target = doc[_KIND_KEY[kind]]
            inverse_page = None
        target.insert(_index(op.get("index"), len(target)), specs.copy_of(value))
        inv = {"op": "remove", "id": value["id"]}
        return inv | ({"page": inverse_page} if inverse_page else {}), value["id"]

    id_ = op.get("id")
    if not isinstance(id_, str):
        raise SpecError([f"{name or 'The operation'} needs an id."])
    try:
        kind, entry, holder = specs.find(doc, id_)
    except KeyError:
        raise SpecError([f"There's nothing with id {id_[:64]!r} in this application."]) from None

    if name == "remove":
        if holder is None:
            raise SpecError(["The application itself can't be removed."])
        users = specs.dependents(doc, id_)
        if kind == "page":
            inner = {c["id"] for c in entry.get("components") or []}
            users += [u for c in inner for u in specs.dependents(doc, c) if u not in inner]
        if users:
            raise SpecError([f"{id_} is used by {', '.join(dict.fromkeys(users))}; change or remove those first."])
        index = holder.index(entry)
        holder.pop(index)
        inv = {"op": "add", "kind": kind, "index": index, "value": entry}
        if kind == "component":
            inv["page"] = op.get("page") or _owner(doc, holder)
        return inv, id_

    if name == "set":
        parts = _field_path(kind, op.get("field"))
        old = _get(entry, parts)
        _put(entry, parts, specs.copy_of(op["value"]) if "value" in op else _SENTINEL)
        inv = {"op": "set", "id": id_, "field": op["field"]}
        if old is not _SENTINEL:
            inv["value"] = old
        return inv, id_

    if name == "move":
        if kind not in ("component", "page", "measure", "source", "filter", "interaction") or holder is None:
            raise SpecError(["Only entries in a list can be moved."])
        old_index = holder.index(entry)
        old_page = _owner(doc, holder) if kind == "component" else None
        holder.pop(old_index)
        dest = _page(doc, op.get("page"))["components"] if kind == "component" and op.get("page") else holder
        try:
            dest.insert(_index(op.get("index"), len(dest)), entry)
        except SpecError:
            holder.insert(old_index, entry)
            raise
        inv = {"op": "move", "id": id_, "index": old_index}
        if old_page:
            inv["page"] = old_page
        return inv, id_

    raise SpecError([f"Unknown operation {str(name)[:20]!r}; use add, remove, set or move."])


def _owner(doc: dict[str, Any], holder: list[dict[str, Any]]) -> str:
    for page in doc["pages"]:
        if page["components"] is holder:
            return str(page["id"])
    raise SpecError(["That component isn't on a page."])


def apply(spec: dict[str, Any], ops: Any) -> dict[str, Any]:
    """Apply ``ops`` to a copy of ``spec``. Returns ``{"spec", "inverse", "changed"}``: the new valid
    specification, the operations that undo it (apply them in order) and the ids touched. ``spec`` itself
    is never changed; SpecError lists why nothing was applied."""
    if not isinstance(ops, list) or not ops or not all(isinstance(o, dict) for o in ops):
        raise SpecError(["A change is a non-empty list of operations."])
    if len(ops) > MAX_OPS:
        raise SpecError([f"A change has at most {MAX_OPS} operations."])
    doc = specs.copy_of(spec)
    inverse: list[dict[str, Any]] = []
    changed: list[str] = []
    for i, op in enumerate(ops):
        try:
            inv, touched = _one(doc, op)
        except SpecError as exc:
            raise SpecError([f"Operation {i + 1}: {m}" for m in exc.problems]) from None
        inverse.insert(0, inv)
        if touched not in changed:
            changed.append(touched)
    specs.check(doc)
    return {"spec": doc, "inverse": inverse, "changed": changed}
