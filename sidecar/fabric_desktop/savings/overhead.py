"""What one run on a PC still cost, as separate measurements (ESI-202).

A run on a PC still moves data and is still synced: it reads its inputs from OneLake, writes its
outputs back, and Optimize writes the run's record to the central lakehouse. The attribution
waterfall (``attribution.attribute``) subtracts that from gross to give net (ESI-203). This module
turns one ``fdopt_local_execution`` row, and the syncs that carried it, into one measurement per
figure:

- **Transfer**: ``transfer_seconds``, bytes read (``input_bytes``), bytes written (``output_bytes``),
  and OneLake read and write operations (``reads``, ``writes``), each from the row.
- **Sync**: seconds, rows and commits of the sync that carried the run (``_record_overhead`` in
  ``optimize_service.py`` records them per sync, not per run).

Each is **Measured** when the row or the sync recorded it and **Unknown**, with the reason, when it
didn't. An empty column is Unknown, never 0; a negative, NaN or infinite value is refused.

**Fabric CU.** The waterfall needs the overhead as Fabric CU. Nothing on a PC measures the CU that
OneLake reads, writes and sync use, and this module has no rate to turn operations or bytes into CU,
so the CU overhead is Unknown, which keeps net incomplete, unless the caller passes a measured or
estimated CU figure for the run (for example OneLake operations from an imported Capacity Metrics
export). No conversion is built in.

**One sync, several runs.** A sync writes many runs at once. ``share_sync`` splits one sync's seconds
across the runs it carried by the rows each one sent, so the shares add up to exactly the sync's
seconds; each share is Estimated (it assumes every row took as long to write) and names the method.
A commit carries every run in it and isn't split. A sync that carried one run is all that run's,
as measured.

Pure: no I/O.
"""

from __future__ import annotations

import math
from typing import Any

from .. import measurement as ms

# Figures read from an fdopt_local_execution row: column -> (metric, unit).
TRANSFER = {
    "transfer_seconds": ("execution_transfer_seconds", ms.SECONDS),
    "input_bytes": ("execution_bytes_read", ms.COUNT),
    "output_bytes": ("execution_bytes_written", ms.COUNT),
    "reads": ("onelake_read_operations", ms.COUNT),
    "writes": ("onelake_write_operations", ms.COUNT),
}
# The metrics one sync records (optimize_service._record_overhead), by the name used here.
SYNC = {"seconds": ("optimize_sync_seconds", ms.SECONDS), "rows": ("optimize_sync_rows", ms.COUNT),
        "commits": ("optimize_sync_commits", ms.COUNT)}
CU_METRIC = "fabric_cu_overhead"
SOURCE = "Local run on this PC"
BY_ROWS, WHOLE = "by rows", "whole sync"
_MICRO = 1_000_000  # measurements keep 6 decimals; shares are split in millionths so they add up exactly

NO_CU = ("No Fabric CU figure for this run's OneLake reads, writes and sync: a PC doesn't measure that "
         "capacity use, so net stays incomplete until one is supplied")


def _number(value: Any, field: str, unit: str) -> float | None:
    """A recorded value as a number, None when it wasn't recorded; refuses anything else."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number, not {value!r}.")
    if not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number, not {value}.")
    if value < 0:
        raise ValueError(f"{field} can't be negative ({value}).")
    if unit == ms.COUNT and value != int(value):
        raise ValueError(f"{field} is a count, so it is a whole number, not {value}.")
    return float(value)


def _ids(execution: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": execution["execution_id"], "workload_id": execution.get("artifact_key") or None,
            "workspace_id": execution.get("workspace_id") or None, "capacity_id": execution.get("capacity_id") or None}


def transfer(execution: dict[str, Any], *, source: str = SOURCE, collected_at: Any = None) -> dict[str, dict[str, Any]]:
    """The run's transfer figures, one measurement per column of :data:`TRANSFER`.

    ``collected_at`` defaults to the run's ``finished_at``, then the row's ``created_at``."""
    if not execution.get("execution_id"):
        raise ValueError("An execution needs execution_id.")
    ids = _ids(execution)
    when = collected_at or execution.get("finished_at") or execution.get("created_at")
    out = {}
    for field, (metric, unit) in TRANSFER.items():
        value = _number(execution.get(field), field, unit)
        if value is None:
            out[field] = ms.unknown(metric, unit, f"{field} wasn't recorded for this run", **ids)
        elif not when:
            raise ValueError(f"{field} was recorded but the run has no finished_at or created_at to say when; "
                             "pass collected_at.")
        else:
            out[field] = ms.make(ms.MEASURED, metric, unit, value, source=source, collected_at=when, **ids)
    return out


def sync(records: list[dict[str, Any]]) -> dict[str, Any]:
    """One sync's seconds, rows and commits, from the measurement rows it recorded (one ``run_id``).

    A metric it didn't record is Unknown."""
    if not records:
        raise ValueError("A sync needs the measurements it recorded.")
    sync_ids = {r.get("run_id") for r in records}
    if len(sync_ids) != 1 or not next(iter(sync_ids)):
        raise ValueError("A sync's measurements all have the same run_id.")
    sync_id = next(iter(sync_ids))
    workload = next((r.get("workload_id") or r.get("workload_key") for r in records
                     if r.get("workload_id") or r.get("workload_key")), None)
    by_metric: dict[str, dict[str, Any]] = {}
    for r in records:
        metric = r.get("metric")
        if metric in by_metric and by_metric[metric].get("value") != r.get("value"):
            raise ValueError(f"Sync {sync_id} has two different {metric} figures.")
        by_metric[str(metric)] = r
    out: dict[str, Any] = {"sync_id": sync_id, "workload_id": workload}
    for name, (metric, unit) in SYNC.items():
        rec = by_metric.get(metric)
        ids: dict[str, Any] = {"run_id": sync_id, "workload_id": workload}
        value = None if rec is None else _number(rec.get("value"), metric, unit)
        if rec is None or value is None or rec.get("classification") == ms.UNKNOWN:
            out[name] = ms.unknown(metric, unit, f"This sync didn't record {metric}", **ids)
            continue
        if rec.get("unit") not in (None, unit):
            raise ValueError(f"{metric} is in {unit}, not {rec.get('unit')}.")
        if rec.get("classification") not in (ms.MEASURED, ms.ESTIMATED):
            raise ValueError(f"{metric} must be measured or estimated, not {rec.get('classification')!r}.")
        period = rec.get("period") or {}
        out[name] = ms.make(rec["classification"], metric, unit, value, source=rec.get("source") or "",
                            method=rec.get("method") or "", collected_at=rec.get("collected_at"),
                            assumptions=rec.get("assumptions") or None,
                            period_start=period.get("start") or rec.get("period_start"),
                            period_end=period.get("end") or rec.get("period_end"), **ids)
    return out


def _split(total_micro: int, weights: dict[str, int]) -> dict[str, int]:
    """``total_micro`` shared in proportion to ``weights`` (largest remainder, ties by key): whole
    millionths that add up to exactly the total."""
    whole = sum(weights.values())
    floors = {k: total_micro * w // whole for k, w in weights.items()}
    left = total_micro - sum(floors.values())
    order = sorted(weights, key=lambda k: (-(total_micro * weights[k] % whole), k))
    for k in order[:left]:
        floors[k] += 1
    return floors


def share_sync(one_sync: dict[str, Any], rows_by_execution: dict[str, int]) -> list[dict[str, Any]]:
    """Each run's share of one sync (from :func:`sync`), by the rows each run sent.

    ``rows_by_execution`` must add up to the rows the sync recorded: a sync that also carried other
    rows can't be charged to these runs alone, and a sync whose rows weren't recorded can't be shared
    by rows at all (both are refused rather than guessed). The seconds shares add up to exactly the
    sync's seconds. A sync that carried one run is all that run's, as recorded."""
    if not rows_by_execution:
        raise ValueError("A sync carried at least one run.")
    weights = {}
    for eid, rows in rows_by_execution.items():
        if not eid:
            raise ValueError("Each run needs its execution_id.")
        value = _number(rows, f"rows of {eid}", ms.COUNT)
        if value is None:
            raise ValueError(f"rows of {eid} must be given to share the sync by rows.")
        weights[str(eid)] = int(value)
    sync_id, workload = one_sync["sync_id"], one_sync.get("workload_id")
    seconds, rows, commits = one_sync["seconds"], one_sync["rows"], one_sync["commits"]
    ids = {"run_id": sync_id, "workload_id": workload}
    total_rows = sum(weights.values())
    if rows["value"] is not None and total_rows != int(rows["value"]):
        raise ValueError(f"The runs' rows add up to {total_rows} but the sync wrote {int(rows['value'])}; "
                         "sharing it would charge these runs for rows they didn't send.")
    if len(weights) == 1:
        (eid,) = weights
        return [{"execution_id": eid, "sync_id": sync_id, "method": WHOLE, "seconds": seconds, "rows": rows,
                 "commits": commits}]
    if rows["value"] is None:
        raise ValueError("The sync's rows weren't recorded, so it can't be shared by rows.")
    if total_rows == 0:
        raise ValueError("The sync wrote no rows, so its seconds can't be shared by rows.")
    if seconds["value"] is None:
        shares: dict[str, int | None] = dict.fromkeys(weights)
    else:
        shares = dict(_split(round(seconds["value"] * _MICRO), weights))
    out = []
    for eid in sorted(weights):
        row_share = ms.make(rows["classification"], rows["metric"], ms.COUNT, weights[eid], source=rows["source"],
                            method=rows.get("method") or "", collected_at=rows["collected_at"],
                            assumptions=rows.get("assumptions") or None, **ids)
        micro = shares[eid]
        if micro is None:
            sec = seconds
        else:
            sec = ms.make(ms.ESTIMATED, seconds["metric"], ms.SECONDS, micro / _MICRO,
                          method=f"sync seconds shared {BY_ROWS}",
                          assumptions=[f"each of the sync's {total_rows} rows took as long to write",
                                       f"this run sent {weights[eid]} of them"], **ids)
        out.append({"execution_id": eid, "sync_id": sync_id, "method": BY_ROWS, "seconds": sec, "rows": row_share,
                    "commits": _commit_unknown(ids)})
    return out


def _commit_unknown(ids: dict[str, Any]) -> dict[str, Any]:
    return ms.unknown(SYNC["commits"][0], ms.COUNT,
                      "A commit carries every run in the sync, so it isn't split between them", **ids)


def _cu_overhead(m: dict[str, Any] | None, execution_id: str) -> dict[str, Any]:
    if m is None:
        return ms.unknown(CU_METRIC, ms.CU_HOURS, NO_CU, run_id=execution_id)
    ms.validate(m)
    if m.get("unit") not in (ms.CU_SECONDS, ms.CU_HOURS):
        raise ValueError(f"The CU overhead must be in CU-seconds or CU-hours, not {m.get('unit')}.")
    if m.get("classification") == ms.SCENARIO:
        raise ValueError("A scenario figure isn't what a run used; the CU overhead is measured or estimated.")
    if m.get("run_id") and m["run_id"] != execution_id:
        raise ValueError(f"The CU overhead is for run {m['run_id']}, not {execution_id}.")
    return m


def _combined(parts: list[dict[str, Any]], metric: str, unit: str, reason: str, execution_id: str) -> dict[str, Any]:
    if not parts:
        return ms.unknown(metric, unit, reason, run_id=execution_id)
    if len(parts) == 1:
        return parts[0]
    return ms.total(parts, metric, unit)


def per_execution(execution: dict[str, Any], sync_shares: list[dict[str, Any]] | tuple[()] = (), *,
                  cu_overhead: dict[str, Any] | None = None, source: str = SOURCE,
                  collected_at: Any = None) -> dict[str, Any]:
    """One run's overhead: its transfer figures, its share of each sync that carried it (from
    :func:`share_sync`), and ``overhead``, the Fabric CU figure for ``attribution.attribute``.

    ``cu_overhead`` is a measured or estimated CU measurement for this run, such as OneLake
    operations from an imported Capacity Metrics export. Without it ``overhead`` is Unknown.
    A run synced twice (from a second PC, say) has two shares, added up."""
    figures = transfer(execution, source=source, collected_at=collected_at)
    eid = execution["execution_id"]
    shares = list(sync_shares)
    for s in shares:
        if s.get("execution_id") != eid:
            raise ValueError(f"A sync share for run {s.get('execution_id')} isn't for run {eid}.")
    if len({s["sync_id"] for s in shares}) != len(shares):
        raise ValueError("The same sync is given twice for this run.")
    for name, (metric, unit) in SYNC.items():
        figures[f"sync_{name}"] = _combined([s[name] for s in shares], metric, unit,
                                            "No sync of this run has been recorded", eid)
    overhead = _cu_overhead(cu_overhead, eid)
    unknown = sorted(k for k, m in figures.items() if m["value"] is None or m.get("complete") is False)
    return {"execution_id": eid, "measurements": figures, "unknown": unknown, "complete": not unknown,
            "sync_ids": [s["sync_id"] for s in shares], "sync_methods": sorted({s["method"] for s in shares}),
            "overhead": overhead, "overhead_known": overhead["value"] is not None}
