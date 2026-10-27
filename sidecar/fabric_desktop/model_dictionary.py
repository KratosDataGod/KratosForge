"""The data dictionary of a layer's semantic model: every table, column and measure with its description.

Descriptions and classifications come from the catalog (what the About card saved, else the starter text in
the entity's [catalog] section, else the entity's own description). Measures are the ones semantic.py writes,
described for you. The dictionary goes out as a CSV people edit in Excel and comes back with a preview of each
change; only table and column descriptions and classifications are taken from it.

The idea is Elegant BI's data dictionary and description scripts (https://github.com/m-kovalsky/Tabular, MIT),
written for the metadata project rather than a model in Tabular Editor.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from . import semantic
from .meta import mesh

HEADER = ["Table", "Column or measure", "Kind", "Type", "Description", "Classification", "Hidden", "Entity"]
EDITABLE = ("description", "classification")
MAX_CHARS = 5_000_000  # a dictionary CSV is a few hundred KB; past this it isn't one
FORMULA = ("=", "+", "-", "@", "\t", "\r")  # Excel runs a cell starting with one of these as a formula

# Contract types (metadata guide) to the Delta type names semantic.py reads.
_DELTA = {"string": "string", "int64": "long", "int32": "integer", "float64": "double", "bool": "boolean",
          "date": "date", "datetime": "timestamp"}


def delta_type(contract_type: str) -> str:
    t = str(contract_type or "").lower()
    return "decimal" if t.startswith("decimal") else _DELTA.get(t, "string")


def entries(project: Any, layer: str, saved: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per table, column and measure of the layer's model, in model order."""
    tables, _ = semantic.plan(project, layer)
    out: list[dict[str, Any]] = []
    for t in tables:
        e = project.entities[t.entity]
        cat = mesh.merged_catalog(project, e, saved)
        t.columns = [(c["name"], delta_type(c["type"] or e.columns.get(c["name"], ""))) for c in cat["columns"]]
        out.append({"table": t.name, "name": "", "kind": "table", "type": "", "entity": t.entity, "hidden": False,
                    "description": cat["description"] or str(e.spec.get("description") or ""),
                    "classification": cat["classification"]})
        if not t.is_dimension:
            out.append(_measure(t, f"{t.name} rows", f"Rows in {t.name}."))
        described = {c["name"]: c["description"] for c in cat["columns"]}
        for col, dtype in semantic._summed(t):
            about = f" {described[col]}" if described.get(col) else ""
            out.append(_measure(t, f"{t.name} total {col}", f"Sum of {col}.{about}"))
        for c in cat["columns"]:
            out.append({"table": t.name, "name": c["name"], "kind": "column", "type": c["type"] or "",
                        "entity": t.entity, "hidden": semantic._hidden(t, c["name"]),
                        "description": c["description"], "classification": c["classification"]})
    return out


def _measure(t: semantic.ModelTable, name: str, text: str) -> dict[str, Any]:
    return {"table": t.name, "name": name, "kind": "measure", "type": "", "entity": t.entity, "hidden": False,
            "description": text + " Written by KratosForge.", "classification": ""}


def descriptions(items: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """What semantic.build writes into the model: {entity: {"": its table's, column: the column's}}. Measures
    are left out: semantic.py describes them from the columns they add up."""
    out: dict[str, dict[str, str]] = {}
    for x in items:
        if x["description"] and x["kind"] in ("table", "column"):
            out.setdefault(x["entity"], {})[x["name"]] = x["description"]
    return out


def counts(items: list[dict[str, Any]]) -> dict[str, int]:
    cols = [x for x in items if x["kind"] == "column"]
    tables = [x for x in items if x["kind"] == "table"]
    return {"tables": len(tables), "tables_described": sum(1 for x in tables if x["description"]),
            "columns": len(cols), "columns_described": sum(1 for x in cols if x["description"]),
            "measures": sum(1 for x in items if x["kind"] == "measure")}


def _cell(value: str) -> str:
    """A cell Excel shows as text: a leading ' keeps =HYPERLINK(...) and the like from running."""
    return f"'{value}" if value.startswith(FORMULA) else value


def _uncell(value: str) -> str:
    """The text _cell wrote, without the ' it added."""
    return value[1:] if value.startswith("'") and value[1:].startswith(FORMULA) else value


def to_csv(items: list[dict[str, Any]]) -> str:
    """The dictionary as CSV. Excel opens it straight away; the Entity column is how Import finds each row."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(HEADER)
    for x in items:
        w.writerow([_cell(v) for v in (x["table"], x["name"] or "(table)", x["kind"].capitalize(), x["type"],
                    x["description"], x["classification"].capitalize(), "Yes" if x["hidden"] else "", x["entity"])])
    return buf.getvalue()


def read_csv(text: str) -> list[dict[str, str]]:
    """The rows of an edited dictionary. Excel may add a byte order mark or save with semicolons."""
    if len(text) > MAX_CHARS:
        raise ValueError("The file is too large to be a data dictionary.")
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    head = next(reader, None)
    if not head or [h.strip().lower() for h in head[:len(HEADER)]] != [h.lower() for h in HEADER]:
        raise ValueError(f"This isn't a data dictionary from KratosForge: the first row must be {', '.join(HEADER)}.")
    rows = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        rows.append(dict(zip([h.lower() for h in HEADER], [_uncell(v.strip()) for v in values] + [""] * len(HEADER))))
    return rows


def plan_import(items: list[dict[str, Any]], rows: list[dict[str, str]]) -> dict[str, Any]:
    """What importing ``rows`` would change. A blank cell leaves the value as it is, so a row can't erase text;
    measure rows are skipped, since their descriptions are written for you."""
    by_key = {(x["entity"], x["name"]): x for x in items if x["kind"] in ("table", "column")}
    changes, skipped, unchanged = [], [], 0
    seen: set[tuple[str, str]] = set()
    for n, r in enumerate(rows, start=2):  # row 1 is the header, as Excel numbers them
        kind = r.get("kind", "").lower()
        if kind == "measure":
            continue
        name = "" if kind == "table" or r.get("column or measure") == "(table)" else r.get("column or measure", "")
        key = (r.get("entity", ""), name)
        label = f"{r.get('table', '')}.{name}" if name else r.get("table", "")
        x = by_key.get(key)
        if x is None:
            skipped.append({"row": n, "object": label or f"row {n}",
                            "reason": f"{name or 'This table'} isn't in {r.get('entity') or 'the model'} any more"
                            if r.get("entity") else "The Entity column is empty"})
            continue
        if key in seen:
            skipped.append({"row": n, "object": label, "reason": "Listed twice; the first row counts"})
            continue
        seen.add(key)
        new_class = r.get("classification", "").lower()
        if new_class and new_class not in mesh.CLASSIFICATIONS:
            skipped.append({"row": n, "object": label, "reason": f"Classification must be one of "
                            f"{', '.join(c.capitalize() for c in mesh.CLASSIFICATIONS)}"})
            new_class = ""
        changed = False
        for field, new in (("description", r.get("description", "")), ("classification", new_class)):
            if new and new != x[field]:
                changes.append({"entity": x["entity"], "column": x["name"], "object": label, "field": field,
                                "now": x[field], "new": new})
                changed = True
        unchanged += not changed
    return {"changes": changes, "skipped": skipped, "unchanged": unchanged}
