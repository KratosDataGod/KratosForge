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


# ---- Fallback predictor (Preview): one verdict for the model, causes that don't depend on size, a forecast from
# the Delta log's history, and the smallest capacity that fits. Predicted from metadata on this PC; Fabric runs
# no query, so nothing here says a model does fall back, only that it will, might, or is within the guardrails.

PREDICTED = "Predicted from metadata on this PC. Fabric hasn't run a query."
HORIZON_DAYS = 90
MIN_COMMITS = 3   # a forecast needs at least this many commits ...
MIN_DAYS = 7      # ... spread over at least this many days
_DAY_MS = 86_400_000
VERDICTS = ("will", "might", "within", "not_checked")
LABELS = {"will": "Will fall back", "might": "Might fall back", "within": "Within limits", "not_checked": "Not checked"}
_METRICS = (("files", "Parquet files", "Parquet file"), ("row_groups", "Row groups", "row group"), ("rows", "Rows", "row"))


@dataclass
class Commit:
    """A Delta table after one commit, rebuilt from its log: when, how many Parquet files, and rows when known."""
    version: int
    timestamp: int           # milliseconds since 1970, from the commit
    files: int
    rows: int | None = None  # None when a removed file's row count isn't in the log that was read


def _ns(n: int, noun: str) -> str:
    return f"{_n(n)} {noun}{'' if n == 1 else 's'}"


def _days_of(points: list[tuple[int, int]]) -> float:
    return (points[-1][0] - points[0][0]) / _DAY_MS if len(points) > 1 else 0.0


def _slope(points: list[tuple[int, int]]) -> float:
    """Least-squares growth per day of (milliseconds, value) points."""
    xs = [(t - points[0][0]) / _DAY_MS for t, _ in points]
    ys = [v for _, v in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    return 0.0 if var == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / var


def growth(points: list[tuple[int, int | None]]) -> dict[str, Any]:
    """How one number of a table grows, from its value after each commit read (oldest first).

    Only the latest unbroken run of known values counts, and only since the last time the number went down
    (OPTIMIZE merges files; a delete removes rows): growth before a compaction says nothing about growth after it.
    ``status`` is ``growing`` (with ``per_day``), ``no_growth`` or ``not_enough`` (fewer than MIN_COMMITS commits
    or MIN_DAYS days)."""
    known: list[tuple[int, int]] = []
    for t, v in points:
        known = [] if v is None else known + [(t, v)]
    out: dict[str, Any] = {"commits": len(known), "days": round(_days_of(known), 1), "per_day": 0.0, "compacted": False}
    if len(known) < MIN_COMMITS or _days_of(known) < MIN_DAYS:
        return out | {"status": "not_enough"}
    drop = max((i for i in range(1, len(known)) if known[i][1] < known[i - 1][1]), default=0)
    seg = known[drop:]
    out |= {"compacted": drop > 0, "used": len(seg)}
    if len(seg) < 2 or seg[-1][1] <= seg[0][1]:
        return out | {"status": "no_growth"}
    if len(seg) < MIN_COMMITS or _days_of(seg) < MIN_DAYS:
        return out | {"status": "not_enough"}  # growing again since the last compaction, but too briefly to measure
    per_day = _slope(seg)
    return out | ({"status": "growing", "per_day": per_day} if per_day > 0 else {"status": "no_growth"})


def _series(commits: list[Commit], stats: TableStats) -> dict[str, list[tuple[int, int | None]]]:
    """Each guardrail's number after each commit. Row groups aren't in the log: they follow the files at today's
    row groups per file."""
    per_file = (stats.row_groups / stats.files) if stats.row_groups is not None and stats.files else None
    return {"files": [(c.timestamp, c.files) for c in commits],
            "row_groups": [(c.timestamp, None if per_file is None else round(c.files * per_file)) for c in commits],
            "rows": [(c.timestamp, c.rows) for c in commits]}


def _growths(commits: list[Commit] | None, stats: TableStats) -> dict[str, dict[str, Any]] | None:
    if commits is None:
        return None
    ordered = sorted(commits, key=lambda c: (c.timestamp, c.version))
    return {k: growth(v) for k, v in _series(ordered, stats).items()}


def _at_horizon(have: int | None, g: dict[str, Any] | None, horizon: int) -> int | None:
    if have is None:
        return None
    if g and g["status"] == "growing":
        return round(have + g["per_day"] * horizon)
    return have


def _level(have: int | None, limit: int | None) -> str:
    """over, near (within 10%) or within; "" when either is unknown. Exactly at the limit is still inside it."""
    if have is None or limit is None:
        return ""
    return "over" if have > limit else "near" if have >= limit * NEAR else "within"


def behavior_meaning(behavior: str, kind: str) -> tuple[str, str]:
    """The behavior setting's name and what it means for this model."""
    b = behavior.lower()
    name = {"directlakeonly": "Direct Lake only", "directqueryonly": "DirectQuery only"}.get(b, "Automatic")
    if kind == "onelake":
        return name, "Direct Lake on OneLake doesn't fall back to DirectQuery. Past a limit, refresh fails."
    if b == "directlakeonly":
        return name, "Nothing falls back. Past a limit, queries on the model fail."
    if b == "directqueryonly":
        return name, "Every query goes to DirectQuery, whatever the limits."
    return name, ("Past a limit, every query on the model goes to DirectQuery, which is slower. A table whose source "
                  "is a view sends only the queries that read it.")


def _smallest(key: str, have: int) -> str:
    """The smallest F SKU where this number is within its limit (not near it), or "" when none is."""
    for sku in GUARDRAILS:
        lim = limits(sku)
        assert lim is not None
        if _level(have, lim[key]) == "within":
            return sku
    return ""


def _forecast_text(g: dict[str, Any] | None, have: int | None, limit: int, noun: str, sku: str,
                   horizon: int) -> dict[str, Any]:
    """The forecast for one guardrail of one table at one SKU."""
    if g is None:
        return {"status": "no_history", "message": "No history read."}
    used = f"{_ns(g['commits'], 'commit')} over {g['days']:g} days"
    if g["status"] == "not_enough":
        return {"status": "not_enough", "message": f"Not enough history ({used}). A forecast needs at least "
                                                   f"{MIN_COMMITS} commits over {MIN_DAYS} days."}
    if have is not None and have > limit:
        return {"status": "past", "message": f"Already past {sku}'s {noun} limit."}
    if g["status"] == "no_growth":
        since = " since the last compaction" if g["compacted"] else ""
        return {"status": "no_growth", "message": f"No growth seen{since} ({used})."}
    assert have is not None
    days = int((limit - have) // g["per_day"]) + 1
    if days <= horizon:
        return {"status": "crossing", "days": days,
                "message": f"Crosses {sku}'s {noun} limit in about {_ns(days, 'day')} at the current rate ({used})."}
    return {"status": "stays_within", "message": f"Stays within {sku}'s {noun} limit for the next {horizon} days at the "
                                                 f"current rate ({used})."}


def _table_report(t: ModelTable, s: TableStats | None | str, lim: dict[str, Any] | None, commits: list[Commit] | None,
                  kind: str, fails: bool, secured: set[str], horizon: int, layer: str) -> dict[str, Any]:
    where = f"{t.schema}.{t.entity}" if t.schema else t.entity
    verb = "fail" if fails else "go to DirectQuery"
    row: dict[str, Any] = {"read": s != "unread", "table": t.name, "delta_table": where, "view": False, "rows": None, "files": None,
                           "row_groups": None, "row_groups_estimated": False, "bytes": None, "checks": [],
                           "forecast": None, "at_horizon": None, "causes": []}
    if s == "unread":
        return row | {"status": "not_checked", "reason": "Its Delta table wasn't read."}
    checks: list[dict[str, Any]] = []
    status = "within"
    if s is None:
        if kind == "sql":
            row["view"] = True
            checks.append({"check": "Source is a view", "evidence": "SQL endpoint view", "level": "over", "fix": None,
                           "message": (f"Every query on this table {'fails' if fails else 'goes to DirectQuery'}, because "
                                       "Direct Lake reads only Delta tables"
                                       + (" and the model doesn't allow DirectQuery." if fails else ".")),
                           "fix_text": f"Materialize the view as a Delta table{' in ' + layer if layer else ''}, then point "
                                       "the model's table at it."})
            row["causes"].append("view")
        else:
            checks.append({"check": "Table missing", "evidence": "No Delta table", "level": "over", "fix": None,
                           "message": f"There's no Delta table at {where}, so queries on this table fail.",
                           "fix_text": "Point the model's table at a Delta table that exists."})
            row["causes"].append("missing")
        status = "will"
    else:
        assert isinstance(s, TableStats)
        g = _growths(commits, s)
        row |= {"rows": s.rows, "files": s.files, "row_groups": s.row_groups,
                "row_groups_estimated": s.row_groups_estimated, "bytes": s.bytes,
                "at_horizon": {k: _at_horizon(getattr(s, k), (g or {}).get(k), horizon) for k, _, _ in _METRICS}}
        if lim:
            forecasts = []
            for key, label, noun in _METRICS:
                have, limit = getattr(s, key), lim[key]
                level = _level(have, limit)
                if not level:
                    checks.append({"check": label, "evidence": "Not in the Delta log", "level": "", "fix": None,
                                   "message": "Not checked: the Delta log doesn't hold every file's row count.",
                                   "fix_text": None, "have": None, "limit": limit})
                    continue
                about = s.row_groups_estimated and key == "row_groups"
                c: dict[str, Any] = {"check": label, "noun": noun, "have": have, "limit": limit, "level": level, "about": about,
                                     "fix": None, "fix_text": None}
                if level == "over":
                    why = (" Compaction doesn't change this." if key == "rows" else
                           " Read from the Delta log and Parquet footers." if key == "row_groups" else
                           " Read from the Delta log.")
                    c["message"] = f"Queries on this table {verb}, because the table has more {noun}s than {lim['sku']} allows.{why}"
                elif level == "near":
                    more = limit - have + 1
                    c["message"] = (f"Within the limit now, but {_n(more)} more {noun}{'' if more == 1 else 's'} would "
                                    "pass it."
                                    + (" Each small append adds one." if key == "files" else ""))
                else:
                    c["message"] = "Within the limit."
                if level in ("over", "near"):
                    if key == "rows":
                        bigger = _smallest("rows", have)
                        c["fix_text"] = (f"Filter or aggregate the rows the model needs into a {layer or 'smaller'} table"
                                         + (f", or move the workspace to {bigger} or larger." if bigger else "."))
                    else:
                        c["fix"] = "compact"
                        c["fix_text"] = ("Compact the table, and batch the appends." if key == "files" and level == "near"
                                         else "Compact the table so its row groups are larger." if key == "row_groups"
                                         else "Compact the table.")
                checks.append(c)
                forecasts.append((key, _forecast_text((g or {}).get(key) if g else None, have, limit,
                                                      noun, lim["sku"], horizon)))
            rank = {"over": 0, "near": 1, "within": 2, "": 3}
            checks.sort(key=lambda c: rank[c["level"]])
            levels = {c["level"] for c in checks}
            status = "will" if "over" in levels else "might" if "near" in levels else "within"
            row["forecast"] = _pick_forecast(forecasts, g)
    if t.name in secured:
        checks.insert(0, {"check": "Security at the SQL endpoint", "evidence": "Row-level or object-level security",
                          "level": "over", "fix": None,
                          "message": f"Queries that read this table {verb}, because the SQL analytics endpoint has "
                                     "row-level or object-level security on it.",
                          "fix_text": "Define the security as roles in the semantic model instead."})
        row["causes"].append("security")
        status = "will"
    return row | {"status": status if lim or s is None or t.name in secured else "not_checked", "checks": checks,
                  "reason": "" if lim or s is None or t.name in secured else "Pick the capacity to compare it with."}


def _pick_forecast(forecasts: list[tuple[str, dict[str, Any]]], g: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """The forecast that matters most for a table: the soonest crossing, else one past the limit, else the most
    careful of the rest. A number the log holds for no commit (rows a writer didn't count) has no say."""
    out: dict[str, Any] = {"commits": max((x["commits"] for x in (g or {}).values()), default=0)}
    if g:
        forecasts = [(k, f) for k, f in forecasts if g[k]["commits"]] or forecasts
    crossing = sorted((f["days"], k, f) for k, f in forecasts if f["status"] == "crossing")
    if crossing:
        return out | crossing[0][2] | {"metric": crossing[0][1]}
    for status in ("past", "not_enough", "no_history", "stays_within", "no_growth"):
        hit = next(((k, f) for k, f in forecasts if f["status"] == status), None)
        if hit:
            return out | hit[1] | {"metric": hit[0]}
    return out | {"status": "no_history", "message": "No history read.", "metric": ""}


def _judge_numbers(tables: list[dict[str, Any]], model_bytes: int, lim: dict[str, Any], when: str) -> str:
    """will, might, within or not_checked for numbers only (no views or security), today or at the horizon."""
    sized = [t for t in tables if not {"view", "missing"} & set(t["causes"])]  # those don't depend on the SKU
    read = [t for t in sized if t["files"] is not None]
    if not read:
        return "not_checked"
    levels = set()
    for t in read:
        nums = t["at_horizon"] if when == "horizon" else {k: t[k] for k, _, _ in _METRICS}
        levels |= {_level(nums[k], lim[k]) for k, _, _ in _METRICS}
    levels.add(_level(model_bytes, lim["model_bytes"]))
    if "over" in levels:
        return "will"
    if "near" in levels:
        return "might"
    return "not_checked" if len(read) < len(sized) else "within"


def predict(bim: dict[str, Any], stats: dict[str, TableStats | None], history: dict[str, list[Commit]] | None,
            sku: str, horizon_days: int = HORIZON_DAYS, endpoint_security: dict[str, list[str]] | None = None,
            layer: str = "", read_problem: str = "") -> dict[str, Any]:
    """Will the model fall back to DirectQuery (or fail), might it, or is it within the published guardrails.

    ``stats`` is as for check(): a table missing from it wasn't read, None means there's no Delta table there.
    ``history`` maps a table to its commits (model_directlake_service.history); a table missing from it has no
    forecast. ``endpoint_security`` lists the tables with row-level (``rls``) or object-level (``ols``) security at
    the SQL analytics endpoint; None when it wasn't read, which leaves the model Not checked rather than Within
    limits. ``layer`` names the layer of the app's own model, for the fixes' wording. Reads nothing; no wall clock."""
    tables = direct_lake_tables(bim)
    behavior = fallback_behavior(bim)
    kinds = {(source(bim, t.expression) or {}).get("kind") for t in tables}
    kind = "onelake" if "onelake" in kinds else "sql" if "sql" in kinds else ""
    fails = behavior.lower() == "directlakeonly" or kind == "onelake"
    lim = limits(sku)
    name, meaning = behavior_meaning(behavior, kind)
    will_label = "Will fail" if fails else "Will fall back"
    secured = set((endpoint_security or {}).get("rls") or []) | set((endpoint_security or {}).get("ols") or [])
    history = history or {}
    rows = [_table_report(t, stats.get(t.name, "unread"), lim, history.get(t.name), kind, fails, secured,
                          horizon_days, layer.title()) for t in tables]
    model_bytes = sum(r["bytes"] or 0 for r in rows)
    size_level = _level(model_bytes, lim["model_bytes"]) if lim and any(r["files"] is not None for r in rows) else ""
    counts = {v: sum(1 for r in rows if r["status"] == v) for v in VERDICTS}

    not_checked = []
    unread = [r["table"] for r in rows if not r["read"]]
    if read_problem == "no_signin":
        not_checked.append("The tables' Delta logs, because there's no OneLake sign-in on this PC.")
    elif unread:
        not_checked.append(f"{_ns(len(unread), 'table')} that couldn't be read: {', '.join(unread[:10])}"
                           + (f" and {len(unread) - 10} more" if len(unread) > 10 else "") + ".")
    if kind == "sql" and endpoint_security is None:
        not_checked.append("Row-level and object-level security at the SQL analytics endpoint, which this PC doesn't "
                           "read. Queries on a table with such security go to DirectQuery.")
    not_checked += [("How busy the capacity's memory is when people query. Fabric can page tables out under pressure, "
                     "which metadata doesn't show."),
                    "Changes Microsoft makes to the guardrails after this app's table of them was last reviewed."]

    model = _model_verdict(rows, lim, size_level, kind, endpoint_security, fails, behavior, will_label)
    notes = [meaning]
    short = [r["table"] for r in rows if r["forecast"] and r["forecast"]["status"] in ("not_enough", "no_history")]
    if short:
        notes.append(f"{_ns(len(short), 'table')} had no forecast, because too little history was read; at the "
                     "horizon they're counted at today's numbers.")
    notes.append("Model size at the horizon is counted at today's size: the forecast covers files, row groups and rows.")
    return {"label": PREDICTED, "sku": lim["sku"] if lim else "", "limits": lim, "kind": kind, "behavior": behavior,
            "behavior_label": name, "behavior_meaning": meaning, "will_label": will_label, "model": model,
            "counts": counts, "tables": rows, "horizon_days": horizon_days, "read_problem": read_problem,
            "size": {"bytes": model_bytes if any(r["files"] is not None for r in rows) else None, "limit": lim["model_bytes"] if lim else None,
                     "level": size_level},
            "skus": what_if(rows, model_bytes, horizon_days),
            "not_checked": not_checked, "notes": notes}


def _model_verdict(rows: list[dict[str, Any]], lim: dict[str, Any] | None, size_level: str, kind: str,
                   endpoint_security: dict[str, list[str]] | None, fails: bool, behavior: str,
                   will_label: str) -> dict[str, Any]:
    """One verdict for the whole model, and the table that decides it."""
    def out(verdict: str, table: str, message: str) -> dict[str, Any]:
        label = will_label if verdict == "will" else LABELS[verdict]
        return {"verdict": verdict, "label": label, "table": table, "message": message}

    sku = lim["sku"] if lim else ""
    dq_only = behavior.lower() == "directqueryonly"
    whole = ("A Direct Lake on OneLake model doesn't fall back, so refresh will fail." if kind == "onelake" else
             "With Direct Lake only, queries on the model will fail instead of going to DirectQuery." if fails else
             "The model is set to DirectQuery only, so every query goes to DirectQuery anyway." if dq_only else
             "With the Automatic setting, one table past a limit sends every query on the model to DirectQuery, "
             "including queries that don't read {t}.")

    def ratio(r: dict[str, Any]) -> float:
        return max(((r[k] or 0) / lim[k] for k, _, _ in _METRICS), default=0.0) if lim else 0.0

    over = sorted((r for r in rows if any(c["level"] == "over" and "have" in c for c in r["checks"])),
                  key=lambda r: (-ratio(r), r["table"]))
    if over:
        t = over[0]
        c = next(c for c in t["checks"] if c["level"] == "over" and "have" in c)
        return out("will", t["table"], f"{t['table']} is past {sku}'s {c['noun']} limit. "
                   + whole.replace("{t}", t["table"]))
    if size_level == "over":
        return out("will", "", f"The model's tables take more space on OneLake than {sku} allows. "
                   + whole.replace(", including queries that don't read {t}", ""))
    for cause, text in (("security", "The SQL analytics endpoint has security on {t}, so queries that read it will {v}."),
                        ("missing", "{t}'s Delta table isn't in the lakehouse, so queries on it will fail."),
                        ("view", "{t}'s source is a SQL view, so every query that reads it will {v}.")):
        hit = next((r for r in rows if cause in r["causes"]), None)
        if hit:
            return out("will", hit["table"], text.replace("{t}", hit["table"]).replace(
                "{v}", "fail" if fails else "go to DirectQuery"))
    near = sorted((r for r in rows if r["status"] == "might"), key=lambda r: (-ratio(r), r["table"]))
    if near:
        t = near[0]
        c = next(c for c in t["checks"] if c["level"] == "near")
        return out("might", t["table"], f"{t['table']} is within 10% of {sku}'s {c['noun']} limit, so a few more "
                   "loads will pass it.")
    if size_level == "near":
        return out("might", "", f"The model's tables are within 10% of {sku}'s model size limit.")
    if not lim:
        return out("not_checked", "", "Pick the capacity to compare the model with its Direct Lake limits.")
    unread = [r for r in rows if not r["read"]]
    if unread:
        return out("not_checked", unread[0]["table"],
                   f"{_ns(len(unread), 'table')} weren't read, so the model can't be judged against {sku}'s limits."
                   .replace("1 table weren't", "1 table wasn't"))
    if kind == "sql" and endpoint_security is None:
        return out("not_checked", "", f"Every table is within {sku}'s limits, but security at the SQL analytics endpoint "
                   "couldn't be read from this PC, and it can send queries to DirectQuery.")
    return out("within", "", f"Every table is within {sku}'s published Direct Lake guardrails.")


def what_if(rows: list[dict[str, Any]], model_bytes: int, horizon_days: int = HORIZON_DAYS) -> dict[str, Any]:
    """The same numbers judged against each F SKU, today and at the horizon, and the smallest that fits. Views and
    endpoint security don't depend on the SKU, so they're left out here and named instead. No cost or CU figure."""
    out = []
    for sku in GUARDRAILS:
        lim = limits(sku)
        assert lim is not None
        out.append({"sku": sku, "aliases": sorted(a for a, f in ALIASES.items() if f == sku),
                    "today": _judge_numbers(rows, model_bytes, lim, "today"),
                    "horizon": _judge_numbers(rows, model_bytes, lim, "horizon")})
    fixed = [r["table"] for r in rows if r["causes"]]
    return {"rows": out, "horizon_days": horizon_days,
            "smallest_today": next((r["sku"] for r in out if r["today"] == "within"), ""),
            "smallest_horizon": next((r["sku"] for r in out if r["horizon"] == "within"), ""),
            "every_sku": fixed}
