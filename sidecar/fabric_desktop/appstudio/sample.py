"""App Studio local preview data (AS-08): made-up numbers for a specification, made on this PC.

The preview shows the layout, the components and how a slicer narrows the others, before any source is
connected and without reading OneLake, a semantic model or a model. Every number is a stable function of the
component, the measure and the category, so a preview looks the same each time it opens. The result says it
is ``simulated``; the page labels it as sample data and never as the app's real figures.
"""

from __future__ import annotations

import hashlib
from typing import Any

from . import spec as specs

NOTE = "Sample numbers made on this PC. They show the layout and how a slicer narrows the page, not your data."
_CATEGORIES = {
    "region": ["North", "South", "East", "West", "Central"],
    "month": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "year": ["2022", "2023", "2024", "2025", "2026"],
    "category": ["Bikes", "Clothing", "Accessories", "Components"],
    "site": ["Leeds", "Lyon", "Tampa", "Osaka", "Pune"],
}
_MAX_ROWS = 12


def _column(field: str) -> str:
    return field[field.rindex("[") + 1:-1]


def categories(field: str) -> list[str]:
    col = _column(field)
    for text in (col.lower(), field.lower()):  # the column's own name first, then its table's
        for key, values in _CATEGORIES.items():
            if key in text:
                return list(values)
    return [f"{col} {i}" for i in range(1, 6)]


def _number(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return 1_000 + int.from_bytes(digest[:4], "big") % 99_000


def _factor(selection: dict[str, str]) -> float:
    """How much a slicer choice narrows a component that doesn't show the sliced field."""
    if not selection:
        return 1.0
    key = "|".join(f"{k}={v}" for k, v in sorted(selection.items()))
    return 0.15 + (_number(key) % 25) / 100


def _component(c: dict[str, Any], names: dict[str, str], selection: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {k: c.get(k) for k in ("id", "type", "title", "layout")}
    measures = [m for m in c.get("measures") or [] if m in names]
    fields = c.get("fields") or []
    field = fields[0] if fields else ""
    labels = categories(field) if field else []
    chosen = selection.get(field) if field else None
    if c["type"] == "text":
        out["text"] = str((c.get("props") or {}).get("text") or c.get("title") or "")
        return out
    if c["type"] == "slicer":
        out.update(field=field, values=labels, selected=chosen if chosen in labels else None)
        return out
    narrowed = {k: v for k, v in selection.items() if k != field}
    factor = _factor(narrowed)
    if chosen in labels:
        labels = [chosen]
    if c["type"] == "card" or not labels:
        base = categories(field) if field else [""]
        total = sum(_number(c["id"], m, label) for m in measures[:1] for label in (labels or base))
        if not labels and selection:
            total = round(total * _factor(selection))
        out.update(value=round(total * factor) if labels else total, measure=names.get(measures[0]) if measures else "")
        return out
    series: list[dict[str, Any]] = [{"name": names[m], "values": [round(_number(c["id"], m, label) * factor) for label in labels]}
              for m in measures]
    if (c.get("props") or {}).get("sort") == "desc" and series:
        first: list[int] = series[0]["values"]
        order = sorted(range(len(labels)), key=lambda i: -first[i])
        labels = [labels[i] for i in order]
        for s in series:
            s["values"] = [s["values"][i] for i in order]
    out.update(field=field, labels=labels[:_MAX_ROWS], series=[{**s, "values": s["values"][:_MAX_ROWS]} for s in series])
    return out


def preview(spec: dict[str, Any], page_id: str = "", selection: dict[str, Any] | None = None) -> dict[str, Any]:
    """The components of one page (the first when ``page_id`` is empty) with sample values.

    ``selection`` maps a slicer's ``Table[Column]`` to the chosen category: a component that shows that field
    keeps only that category, and every other number on the page shrinks, as a filter would."""
    specs.check(spec)
    page = next((p for p in spec["pages"] if p["id"] == page_id), spec["pages"][0])
    chosen = {str(k): str(v) for k, v in (selection or {}).items()
              if isinstance(k, str) and specs.FIELD.fullmatch(k) and isinstance(v, str) and v in categories(k)}
    names = {m["id"]: m["name"] for m in spec["measures"]}
    return {"page": {"id": page["id"], "title": page["title"]}, "simulated": True, "note": NOTE, "selection": chosen,
            "components": [_component(c, names, chosen) for c in page["components"]]}
