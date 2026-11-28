"""A Direct Lake semantic model over one layer's tables, written as TMDL.

Each entity in the layer becomes a table that reads its Delta table through the
lakehouse's SQL analytics endpoint (Direct Lake, no import or refresh schedule).
Relationships come from the metadata: a fact's ``[[lookup]]`` names the dimension and
the key column, and any other column named like a dimension's surrogate key joins
to it. Surrogate keys and the framework's own columns (``_run_id``, ``_loaded_at``) are
hidden. No column summarizes on its own (``summarizeBy: none``); each fact's visible numeric columns that
aren't keys get a total measure instead, which is what Best Practice Analyzer's rules ask for. Lineage tags are
derived from names, so updating the model keeps them stable.

The model is built as TMSL (``model_bim``) and written by ``tmdl.from_bim``, the app's one TMDL writer, which
reads what it writes back and reports anything that would come back different.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import tmdl

_NS = uuid.UUID("2b0e1b9c-6a51-4f0e-9b4d-6b8f3f1d7c11")
_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NUMERIC = {"int64", "double", "decimal"}

# Delta type names (as delta-rs reports them) to TMDL data types. Binary isn't here: Direct Lake can't read it
# (https://learn.microsoft.com/fabric/fundamentals/direct-lake-overview), so left_out reports it instead.
_TYPES = {"string": "string", "long": "int64", "integer": "int64", "short": "int64", "byte": "int64",
          "double": "double", "float": "double", "boolean": "boolean", "date": "dateTime",
          "timestamp": "dateTime", "timestamp_ntz": "dateTime"}


def tmdl_type(delta_type: str) -> str | None:
    t = delta_type.lower()
    if t.startswith("decimal"):
        return "decimal"
    return _TYPES.get(t)


def left_out(t: "ModelTable") -> list[dict[str, str]]:
    """The table's columns the model leaves out because Direct Lake can't read their type (arrays,
    maps, structs, anything not in ``_TYPES``), so the person can be told instead of finding out later."""
    return [{"table": t.name, "column": col, "type": delta_type} for col, delta_type in t.columns
            if tmdl_type(delta_type) is None]


def quote(name: str) -> str:
    """A table name as DAX writes it in a measure: bare when it is a plain identifier, otherwise in single
    quotes. TMDL names are quoted by ``tmdl.quote``."""
    return name if _PLAIN.fullmatch(name) else "'" + name.replace("'", "''") + "'"


def tag(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "/".join(parts)))


@dataclass
class ModelTable:
    name: str             # the table's name in the model
    entity: str           # metadata entity, e.g. gold.dim_customer
    schema: str           # schema in the lakehouse
    table: str            # Delta table name in that schema
    columns: list[tuple[str, str]] = field(default_factory=list)  # (name, Delta type)
    keys: list[str] = field(default_factory=list)
    surrogate_key: str | None = None
    is_dimension: bool = False
    date_column: str | None = None  # marks the table as the model's date table, with this column as its key


@dataclass
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    active: bool = True
    why: str = ""


def table_names(parts: dict[str, str], entities: list[str]) -> dict[str, str]:
    """The name each entity's table has in an existing model, found by its lineage tag, which is derived
    from the entity (``tag('table', entity)``). ``parts`` are the model's TMDL files, keyed by path.

    Each table file is read on its own with ``tmdl.to_bim``, so the reader's 20 MB limit applies to one
    table's file, not the whole model; a file past it raises ValueError rather than guessing (a wrong guess
    would rename a table that reports use)."""
    by_tag: dict[str, str] = {}
    for path, text in parts.items():
        if not path.startswith("definition/tables/") or not path.endswith(".tmdl"):
            continue
        try:
            bim, _ = tmdl.to_bim({path: text})
        except ValueError as exc:  # narrow: too large or nested too deeply to read; the reason is kept
            raise ValueError(f"{path[:120]} couldn't be read ({exc}), so its table's name isn't known.") from None
        for t in bim["model"].get("tables") or []:
            if isinstance(t.get("name"), str) and isinstance(t.get("lineageTag"), str):
                by_tag.setdefault(t["lineageTag"], t["name"])
    return {e: by_tag[tag("table", e)] for e in entities if tag("table", e) in by_tag}


def plan(project: Any, layer: str, kept: dict[str, str] | None = None) -> tuple[list[ModelTable], list[Relationship]]:
    """The tables and relationships of a model over ``layer``; columns are filled in later.

    ``kept`` maps an entity to the name its table already has in the model (``table_names``). Those names
    stay, so a new entity whose Delta table shares a name in another schema never renames a table that
    reports already use (with its measures, which are named after it); only the newcomer is qualified."""
    entities = [e for e in project.entities.values() if e.layer == layer]
    kept = {e: n for e, n in (kept or {}).items() if list((kept or {}).values()).count(n) == 1}
    taken = set(kept.values())
    tables: list[ModelTable] = []
    for e in sorted(entities, key=lambda x: x.name):
        if e.name in kept:
            name = kept[e.name]
        else:
            clash = sum(1 for x in entities if x.table == e.table) > 1
            name = f"{e.schema}_{e.table}" if clash or not e.table or e.table in taken else e.table
        taken.add(name)
        tables.append(ModelTable(name=name, entity=e.name, schema=e.schema, table=e.table, keys=list(e.keys),
                                 surrogate_key=e.surrogate_key,
                                 is_dimension=e.mode in ("scd1", "scd2") and bool(e.surrogate_key),
                                 date_column=e.date_column))
    by_entity = {t.entity: t for t in tables}
    rels: list[Relationship] = []
    for t in tables:
        e = project.entities[t.entity]
        for lk in e.spec.get("lookup", []):
            dim = by_entity.get(lk.get("dim"))
            if dim and dim.surrogate_key and lk.get("as"):
                rels.append(Relationship(t.name, lk["as"], dim.name, dim.surrogate_key,
                                         why=f"[[lookup]] of {dim.entity}"))
    for t in tables:
        cols = set(project.entities[t.entity].columns)
        for dim in tables:
            sk = dim.surrogate_key
            if dim is t or not dim.is_dimension or sk is None or sk not in cols:
                continue
            if any(r.from_table == t.name and r.from_column == sk for r in rels):
                continue
            rels.append(Relationship(t.name, sk, dim.name, sk,
                                     why=f"same name as {dim.entity}'s surrogate key"))
    # Power BI allows one active relationship between two tables.
    seen: set[tuple[str, str]] = set()
    for r in rels:
        pair = (r.from_table, r.to_table)
        r.active = pair not in seen
        seen.add(pair)
    return tables, rels


def _hidden(t: ModelTable, col: str) -> bool:
    return col.startswith("_") or col.endswith("_sk") or col == t.surrogate_key


def _summed(t: ModelTable) -> list[tuple[str, str]]:
    """A fact's visible numeric columns that aren't keys: each gets an explicit total measure, since every
    column has ``summarizeBy: none`` (Best Practice Analyzer's NUMERIC_COLUMN_SUMMARIZE_BY, an error-level rule).
    The measure is named after its table and column, like the row count measure, so the name stays the same as the
    model grows: reports bind measures by name, and a name can't clash with another table's."""
    if t.is_dimension:
        return []
    out = []
    for col, delta_type in t.columns:
        dtype = tmdl_type(delta_type)
        keyish = col in t.keys or col.endswith(("_sk", "_id", "_key")) or col == t.surrogate_key
        if dtype in _NUMERIC and not keyish and not _hidden(t, col):
            out.append((col, dtype))
    return out


def _about(text: str | None) -> str | None:
    """A description as TMDL ``///`` lines keep it: each line trimmed, and none when it is blank."""
    lines = [line.strip() for line in str(text or "").strip().splitlines()]
    return "\n".join(lines) or None


def _set(obj: dict[str, Any], **props: Any) -> dict[str, Any]:
    """``obj`` with the properties that have a value."""
    obj.update({k: v for k, v in props.items() if v is not None})
    return obj


def _table(t: ModelTable, expression: str, about: dict[str, str] | None = None) -> dict[str, Any]:
    """One table as TMSL. ``about`` maps "" (the table) or a column's name to its description, from the catalog.
    Measures are described here: what they add up, then the column's own description."""
    about = about or {}
    date_key = t.date_column if any(c == t.date_column and tmdl_type(d) == "dateTime" for c, d in t.columns) else None
    table = _set({"name": t.name}, description=_about(about.get("")), lineageTag=tag("table", t.entity),
                 dataCategory="Time" if date_key else None)  # a date table, with date_key as its key column
    measures = []
    if not t.is_dimension:
        measures.append(_set({"name": f"{t.name} rows"}, description=_about(f"Rows in {t.name}."),
                             expression=f"COUNTROWS({quote(t.name)})", formatString="#,0",
                             lineageTag=tag("measure", t.entity)))
    for col, summed_type in _summed(t):
        measures.append(_set({"name": f"{t.name} total {col}"},
                             description=_about(f"Sum of {col}." + (f" {about[col]}" if about.get(col) else "")),
                             expression=f"SUM({quote(t.name)}[{col.replace(']', ']]')}])",
                             formatString="#,0" if summed_type == "int64" else "#,0.00",
                             lineageTag=tag("measure", t.entity, col)))
    columns = []
    for col, delta_type in t.columns:
        dtype = tmdl_type(delta_type)
        if dtype is None:
            continue  # arrays, maps and structs, which Direct Lake can't read; reported by left_out
        columns.append(_set({"name": col}, description=_about(about.get(col)), dataType=dtype,
                            isHidden=True if _hidden(t, col) else None, isKey=True if col == date_key else None,
                            formatString="Short Date" if dtype == "dateTime" and delta_type.lower() == "date" else None,
                            lineageTag=tag("column", t.entity, col), summarizeBy="none", sourceColumn=col))
    # Measures before columns, as this model's files have always listed them.
    table.update({k: v for k, v in (("measures", measures), ("columns", columns)) if v})
    table["partitions"] = [{"name": t.name, "mode": "directLake",
                            "source": {"type": "entity", "entityName": t.table, "schemaName": t.schema,
                                       "expressionSource": expression}}]
    return table


def _m_text(value: str) -> str:
    """``value`` inside a Power Query M text literal: a double quote is written twice, so the text can't end early."""
    return value.replace('"', '""')


def model_bim(tables: list[ModelTable], rels: list[Relationship], sql_server: str, sql_endpoint_id: str,
              descriptions: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """The Direct Lake model as a TMSL ``model.bim`` document, which ``build`` writes as TMDL with
    ``tmdl.from_bim``. ``descriptions`` maps an entity to {"": its table's description, column: the column's}
    (model_dictionary.descriptions)."""
    expression = "DatabaseQuery"
    model: dict[str, Any] = {
        "culture": "en-US", "defaultPowerBIDataSourceVersion": "powerBI_V3", "sourceQueryCulture": "en-US",
        "expressions": [{"name": expression, "kind": "m",
                         "expression": f'let\n    database = Sql.Database("{_m_text(sql_server)}", "{_m_text(sql_endpoint_id)}")\n'
                                       "in\n    database",
                         "lineageTag": tag("expression", sql_endpoint_id)}]}
    if tables:  # an empty list would read back as no list at all
        model["tables"] = [_table(t, expression, (descriptions or {}).get(t.entity)) for t in tables]
    if rels:
        model["relationships"] = [_set({"name": tag("rel", r.from_table, r.from_column, r.to_table, r.to_column)},
                                       isActive=None if r.active else False, fromTable=r.from_table,
                                       fromColumn=r.from_column, toTable=r.to_table, toColumn=r.to_column)
                                  for r in rels]
    return {"compatibilityLevel": 1604, "model": model}


def build(tables: list[ModelTable], rels: list[Relationship], sql_server: str, sql_endpoint_id: str,
          descriptions: dict[str, dict[str, str]] | None = None) -> dict[str, str]:
    """TMDL definition parts, keyed by path, for Fabric's semantic model API: ``model_bim`` written by
    ``tmdl.from_bim``, which reads its own output back. Anything that wouldn't come back as built raises
    ValueError, so a model that would differ from the plan is never sent."""
    files, report = tmdl.from_bim(model_bim(tables, rels, sql_server, sql_endpoint_id, descriptions))
    if report:
        lost = ", ".join(str(r.get("object"))[:80] for r in report[:3])
        raise ValueError(f"The model wouldn't read back from TMDL as built ({lost}), so it wasn't written.")
    return {"definition.pbism": json.dumps({"version": "4.0", "settings": {}}, indent=2), **files}
