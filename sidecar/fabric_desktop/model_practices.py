"""Best practices for a layer's semantic model, checked on this PC from the metadata project.

The model is the one Update model writes (semantic.py): its tables, columns, measures and relationships come
from the project's entities, their types from each entity's contract, and descriptions from the catalog, as
the data dictionary shows them. Nothing is read from Fabric and no Fabric job runs. Findings have the shape
notebook_practices gives a notebook (rule_id, severity, rule, category, object_type, object, description, url).

The rules are the Best Practice Analyzer rules that need only the model's definition, from Microsoft's
BPARules.json (MIT), which Michael Kovalsky of Elegant BI co-wrote; rules that need VertiPaq statistics stay
with the check that runs in Fabric (model_bpa). ``settings`` maps a rule id to ``{"enabled": bool,
"severity": "Error"|"Warning"|"Info"}``, as a governance policy gives them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import model_dictionary, semantic

SEVERITIES = ("Error", "Warning", "Info")
URL = "semantic-model-rules.md"
SCD2_TYPES = {"valid_from": "dateTime", "valid_to": "dateTime", "is_current": "boolean", "row_hash": "string"}
_SPECIAL = re.compile(r"[\t\r\n]|^\s|\s$")


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    category: str
    name: str
    description: str


RULES: tuple[Rule, ...] = (
    Rule("SM001", "Error", "Error Prevention", "Relationship columns have the same type",
         "Both columns of a relationship need the same data type, or the relationship fails. Give the fact's "
         "key column the dimension key's type in its contract."),
    Rule("SM002", "Warning", "Performance", "Relationship keys are integers",
         "Joins on whole numbers are faster and smaller than joins on text or dates. Take keys from LH_Keys "
         "([[keys]]) or a [[lookup]], which issue integer surrogate keys."),
    Rule("SM003", "Warning", "Performance", "No floating point columns",
         "A double is approximate, so totals can differ in the last digits. Use decimal(p,s) in the contract "
         "for amounts."),
    Rule("SM004", "Info", "Formatting", "Foreign keys are hidden",
         "People filter by the dimension's columns, not a fact's key. Name the key like the dimension's "
         "surrogate key (ending _sk), which Update model hides."),
    Rule("SM005", "Info", "Maintenance", "Tables and visible columns have a description",
         "Descriptions show as tooltips in Power BI's field list. Add them on the entity's About card or with "
         "the data dictionary."),
    Rule("SM006", "Warning", "Maintenance", "Fact tables relate to a dimension",
         "A fact with no relationship can't be filtered by any dimension. Add a [[lookup]], or name a column "
         "like a dimension's surrogate key."),
    Rule("SM007", "Warning", "Naming Conventions", "Names have no tabs, line breaks or edge spaces",
         "Such names are hard to type in DAX and break some tools. Rename the column or table."),
)
_BY_ID = {r.id: r for r in RULES}


def rules() -> list[dict[str, Any]]:
    """Every semantic model rule, as a governance template lists them."""
    return [{"id": r.id, "kind": "model", "severity": r.severity, "category": r.category, "name": r.name,
             "description": r.description, "url": URL} for r in RULES]


def _known_type(e: Any, column: str, contract: str) -> str | None:
    """The column's type in the model, or None when the project doesn't say."""
    if contract:
        return semantic.tmdl_type(model_dictionary._delta_type(contract))
    if column == e.surrogate_key or column in e.key_columns or column in {lk.get("as") for lk in e.spec.get("lookup", [])}:
        return "int64"  # surrogate keys are whole numbers (LH_Keys, scd1 and scd2 dimensions)
    if e.mode == "scd2" and column in SCD2_TYPES:
        return SCD2_TYPES[column]
    return None


def model_item(project: Any, layer: str, saved: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The layer's model as Update model writes it, for the checkers: tables with their columns' types,
    hidden flags and descriptions, and the relationships with both columns' types."""
    tables, rels = semantic.plan(project, layer)
    entries = model_dictionary.entries(project, layer, saved)
    out_tables = []
    by_name: dict[str, dict[str, Any]] = {}
    for t in tables:
        e = project.entities[t.entity]
        mine = [x for x in entries if x["entity"] == t.entity]
        table = next(x for x in mine if x["kind"] == "table")
        cols = [{"name": x["name"], "type": _known_type(e, x["name"], x["type"]), "hidden": x["hidden"],
                 "description": x["description"]} for x in mine if x["kind"] == "column"]
        row = {"name": t.name, "entity": t.entity, "dimension": t.is_dimension, "description": table["description"],
               "columns": cols}
        out_tables.append(row)
        by_name[t.name] = row

    def col_type(table: str, column: str) -> str | None:
        return next((c["type"] for c in by_name[table]["columns"] if c["name"] == column), None)

    return {"kind": "model", "id": f"SM_{layer.title()}", "layer": layer, "tables": out_tables,
            "relationships": [{"from_table": r.from_table, "from_column": r.from_column, "to_table": r.to_table,
                               "to_column": r.to_column, "from_type": col_type(r.from_table, r.from_column),
                               "to_type": col_type(r.to_table, r.to_column), "active": r.active} for r in rels]}


def check(item: dict[str, Any], settings: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Findings for a model item (``model_item``), worst first."""
    settings = settings or {}
    found: list[dict[str, Any] | None] = []
    tables = {t["name"]: t for t in item.get("tables") or []}
    rels = item.get("relationships") or []
    for r in rels:
        a, b = r.get("from_type"), r.get("to_type")
        where = f"{r['from_table']}.{r['from_column']} to {r['to_table']}.{r['to_column']}"
        fact = tables.get(r["from_table"], {}).get("entity")
        if a and b and a != b:
            found.append(_finding("SM001", settings, "Relationship", where, f"{r['from_column']} is {a} and "
                                  f"{r['to_table']}.{r['to_column']} is {b}.", fact))
        wrong = sorted({x for x in (a, b) if x and x != "int64"})
        if wrong:
            found.append(_finding("SM002", settings, "Relationship", where, f"The key is {' and '.join(wrong)}.", fact))
        col = next((c for c in tables.get(r["from_table"], {}).get("columns", []) if c["name"] == r["from_column"]), None)
        if col and not col["hidden"]:
            found.append(_finding("SM004", settings, "Column", f"{r['from_table']}.{r['from_column']}",
                                  f"Joins to {r['to_table']} but people can see it.", fact))
    related = {r["from_table"] for r in rels}
    for t in tables.values():
        names = [t["name"]] + [c["name"] for c in t["columns"]]
        for n in names:
            if _SPECIAL.search(n):
                found.append(_finding("SM007", settings, "Table" if n == t["name"] else "Column",
                                      t["name"] if n == t["name"] else f"{t['name']}.{n!r}",
                                      f"{n!r} has a tab, a line break, or a space at the start or end.", t["entity"]))
        doubles = [c["name"] for c in t["columns"] if c["type"] == "double" and not c["name"].startswith("_")]
        if doubles:
            found.append(_finding("SM003", settings, "Table", t["name"], f"{_list(doubles)} {'is' if len(doubles) == 1 else 'are'} "
                                  "double.", t["entity"]))
        bare = [c["name"] for c in t["columns"] if not c["hidden"] and not c["description"]]
        if not t["description"] or bare:
            what = (["the table"] if not t["description"] else []) + ([f"{len(bare)} visible column{'s' if len(bare) != 1 else ''}"
                                                                       f" ({_list(bare)})"] if bare else [])
            found.append(_finding("SM005", settings, "Table", t["name"], f"No description for {' and '.join(what)}.",
                                  None, {"action": "open_model", "label": "Open the data dictionary", "layer": item.get("layer")}))
        if not t["dimension"] and t["name"] not in related:
            found.append(_finding("SM006", settings, "Table", t["name"], "Relates to no dimension.", t["entity"]))
    rows = [f for f in found if f is not None]
    rows.sort(key=lambda f: (SEVERITIES.index(f["severity"]), f["rule_id"], f["object"]))
    return {"counts": {s: sum(1 for f in rows if f["severity"] == s) for s in SEVERITIES}, "findings": rows}


def _list(names: list[str], most: int = 5) -> str:
    return ", ".join(names[:most]) + (f" and {len(names) - most} more" if len(names) > most else "")


def _finding(rule_id: str, settings: dict[str, dict[str, Any]], object_type: str, obj: str, detail: str,
             entity: str | None, fix: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rule = _BY_ID[rule_id]
    own = settings.get(rule_id)
    own = own if isinstance(own, dict) else {}
    if own.get("enabled") is False:
        return None
    severity = own.get("severity") if own.get("severity") in SEVERITIES else rule.severity
    fix = fix or ({"action": "open_entity", "label": "Open the entity", "entity": entity} if entity else None)
    return {"rule_id": rule_id, "severity": severity, "rule": rule.name, "category": rule.category,
            "object_type": object_type, "object": obj, "description": f"{detail} {rule.description}", "url": URL,
            **({"fix": fix} if fix else {})}
