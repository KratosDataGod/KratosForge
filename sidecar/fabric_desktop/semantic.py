"""A Direct Lake semantic model over one layer's tables, written as TMDL.

Each entity in the layer becomes a table that reads its Delta table through the
lakehouse's SQL analytics endpoint (Direct Lake, no import or refresh schedule).
Relationships come from the metadata: a fact's ``[[lookup]]`` names the dimension and
the key column, and any other column named like a dimension's surrogate key joins
to it. Surrogate keys and the framework's own columns (``_run_id``, ``_loaded_at``) are
hidden. No column summarizes on its own (``summarizeBy: none``); each fact's visible numeric columns that
aren't keys get a total measure instead, which is what Best Practice Analyzer's rules ask for. Lineage tags are
derived from names, so updating the model keeps them stable.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

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
    return name if _PLAIN.match(name) else "'" + name.replace("'", "''") + "'"


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


def _unquote(name: str) -> str:
    return name[1:-1].replace("''", "'") if name.startswith("'") and name.endswith("'") else name


def table_names(parts: dict[str, str], entities: list[str]) -> dict[str, str]:
    """The name each entity's table has in an existing model, found by its lineage tag, which is derived
    from the entity (``tag('table', entity)``). ``parts`` are the model's TMDL files, keyed by path."""
    by_tag = {}
    for path, text in parts.items():
        if not path.startswith("definition/tables/"):
            continue
        # The table line can follow /// description lines, and other properties can come before its lineage
        # tag; the table's own properties are indented once, its columns' and measures' twice.
        head = re.search(r"^table (.+)$", text.replace("\r\n", "\n"), re.M)
        own = head and re.search(r"^\tlineageTag: (\S+)", text.replace("\r\n", "\n")[head.end():], re.M)
        if head and own:
            by_tag[own.group(1)] = _unquote(head.group(1).strip())
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
            if dim is t or not dim.is_dimension or dim.surrogate_key not in cols:
                continue
            if any(r.from_table == t.name and r.from_column == dim.surrogate_key for r in rels):
                continue
            rels.append(Relationship(t.name, dim.surrogate_key, dim.name, dim.surrogate_key,
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


def _about(text: str, indent: str) -> list[str]:
    """TMDL description lines (``///``), which go right before the object they describe."""
    return [f"{indent}/// {line}".rstrip() for line in str(text or "").strip().splitlines()] if str(text or "").strip() else []


def _table_tmdl(t: ModelTable, expression: str, about: dict[str, str] | None = None) -> str:
    """``about`` maps "" (the table) or a column's name to its description, from the catalog. Measures are
    described here: what they add up, then the column's own description."""
    about = about or {}
    lines = [*_about(about.get(""), ""), f"table {quote(t.name)}", f"\tlineageTag: {tag('table', t.entity)}"]
    date_key = t.date_column if any(c == t.date_column and tmdl_type(d) == "dateTime" for c, d in t.columns) else None
    if date_key:
        lines.append("\tdataCategory: Time")  # Mark as date table, with date_key as its key column
    lines.append("")
    if not t.is_dimension:
        lines += [*_about(f"Rows in {t.name}.", "\t"), f"\tmeasure {quote(t.name + ' rows')} = COUNTROWS({quote(t.name)})",
                  "\t\tformatString: #,0", f"\t\tlineageTag: {tag('measure', t.entity)}", ""]
    for col, dtype in _summed(t):
        lines += [*_about(f"Sum of {col}." + (f" {about[col]}" if about.get(col) else ""), "\t"),
                  f"\tmeasure {quote(f'{t.name} total {col}')} = SUM({quote(t.name)}[{col.replace(']', ']]')}])",
                  f"\t\tformatString: {'#,0' if dtype == 'int64' else '#,0.00'}",
                  f"\t\tlineageTag: {tag('measure', t.entity, col)}", ""]
    for col, delta_type in t.columns:
        dtype = tmdl_type(delta_type)
        if dtype is None:
            continue  # arrays, maps and structs, which Direct Lake can't read; reported by left_out
        lines += [*_about(about.get(col), "\t"), f"\tcolumn {quote(col)}"]
        lines.append(f"\t\tdataType: {dtype}")
        if _hidden(t, col):
            lines.append("\t\tisHidden")
        if col == date_key:
            lines.append("\t\tisKey")
        if dtype == "dateTime" and delta_type.lower() == "date":
            lines.append("\t\tformatString: Short Date")
        lines += [f"\t\tlineageTag: {tag('column', t.entity, col)}", "\t\tsummarizeBy: none",
                  f"\t\tsourceColumn: {col}", ""]
    lines += [f"\tpartition {quote(t.name)} = entity", "\t\tmode: directLake", "\t\tsource",
              f"\t\t\tentityName: {t.table}", f"\t\t\tschemaName: {t.schema}", f"\t\t\texpressionSource: {expression}", ""]
    return "\n".join(lines)


def build(tables: list[ModelTable], rels: list[Relationship], sql_server: str, sql_endpoint_id: str,
          descriptions: dict[str, dict[str, str]] | None = None) -> dict[str, str]:
    """TMDL definition parts, keyed by path, for Fabric's semantic model API. ``descriptions`` maps an entity
    to {"": its table's description, column: the column's} (model_dictionary.descriptions)."""
    expression = "DatabaseQuery"
    parts = {
        "definition.pbism": json.dumps({"version": "4.0", "settings": {}}, indent=2),
        "definition/database.tmdl": "database\n\tcompatibilityLevel: 1604\n",
        "definition/model.tmdl": "\n".join(
            ["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
             "\tsourceQueryCulture: en-US", ""]
            + [f"ref table {quote(t.name)}" for t in tables]) + "\n",
        "definition/expressions.tmdl": "\n".join([
            f"expression {expression} =", "\t\tlet",
            f'\t\t    database = Sql.Database("{sql_server}", "{sql_endpoint_id}")', "\t\tin", "\t\t    database",
            f"\tlineageTag: {tag('expression', sql_endpoint_id)}", ""]),
    }
    for t in tables:
        parts[f"definition/tables/{t.name}.tmdl"] = _table_tmdl(t, expression, (descriptions or {}).get(t.entity))
    if rels:
        lines = []
        for r in rels:
            lines += [f"relationship {tag('rel', r.from_table, r.from_column, r.to_table, r.to_column)}"]
            if not r.active:
                lines.append("\tisActive: false")
            lines += [f"\tfromColumn: {quote(r.from_table)}.{quote(r.from_column)}",
                      f"\ttoColumn: {quote(r.to_table)}.{quote(r.to_column)}", ""]
        parts["definition/relationships.tmdl"] = "\n".join(lines)
    return parts
