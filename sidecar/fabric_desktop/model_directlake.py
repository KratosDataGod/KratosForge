"""Direct Lake check: what would make a semantic model fall back to DirectQuery, or fail, before people query it.

Pure checks over a model's definition (a parsed model.bim) and its Delta tables' numbers, which
model_directlake_service.py reads. Each finding has a severity:

- ``fail``: queries or refresh fail (a column or table the Delta table no longer has, a relationship key that
  isn't unique, a type Direct Lake can't read).
- ``warn``: queries fall back to DirectQuery or slow down (a table past the capacity's Direct Lake guardrails),
  or are about to (90% of a limit).
- ``info``: worth knowing (a Delta column the model doesn't have yet, one it leaves out on purpose).

The guardrails are Microsoft's, per capacity SKU:
https://learn.microsoft.com/fabric/fundamentals/direct-lake-overview#fabric-capacity-guardrails-and-limitations
The idea of checking them from a notebook comes from semantic-link-labs (MIT,
https://github.com/microsoft/semantic-link-labs: get_direct_lake_guardrails, direct_lake_schema_compare); this
is written for this PC and imports nothing from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import semantic

NEAR = 0.9  # past this share of a limit is "near a limit"

# SKU: (Parquet files per table, row groups per table, rows per table, max model size on OneLake in bytes or None)
_GB = 1024 ** 3
_SMALL = (1_000, 1_000, 300_000_000)
GUARDRAILS: dict[str, tuple[int, int, int, int | None]] = {
    "F2": (*_SMALL, 10 * _GB), "F4": (*_SMALL, 10 * _GB), "F8": (*_SMALL, 10 * _GB),
    "F16": (*_SMALL, 20 * _GB), "F32": (*_SMALL, 40 * _GB),
    "F64": (5_000, 5_000, 1_500_000_000, None), "F128": (5_000, 5_000, 3_000_000_000, None),
    "F256": (5_000, 5_000, 6_000_000_000, None), "F512": (10_000, 10_000, 12_000_000_000, None),
    "F1024": (10_000, 10_000, 24_000_000_000, None), "F2048": (10_000, 10_000, 24_000_000_000, None),
    "F4096": (10_000, 10_000, 24_000_000_000, None), "F8192": (10_000, 10_000, 24_000_000_000, None),
}
# Premium and trial capacities have the guardrails of the F SKU with the same size.
ALIASES = {"FT1": "F64", "P1": "F64", "P2": "F128", "P3": "F256", "P4": "F512", "P5": "F1024"}
SKUS = list(GUARDRAILS) + list(ALIASES)

# TMSL data types to the TMDL type names semantic.tmdl_type gives a Delta type.
_TMSL = {"string": "string", "int64": "int64", "double": "double", "decimal": "decimal", "boolean": "boolean",
         "dateTime": "dateTime", "binary": "binary"}
_SQL_SOURCE = re.compile(r'Sql\.Database\s*\(\s*"[^"]*"\s*,\s*"([^"]+)"', re.I)
_ONELAKE_SOURCE = re.compile(r'AzureStorage\.DataLake\s*\(\s*"https://[^/"]+/([0-9a-fA-F-]{36})/([0-9a-fA-F-]{36})', re.I)


def limits(sku: str) -> dict[str, Any] | None:
    """The Direct Lake guardrails of a capacity SKU, or None for one this app doesn't know."""
    key = str(sku or "").strip().upper()
    key = ALIASES.get(key, key)
    if key not in GUARDRAILS:
        return None
    files, row_groups, rows, size = GUARDRAILS[key]
    return {"sku": key, "files": files, "row_groups": row_groups, "rows": rows, "model_bytes": size}


@dataclass
class ModelTable:
    name: str
    schema: str              # "" for a lakehouse without schemas
    entity: str              # the Delta table's name
    expression: str          # the shared expression the partition reads through
    columns: list[dict[str, Any]] = field(default_factory=list)  # {name, source, type, calculated}


@dataclass
class TableStats:
    """A Delta table's numbers from its log, and row groups from its Parquet footers."""
    columns: list[tuple[str, str]]   # (name, Delta type)
    rows: int | None
    files: int
    bytes: int
    row_groups: int | None
    row_groups_estimated: bool = False


def source(bim: dict[str, Any], expression: str) -> dict[str, str] | None:
    """Where a shared expression reads: ``{"kind": "sql", "endpoint": id}`` for Direct Lake on SQL,
    ``{"kind": "onelake", "workspace": id, "item": id}`` for Direct Lake on OneLake, None when it's neither."""
    for e in (bim.get("model") or {}).get("expressions") or []:
        if e.get("name") != expression:
            continue
        text = e.get("expression")
        text = "\n".join(text) if isinstance(text, list) else str(text or "")
        if m := _SQL_SOURCE.search(text):
            return {"kind": "sql", "endpoint": m.group(1).lower()}
        if m := _ONELAKE_SOURCE.search(text):
            return {"kind": "onelake", "workspace": m.group(1).lower(), "item": m.group(2).lower()}
    return None


def direct_lake_tables(bim: dict[str, Any]) -> list[ModelTable]:
    """The model's tables that have a Direct Lake partition, with the columns each reads."""
    out = []
    for t in (bim.get("model") or {}).get("tables") or []:
        part = next((p for p in t.get("partitions") or [] if str(p.get("mode", "")).lower() == "directlake"), None)
        src = (part or {}).get("source") or {}
        if not part or src.get("type") != "entity" or not src.get("entityName"):
            continue
        cols = [{"name": c.get("name", ""), "source": c.get("sourceColumn") or "", "type": c.get("dataType", ""),
                 "calculated": c.get("type") in ("calculated", "calculatedTableColumn")}
                for c in t.get("columns") or [] if c.get("type") != "rowNumber"]
        out.append(ModelTable(name=t.get("name", ""), schema=src.get("schemaName") or "", entity=src["entityName"],
                              expression=src.get("expressionSource") or "", columns=cols))
    return out


def fallback_behavior(bim: dict[str, Any]) -> str:
    """The model's Direct Lake behavior: Automatic falls back to DirectQuery, DirectLakeOnly fails instead."""
    return str((bim.get("model") or {}).get("directLakeBehavior") or "automatic")


def relationships(bim: dict[str, Any]) -> list[dict[str, str]]:
    return [{"from_table": r.get("fromTable", ""), "from_column": r.get("fromColumn", ""),
             "to_table": r.get("toTable", ""), "to_column": r.get("toColumn", "")}
            for r in (bim.get("model") or {}).get("relationships") or []
            if r.get("fromTable") and r.get("toTable")]


def _n(x: int) -> str:
    return f"{x:,}"


def _finding(severity: str, table: str, check: str, message: str, fix: str | None = None,
             column: str = "") -> dict[str, Any]:
    return {"severity": severity, "table": table, "check": check, "message": message, "fix": fix, "column": column}


def _guardrail(t: ModelTable, label: str, noun: str, have: int | None, limit: int, sku: str, past: str,
               about: bool = False) -> dict[str, Any] | None:
    if have is None:
        return None
    shown = ("about " if about else "") + _n(have)
    fix = "compact" if noun != "rows" else None  # compacting cuts files and row groups; rows only shrink by filtering
    if have > limit:
        return _finding("warn", t.name, label, f"{shown} {noun}; {sku} allows {_n(limit)} a table. {past}", fix)
    if have >= limit * NEAR:
        return _finding("warn", t.name, label,
                        f"{shown} {noun}; {sku} allows {_n(limit)} a table, so a few more loads will pass it.", fix)
    return None


def check(bim: dict[str, Any], stats: dict[str, TableStats | None], lim: dict[str, Any] | None,
          duplicates: dict[str, int] | None = None, ours: bool = False,
          unmatched: dict[str, dict[str, int]] | None = None) -> dict[str, Any]:
    """Findings for the model's Direct Lake tables.

    ``stats`` maps a model table's name to its Delta table's numbers, or None when there's no such Delta table.
    Tables missing from ``stats`` couldn't be read and aren't checked. ``duplicates`` maps "table.column" (the one
    side of a relationship) to how many of its values appear more than once, and ``unmatched`` maps "table.column"
    (the many side) to the rows whose key has no match on the one side; None when they weren't checked. ``ours``
    is True for the model KratosForge builds over the layer, whose fix for schema drift is Update model."""
    tables = direct_lake_tables(bim)
    behavior = fallback_behavior(bim)
    onelake = any((source(bim, t.expression) or {}).get("kind") == "onelake" for t in tables)
    fails = behavior.lower() == "directlakeonly" or onelake
    past = ("Refresh fails until the table is compacted." if onelake else
            "Queries on it fail until the table is compacted, because fallback is off." if fails else
            "Queries on it fall back to DirectQuery and run slower.")
    update = "update_model" if ours else None
    findings: list[dict[str, Any]] = []
    rows_out = []
    total_bytes = 0
    types: dict[tuple[str, str], str] = {}
    for t in tables:
        s = stats.get(t.name, "unread")
        where = f"{t.schema}.{t.entity}" if t.schema else t.entity
        if s == "unread":
            continue
        if s is None:
            findings.append(_finding("fail", t.name, "Table missing",
                                     f"{where} isn't a Delta table in the lakehouse. If it's a SQL view, Direct Lake on SQL "
                                     "always falls back to DirectQuery for it; otherwise queries on it fail.", update))
            rows_out.append({"table": t.name, "delta_table": where, "exists": False})
            continue
        assert isinstance(s, TableStats)
        total_bytes += s.bytes
        delta = dict(s.columns)
        for c in t.columns:
            if c["calculated"]:
                if not onelake:
                    findings.append(_finding("fail", t.name, "Calculated column",
                                             f"{c['name']} is a calculated column, which Direct Lake on SQL doesn't support.",
                                             column=c["name"]))
                continue
            src = c["source"] or c["name"]
            if src not in delta:
                findings.append(_finding("fail", t.name, "Column missing",
                                         f"The model reads {src}, which {where} no longer has. Visuals that use it show an error.",
                                         update, src))
                continue
            dtype = semantic.tmdl_type(delta[src])
            types[(t.name, c["name"])] = dtype or ""
            if dtype is None:
                findings.append(_finding("fail", t.name, "Type Direct Lake can't read",
                                         f"{src} is {delta[src]}, which Direct Lake can't read. Leave the column out of the "
                                         "model, or store it as a string.", update, src))
            elif c["type"] and _TMSL.get(c["type"]) and _TMSL[c["type"]] != dtype:
                findings.append(_finding("warn", t.name, "Type changed",
                                         f"The model says {src} is {c['type']}, and {where} now stores it as {delta[src]}. "
                                         "Refresh can fail or values can change.", update, src))
        read = {c["source"] or c["name"] for c in t.columns}
        for col, typ in s.columns:
            if col in read:
                continue
            if semantic.tmdl_type(typ) is None:
                findings.append(_finding("info", t.name, "Type left out",
                                         f"{col} is {typ}, which Direct Lake can't read, so the model leaves it out.",
                                         None, col))
            elif not col.startswith("_"):
                findings.append(_finding("info", t.name, "New column",
                                         f"{where} has {col}, which the model doesn't have yet.", update, col))
        if lim:
            for label, noun, have, limit, about in (
                    ("Parquet files", "Parquet files", s.files, lim["files"], False),
                    ("Row groups", "row groups", s.row_groups, lim["row_groups"], s.row_groups_estimated),
                    ("Rows", "rows", s.rows, lim["rows"], False)):
                f = _guardrail(t, label, noun, have, limit, lim["sku"], past, about)
                if f:
                    findings.append(f)
        rows_out.append({"table": t.name, "delta_table": where, "exists": True, "rows": s.rows, "files": s.files,
                         "row_groups": s.row_groups, "row_groups_estimated": s.row_groups_estimated, "bytes": s.bytes})
    if lim and lim["model_bytes"] and total_bytes > lim["model_bytes"]:
        findings.append(_finding("warn", "", "Model size",
                                 f"The model's tables take {total_bytes / _GB:.1f} GB on OneLake; {lim['sku']} allows "
                                 f"{lim['model_bytes'] // _GB} GB. {past}"))
    for r in relationships(bim):
        a, b = types.get((r["from_table"], r["from_column"])), types.get((r["to_table"], r["to_column"]))
        if a and b and a != b:
            findings.append(_finding("fail", r["from_table"], "Relationship types differ",
                                     f"{r['from_table']}.{r['from_column']} is {a} and {r['to_table']}.{r['to_column']} is "
                                     f"{b}. Direct Lake needs both sides of a relationship to have the same type.",
                                     column=r["from_column"]))
        n = (duplicates or {}).get(f"{r['to_table']}.{r['to_column']}")
        if n:
            findings.append(_finding("fail", r["to_table"], "Key not unique",
                                     f"{r['to_column']} has {_n(n)} value{'s' if n != 1 else ''} that appear more than once, "
                                     f"so the relationship from {r['from_table']} fails at query time.",
                                     "show_rows", r["to_column"]))
        miss = (unmatched or {}).get(f"{r['from_table']}.{r['from_column']}") or {}
        if miss.get("rows") or miss.get("empty"):
            parts = []
            if miss.get("rows"):
                parts.append(f"{_n(miss['rows'])} row{'s' if miss['rows'] != 1 else ''} with a {r['from_column']} that "
                             f"isn't in {r['to_table']} ({_n(miss['values'])} value{'s' if miss['values'] != 1 else ''})")
            if miss.get("empty"):
                parts.append(f"{_n(miss['empty'])} row{'s' if miss['empty'] != 1 else ''} with no {r['from_column']}")
            findings.append(_finding("info", r["from_table"], "Rows with no match",
                                     f"{r['from_table']} has {' and '.join(parts)}. Power BI groups them under (Blank) "
                                     f"when you slice by {r['to_table']}, instead of under the {r['to_table']} row they belong to.",
                                     "show_rows", r["from_column"]))
    order = {"fail": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["table"], f["check"]))
    counts = {k: sum(1 for f in findings if f["severity"] == k) for k in order}
    return {"findings": findings, "tables": rows_out, "counts": counts, "behavior": behavior,
            "storage": "onelake" if onelake else "sql", "limits": lim, "model_bytes": total_bytes}


def unique_keys(bim: dict[str, Any]) -> list[tuple[str, str]]:
    """(table, column) on the one side of each relationship: the keys whose values must be unique."""
    return sorted({(r["to_table"], r["to_column"]) for r in relationships(bim)})
