"""OPTIMIZE, VACUUM and RESTORE from a notebook on this PC (Fabric notebook parity F6, Preview).

Spark SQL's ``OPTIMIZE t [WHERE ...] [ZORDER BY (...)]``, ``VACUUM t [RETAIN n HOURS] [DRY RUN]`` and
``RESTORE [TABLE] t [TO] VERSION AS OF n | TIMESTAMP AS OF '...'``, and DeltaTable's ``optimize()``,
``vacuum()``, ``restoreToVersion()`` and ``restoreToTimestamp()``, run with delta-rs through
lakehouse.maintain_table, which holds the guards. This module only reads the statement and shapes
the answer as Spark's.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa

from . import lakehouse

_NAME = r"((?:`[^`]*`|[^\s`;])+)"
# Read in one pass over a long run of blanks: the end's blanks once (*+), not split every way around
# the ";", and WHERE's and ZORDER BY's text not tried to end inside a run of blanks (_NOT_MID_BLANKS),
# where whatever follows also reads from the run's start. "OPTIMIZE t WHERE x", 800 spaces and a word
# took 0.8 s, and the time grew with the cube.
_NOT_MID_BLANKS = r"(?:(?<!\s)|(?!\s))"
_OPTIMIZE = re.compile(rf"^\s*OPTIMIZE\s+{_NAME}(?:\s+WHERE\s+(?P<where>.+?){_NOT_MID_BLANKS})?"
                       rf"(?:\s+ZORDER\s+BY\s*\(?(?P<zorder>[^)]*?){_NOT_MID_BLANKS}\)?)?\s*+;?\s*$", re.I | re.S)
_VACUUM = re.compile(rf"^\s*VACUUM\s+{_NAME}(?:\s+RETAIN\s+(?P<hours>\d+(?:\.\d+)?)\s+HOURS?)?"
                     r"(?P<dry>\s+DRY\s+RUN)?\s*+;?\s*$", re.I | re.S)
_RESTORE = re.compile(rf"^\s*RESTORE\s+(?:TABLE\s+)?{_NAME}\s+(?:TO\s+)?(?:VERSION\s+AS\s+OF\s+(?P<version>\d+)|"
                      r"TIMESTAMP\s+AS\s+OF\s+(?P<ts>'[^']*'|\"[^\"]*\"))\s*+;?\s*$", re.I | re.S)
STARTS = re.compile(r"^\s*(OPTIMIZE|VACUUM|RESTORE)\b", re.I)


def statement(query: str) -> dict[str, Any] | None:
    """The maintenance statement ``query`` is, as maintain_table's arguments, or None when it isn't one.
    A statement that starts with OPTIMIZE, VACUUM or RESTORE but isn't in a form this PC reads is refused
    by name, so it never reaches the SQL engine as something else."""
    word = STARTS.match(query)
    if not word:
        return None
    kind = word.group(1).upper()
    m = {"OPTIMIZE": _OPTIMIZE, "VACUUM": _VACUUM, "RESTORE": _RESTORE}[kind].match(query)
    if not m:
        forms = {"OPTIMIZE": "OPTIMIZE t [WHERE part = 'v' AND ...] [ZORDER BY (col, ...)]",
                 "VACUUM": "VACUUM t [RETAIN n HOURS] [DRY RUN]",
                 "RESTORE": "RESTORE [TABLE] t TO VERSION AS OF n, or TO TIMESTAMP AS OF '...'"}[kind]
        raise ValueError(f"{kind} on this PC takes the form {forms}. Nothing was changed.")
    table = _table(m.group(1))
    if kind == "OPTIMIZE":
        zorder = [c.strip().strip("`") for c in (m.group("zorder") or "").split(",") if c.strip()]
        return {"table": table, "operation": "optimize", "where": _partition_filters(m.group("where")),
                "zorder": zorder or None}
    if kind == "VACUUM":
        hours = m.group("hours")
        return {"table": table, "operation": "vacuum", "retain_hours": float(hours) if hours is not None else None,
                "dry_run": bool(m.group("dry"))}
    if m.group("version") is not None:
        return {"table": table, "operation": "restore", "version": int(m.group("version"))}
    return {"table": table, "operation": "restore", "timestamp": parse_timestamp(m.group("ts")[1:-1])}


def _table(name: str) -> str:
    if name.lower().startswith(("delta.", "parquet.")) and "`" in name:
        raise ValueError(f"{name}: on this PC, name the table (dbo.sales), not its path. Nothing was changed.")
    return name


def parse_timestamp(text: str) -> datetime:
    """A RESTORE timestamp, as Spark reads one: a date or a date and time, in UTC unless it says otherwise."""
    try:
        when = datetime.fromisoformat(str(text).strip().replace(" ", "T", 1))
    except ValueError:
        raise ValueError(f"{text!r} isn't a date and time RESTORE can read, such as '2026-11-01 13:00:00'.") from None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _partition_filters(where: str | None) -> list[tuple[str, str, Any]] | None:
    """OPTIMIZE's WHERE, which Delta allows only on partition columns: col = value, ANDed. Anything else is
    refused rather than read loosely."""
    if not where:
        return None
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(where, read="spark")
    except sqlglot.errors.SqlglotError:
        tree = None
    terms = list(tree.flatten()) if isinstance(tree, exp.And) else [tree]
    out = []
    for term in terms:
        if not (isinstance(term, exp.EQ) and isinstance(term.this, exp.Column) and isinstance(term.expression, exp.Literal)):
            raise ValueError(f"OPTIMIZE ... WHERE on this PC takes partition column = value, joined with AND, not "
                             f"{where!r}. Nothing was changed.")
        out.append((term.this.name, "=", str(term.expression.this)))
    return out


def run(spec: dict[str, Any]) -> tuple[pa.Table, str]:
    """Run one maintenance statement; Spark's answer for it as an Arrow table, and a note to print."""
    from . import spark_local
    spark_local._preview_on(spec["operation"].upper())
    r = lakehouse.maintain_table(**spec)
    return answer(r), r.note


def answer(r: lakehouse.MaintenanceResult) -> pa.Table:
    m = r.metrics
    if r.operation == "optimize":
        metrics = {"numFilesAdded": int(m.get("numFilesAdded", 0)), "numFilesRemoved": int(m.get("numFilesRemoved", 0)),
                   "partitionsOptimized": int(m.get("partitionsOptimized", 0)),
                   "totalConsideredFiles": int(m.get("totalConsideredFiles", 0)),
                   "totalFilesSkipped": int(m.get("totalFilesSkipped", 0))}
        return pa.table({"path": [r.table], "metrics": [metrics]})
    if r.operation == "vacuum":
        # As Spark: a dry run lists the files it would delete; a real one answers with the table.
        return pa.table({"path": pa.array(r.files if m.get("dryRun") else [r.table], pa.string())})
    return pa.table({"num_removed_files": [int(m.get("numRemovedFile", 0))],
                     "num_restored_files": [int(m.get("numRestoredFile", 0))]})
