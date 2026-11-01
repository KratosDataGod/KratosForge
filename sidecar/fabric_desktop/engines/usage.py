"""Usage records any backend can store: measured, billed or estimated, one unit each (ADR-006).

A record says what was used, in which unit, and how we know:

    measured   this PC measured it (wall seconds, CPU seconds, peak bytes): measure.py's numbers
    billed     the platform's own billing says so (Fabric capacity metrics, Databricks system
               billing tables), with where it came from
    estimated  worked out from assumptions, which the record carries; never shown as billed

Units are never converted into each other. Fabric capacity unit seconds and Databricks DBUs are
different things priced differently, so ``totals`` adds only same-unit, same-kind records, and
``add`` refuses to mix them. A money comparison needs a pricing configuration and belongs to FinOps
(calibration.py), not here.

``UsageSink`` is where records go. Optimize can write them to the central lakehouse's ``fdopt_``
tables when one is set up; a local-only or Databricks-only profile needs no sink at all.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

KINDS = ("measured", "billed", "estimated")
UNITS = {
    "seconds": "wall-clock seconds",
    "cpu_seconds": "CPU seconds",
    "bytes": "bytes (peak memory or data read)",
    "rows": "rows",
    "fabric_cu_seconds": "Fabric capacity unit seconds",
    "databricks_dbu": "Databricks units (DBU)",
    "snowflake_credits": "Snowflake credits",
    "warehouse_seconds": "SQL warehouse seconds",
}


@dataclass(frozen=True)
class UsageRecord:
    engine: str
    unit: str
    amount: float
    kind: str
    source: str = ""                     # measure.py, capacity metrics app, system.billing.usage...
    assumptions: tuple[str, ...] = ()    # required for estimates
    at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def __post_init__(self) -> None:
        if self.unit not in UNITS:
            raise ValueError(f"Unknown unit {self.unit!r}. Units: {', '.join(UNITS)}.")
        if self.kind not in KINDS:
            raise ValueError(f"Unknown kind {self.kind!r}. Kinds: {', '.join(KINDS)}.")
        if isinstance(self.amount, bool) or not isinstance(self.amount, (int, float)) or not math.isfinite(self.amount):
            raise ValueError("Usage is a finite number.")
        if self.amount < 0:
            raise ValueError("Usage can't be negative.")
        if self.kind == "estimated" and not self.assumptions:
            raise ValueError("An estimate says what it assumes.")
        if self.kind == "billed" and not self.source:
            raise ValueError("Billed usage says where the bill came from.")

    def add(self, other: UsageRecord) -> UsageRecord:
        if (self.unit, self.kind) != (other.unit, other.kind):
            raise ValueError(f"Can't add {other.kind} {other.unit} to {self.kind} {self.unit}: "
                             "different units or kinds are never combined.")
        return UsageRecord(self.engine if self.engine == other.engine else "*", self.unit,
                           self.amount + other.amount, self.kind, self.source,
                           tuple(dict.fromkeys(self.assumptions + other.assumptions)))

    def as_dict(self) -> dict[str, Any]:
        return {"engine": self.engine, "unit": self.unit, "amount": self.amount, "kind": self.kind,
                "source": self.source, "assumptions": list(self.assumptions), "at": self.at}


def totals(records: Iterable[UsageRecord]) -> list[dict[str, Any]]:
    """One total per (kind, unit): measured seconds next to billed DBUs, never added together."""
    sums: dict[tuple[str, str], float] = {}
    for r in records:
        sums[(r.kind, r.unit)] = sums.get((r.kind, r.unit), 0.0) + r.amount
    return [{"kind": k, "unit": u, "label": UNITS[u], "amount": round(a, 6)} for (k, u), a in sorted(sums.items())]


class UsageSink(Protocol):
    def write(self, records: list[UsageRecord]) -> None: ...


class MemorySink:
    """Keeps records in memory; what a profile with no central backend uses."""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def write(self, records: list[UsageRecord]) -> None:
        self.records.extend(records)
