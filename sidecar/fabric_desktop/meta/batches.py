"""Full loads bigger than memory: the source read in batches of one column's values.

With ``[load] batch_by = "<column>"`` a full load (or an incremental entity's first load or full
rebuild) reads its Delta source one batch at a time. A batch is a run of consecutive values of the
column, sorted, holding about as many rows as fit in free memory with the copies the later steps
make. Each batch goes through every step (contract, keys, clean, derive, hooks, lookups, checks)
on its own and is streamed into the table, and the table is replaced in one Delta commit at the
end, so readers see the old table until the whole load has worked and a load that stops leaves it
as it was.

A batch never splits one value, so a value with more rows than fit is too big on its own and the
memory guard says so before anything is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl

from . import preflight

DEFAULT_BATCH_ROWS = 2_000_000  # when free memory can't be told
MIN_BATCH_ROWS = 50_000  # below this the per-batch overhead (a scan, the key MERGE) dominates
# A batch may use this share of free memory, with preflight.HEADROOM for the copies the steps make:
# the writer still holds the previous batch's row groups while the next one is read.
MEMORY_SHARE = 0.5


@dataclass
class Batch:
    lo: Any
    hi: Any
    rows: int
    nulls: bool = False  # the batch of rows where the column is empty

    def expr(self, column: str) -> pl.Expr:
        if self.nulls:
            return pl.col(column).is_null()
        return pl.col(column).is_between(self.lo, self.hi)

    def text(self) -> str:
        if self.nulls:
            return "rows with no value"
        return str(self.lo) if self.lo == self.hi else f"{self.lo} to {self.hi}"


def count(n: int) -> str:
    return f"{n} batch{'' if n == 1 else 'es'}"


def batch_rows(est: preflight.Estimate, available: int | None) -> int:
    """Rows per batch so one batch, with the copies the steps after the read make, fits in free memory."""
    if not est.rows or not est.memory_bytes or available is None:
        return DEFAULT_BATCH_ROWS
    per_row = est.memory_bytes / est.rows
    return max(MIN_BATCH_ROWS, int(available * MEMORY_SHARE / preflight.HEADROOM / per_row))


def plan(counts: pl.DataFrame, column: str, target: int) -> list[Batch]:
    """Batches of consecutive values from ``counts`` (one row per value: ``column`` and ``len``),
    each holding up to ``target`` rows, except a value that alone holds more."""
    nulls = counts.filter(pl.col(column).is_null())
    values = counts.filter(pl.col(column).is_not_null()).sort(column)
    out: list[Batch] = []
    cur: Batch | None = None
    for value, n in values.select(column, "len").iter_rows():
        if cur is not None and cur.rows + n <= target:
            cur.hi, cur.rows = value, cur.rows + n
            continue
        cur = Batch(value, value, n)
        out.append(cur)
    if nulls.height and int(nulls["len"].sum()):
        out.append(Batch(None, None, int(nulls["len"].sum()), nulls=True))
    return out


def fold_steps(steps: list[Any]) -> list[tuple[str, int, float, str]]:
    """Each step's batches as one step: (name, rows, seconds, detail), in the order they first ran.
    The detail is the batches' own text when they agree, else the different texts."""
    order: dict[str, list[Any]] = {}
    for s in steps:
        acc = order.setdefault(s.name, [0, 0.0, []])
        acc[0] += s.rows
        acc[1] += s.seconds
        if s.detail and s.detail not in acc[2]:
            acc[2].append(s.detail)
    out = []
    for name, (rows, seconds, details) in order.items():
        text = "; ".join(details[:3]) + (f"; and {len(details) - 3} other results" if len(details) > 3 else "")
        out.append((name, rows, round(seconds, 3), text))
    return out
