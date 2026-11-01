"""The central lakehouse's tables, and the migrations that build them.

Fabric Desktop keeps one central lakehouse per organization (default ``FabricDesktop_Central``).
Its tables come in families, each with its own prefix, migrations and schema version: Optimize's
``fdopt_`` tables (which also hold the installations, audit and config every family shares), and
any family another feature registers with ``register`` (for example ``fdacc_`` for the access cache).
A family may read another family's tables but never writes to or migrates them. Tables sit in ``dbo``
unless their family has a schema of its own (so a OneLake security role can leave it out; see
roles.py), and directly under Tables in a lakehouse without schemas. The rules that let many installations write to the
same tables at once:

- **Insert only.** No client updates or deletes a row. A change of state (a recommendation
  accepted, applied, dismissed; a setting changed) is a new row, and the latest row wins.
- **Idempotent.** Every row has a ``record_id`` worked out from what it records, and is written
  with an insert-only MERGE on it, so a retry or a second sync of the same row adds nothing.
- **Provenance.** Every row says which installation wrote it, when, with which app version and
  against which schema version. Fabric items are identified by workspace and item ids, never by
  display name.

A migration only adds: a new table, or a new nullable column. Each step is safe to run again, so a
migration that stopped part way is finished by running it again, and its version is recorded in
the family's ``<prefix>schema_version`` only after every step succeeded. An app that finds a newer
schema version of a family than it knows opens that family read-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

import polars as pl

PREFIX = "fdopt_"
SCHEMA = "dbo"
S, I, F, B = pl.String, pl.Int64, pl.Float64, pl.Boolean
# A column type as these tables spell it: the class (pl.String) or an instance (pl.Datetime("us")).
# polars takes either in a schema.
Dtype = pl.DataType | type[pl.DataType]

# On every row.
PROVENANCE: dict[str, Dtype] = {"record_id": S, "installation_id": S, "created_at": S, "app_version": S,
                                      "schema_version": I}

# Version 1. Times are ISO 8601 UTC strings, like the metadata framework's run log.
TABLES_V1: dict[str, dict[str, Dtype]] = {
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


def columns(name: str, tables: dict[str, dict[str, Dtype]] | None = None) -> dict[str, Dtype]:
    """Every column of table ``name`` (without the prefix): provenance first, then its own."""
    return PROVENANCE | (tables or TABLES_V1)[name]


def table(name: str) -> str:
    return PREFIX + name


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    # The tables (by name, without prefix) with every column this version expects.
    tables: dict[str, dict[str, Dtype]]
    # Extra steps beyond creating missing tables; each must be safe to run again.
    steps: tuple[Callable[[Any], None], ...] = ()


MIGRATIONS: list[Migration] = [Migration(1, "Create the Optimize tables", TABLES_V1)]


def latest() -> int:
    return max(m.version for m in MIGRATIONS)


def expected(version: int | None = None) -> dict[str, dict[str, Dtype]]:
    """Every table and column a lakehouse at ``version`` (default: this app's) has, by full name."""
    version = latest() if version is None else version
    out: dict[str, dict[str, Dtype]] = {}
    for m in MIGRATIONS:
        if m.version <= version:
            for name in m.tables:
                out[table(name)] = out.get(table(name), {}) | columns(name, m.tables)
    return out


# ---------- table families ----------

# fd + 2 to 8 lowercase letters + underscore: fdopt_, fdacc_, fdcap_ ...
PREFIX_RULE = re.compile(r"^fd[a-z]{2,8}_$")
# A family's own schema: lowercase letters, digits and _, starting with a letter.
SCHEMA_RULE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class Family:
    """One feature's tables in the central lakehouse: ``<prefix><name>``, versioned on their own in
    ``<prefix>schema_version``. Every table gets the PROVENANCE columns and ``record_id`` as its key.

    ``title`` names the owner as people see it ("Optimize", "Access cache"). ``migrations`` is the
    list, or a function returning it. ``default_config`` rows go to fdopt_config on initialize, their
    keys prefixed with the family's prefix (Optimize's aren't). ``schema`` is where its tables sit in
    a lakehouse with schemas: dbo, or one of its own for tables only some people may read. Choose it
    before the family's first release: tables already written elsewhere aren't moved."""

    def __init__(self, prefix: str, title: str, migrations: list[Migration] | Callable[[], list[Migration]],
                 default_config: tuple[tuple[str, str], ...] = (), schema: str = SCHEMA):
        self.prefix, self.title, self._migrations, self.default_config = prefix, title, migrations, default_config
        self.schema = schema

    @property
    def migrations(self) -> tuple[Migration, ...]:
        m = self._migrations
        return tuple(m() if callable(m) else m)

    def table(self, name: str) -> str:
        return self.prefix + name

    def latest(self) -> int:
        return max(m.version for m in self.migrations)

    def expected(self, version: int | None = None) -> dict[str, dict[str, Dtype]]:
        """Every table and column this family has at ``version`` (default: this app's), by full name."""
        version = self.latest() if version is None else version
        out: dict[str, dict[str, Dtype]] = {}
        for m in self.migrations:
            if m.version <= version:
                for name in m.tables:
                    out[self.table(name)] = out.get(self.table(name), {}) | columns(name, m.tables)
        return out


OPTIMIZE = Family(PREFIX, "Optimize", lambda: MIGRATIONS, tuple(DEFAULT_CONFIG.items()))
FAMILIES: dict[str, Family] = {PREFIX: OPTIMIZE}


def register(family: Family) -> Family:
    """Add a feature's table family to the central lakehouse. Call once, at import of the module
    that owns the tables. The prefix follows PREFIX_RULE and is unique; versions start at 1 with no
    gaps; the first migration creates ``schema_version`` like every family's; a later migration
    only adds tables or nullable columns. A schema of its own is not dbo and not another family's."""
    if not PREFIX_RULE.match(family.prefix):
        raise ValueError(f"A table family prefix is fd, 2 to 8 lowercase letters and _ (not {family.prefix!r}).")
    if not SCHEMA_RULE.match(family.schema):
        raise ValueError(f"A family's schema is lowercase letters, digits and _ (not {family.schema!r}).")
    owner = next((f for f in FAMILIES.values() if f.schema == family.schema and f.prefix != family.prefix), None)
    if family.schema != SCHEMA and owner:
        raise ValueError(f"The {family.schema} schema already belongs to {owner.title}.")
    if family.prefix in FAMILIES and FAMILIES[family.prefix] is not family:
        raise ValueError(f"The {family.prefix} tables already belong to {FAMILIES[family.prefix].title}.")
    versions = [m.version for m in family.migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(f"{family.title}'s migrations must be numbered 1, 2, 3... in order.")
    if family.migrations[0].tables.get("schema_version") != TABLES_V1["schema_version"]:
        raise ValueError(f"{family.title}'s first migration must create schema_version "
                         f"with the columns {list(TABLES_V1['schema_version'])}.")
    FAMILIES[family.prefix] = family
    return family


def family_of(table_name: str) -> Family | None:
    """The registered family a table belongs to, by its prefix."""
    return next((f for p, f in FAMILIES.items() if table_name.startswith(p)), None)


def schema_of(table_name: str) -> str:
    """The schema a table sits in (in a lakehouse with schemas): its family's, else dbo."""
    family = family_of(table_name)
    return family.schema if family else SCHEMA


def schemas() -> list[str]:
    """Every schema a registered family uses, dbo first."""
    return [SCHEMA] + sorted({f.schema for f in FAMILIES.values()} - {SCHEMA})
