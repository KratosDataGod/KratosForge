"""The optimization lakehouse's tables, and the migrations that build them.

Every table is named ``fdopt_<name>`` so it can share a lakehouse with anything else, and sits in
``dbo`` (or directly under Tables in a lakehouse without schemas). The rules that let many
installations write to the same tables at once:

- **Insert only.** No client updates or deletes a row. A change of state (a recommendation
  accepted, applied, dismissed; a setting changed) is a new row, and the latest row wins.
- **Idempotent.** Every row has a ``record_id`` worked out from what it records, and is written
  with an insert-only MERGE on it, so a retry or a second sync of the same row adds nothing.
- **Provenance.** Every row says which installation wrote it, when, with which app version and
  against which schema version. Fabric items are identified by workspace and item ids, never by
  display name.

A migration only adds: a new table, or a new nullable column. Each step is safe to run again, so a
migration that stopped part way is finished by running it again, and its version is recorded in
``fdopt_schema_version`` only after every step succeeded. An app that finds a newer schema version
than it knows opens the lakehouse read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import polars as pl

PREFIX = "fdopt_"
SCHEMA = "dbo"
S, I, F, B = pl.String, pl.Int64, pl.Float64, pl.Boolean

# On every row.
PROVENANCE: dict[str, pl.DataType] = {"record_id": S, "installation_id": S, "created_at": S, "app_version": S,
                                      "schema_version": I}

# Version 1. Times are ISO 8601 UTC strings, like the metadata framework's run log.
TABLES_V1: dict[str, dict[str, pl.DataType]] = {
    # One row per installation that connected. installation_id is random (installation.py).
    "installations": {"label": S, "os": S, "registered_at": S},
    "analysis_runs": {"run_id": S, "started_at": S, "finished_at": S, "status": S, "scope": S,
                      "rule_set_version": S, "artifacts_scanned": I, "findings": I, "error": S},
    # An artifact as one analysis run saw it. display_name is for showing only, never a key.
    "artifacts": {"artifact_key": S, "workspace_id": S, "item_id": S, "item_type": S, "display_name": S,
                  "run_id": S, "observed_at": S},
    # One row per observation; the same problem seen again has the same fingerprint.
    "findings": {"fingerprint": S, "rule_id": S, "rule_version": S, "artifact_key": S, "target": S,
                 "severity": S, "title": S, "detail": S, "evidence": S, "run_id": S, "observed_at": S},
    # Lifecycle events: open, accepted, applied, dismissed, verified, superseded.
    "recommendations": {"recommendation_id": S, "fingerprint": S, "kind": S, "state": S, "reason": S,
                        "impact": S, "effort": S, "confidence": S, "at": S},
    "actions": {"action_id": S, "recommendation_id": S, "artifact_key": S, "action_type": S, "dry_run": B,
                "outcome": S, "started_at": S, "finished_at": S, "message": S},
    # The measurement.py shape: classified, with source, method and assumptions.
    "measurements": {"measurement_id": S, "classification": S, "metric": S, "unit": S, "value": F, "currency": S,
                     "workload_key": S, "run_id": S, "workspace_id": S, "capacity_id": S, "source": S, "method": S,
                     "collected_at": S, "period_start": S, "period_end": S, "assumptions": S, "uncertainty": S,
                     "reason": S},
    "cu_usage": {"workspace_id": S, "item_id": S, "capacity_id": S, "operation": S, "period_start": S,
                 "period_end": S, "cu_seconds": F, "classification": S, "source": S, "collected_at": S},
    # category: verified_performance, measured_cu, estimated_cu, potential. ``counts_for`` is the
    # expected cloud execution a saving stands for, so several local runs replacing one cloud run
    # count once.
    "savings": {"savings_id": S, "kind": S, "category": S, "classification": S, "workload_key": S,
                "recommendation_id": S, "counts_for": S, "metric": S, "unit": S, "value": F, "method": S,
                "confidence": S, "assumptions": S, "baseline_runs": I, "compared_runs": I, "period_start": S,
                "period_end": S},
    "local_execution": {"execution_id": S, "project": S, "artifact_key": S, "workload_type": S, "engine": S,
                        "started_at": S, "finished_at": S, "seconds": F, "rows": I, "bytes": I, "cpu_seconds": F,
                        "peak_memory_bytes": I, "result": S, "fabric_workload_type": S, "counts_for": S},
    "dependencies": {"from_key": S, "to_key": S, "relation": S, "run_id": S, "observed_at": S},
    "rules": {"rule_id": S, "rule_version": S, "title": S, "category": S, "definition_hash": S},
    "audit": {"at": S, "action": S, "target": S, "outcome": S, "message": S},
    # Settings for everyone using this lakehouse, as events: the latest row per key wins.
    "config": {"key": S, "value": S, "at": S},
    "schema_version": {"version": I, "description": S, "applied_at": S},
}

# What makes two rows the same row, for the insert-only MERGE.
KEY = "record_id"
DEFAULT_CONFIG = {
    "retention_days": "365",   # how long rows are kept once retention runs (a later PR)
    "store_sql_text": "never",  # SQL and code text is never written; only fingerprints
}


def columns(name: str, tables: dict[str, dict[str, pl.DataType]] | None = None) -> dict[str, pl.DataType]:
    """Every column of table ``name`` (without the prefix): provenance first, then its own."""
    return PROVENANCE | (tables or TABLES_V1)[name]


def table(name: str) -> str:
    return PREFIX + name


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    # The tables (by name, without prefix) with every column this version expects.
    tables: dict[str, dict[str, pl.DataType]]
    # Extra steps beyond creating missing tables; each must be safe to run again.
    steps: tuple[Callable[[Any], None], ...] = ()


MIGRATIONS: list[Migration] = [Migration(1, "Create the Optimize tables", TABLES_V1)]


def latest() -> int:
    return max(m.version for m in MIGRATIONS)


def expected(version: int | None = None) -> dict[str, dict[str, pl.DataType]]:
    """Every table and column a lakehouse at ``version`` (default: this app's) has, by full name."""
    version = latest() if version is None else version
    out: dict[str, dict[str, pl.DataType]] = {}
    for m in MIGRATIONS:
        if m.version <= version:
            for name in m.tables:
                out[table(name)] = out.get(table(name), {}) | columns(name, m.tables)
    return out
