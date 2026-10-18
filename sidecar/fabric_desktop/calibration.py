"""Fabric CU avoided by a run on this PC, from Fabric runs of the same workload (FINOPS-005).

Run time on this PC is never turned into CU: how long a run took here says nothing about what
the same work uses in Fabric. The estimate comes only from Fabric runs of the same workload
(its baseline): their measured CU when an imported Capacity Metrics export matched them,
otherwise their estimated job CU. With no usable baseline the saving is unknown, never 0.

Pure: no I/O and no Fabric calls. The caller gathers the baselines.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

from . import measurement as ms

METRIC = "fabric_job_cu_avoided"
MAX_AGE_DAYS = 90
MAX_BASELINES = 5  # the most recent Fabric runs; older ones say less about today's data


def _usable(b: dict[str, Any], now: dt.datetime, max_age_days: float) -> bool:
    cu = b.get("cu") or {}
    if cu.get("classification") not in (ms.MEASURED, ms.ESTIMATED) or cu.get("value") is None:
        return False
    at = ms.parse_time(b.get("at"))
    return at is not None and (now - at).total_seconds() <= max_age_days * 86400


def calibrate(local_run: dict[str, Any], baselines: list[dict[str, Any]], *, now: dt.datetime | None = None,
              max_age_days: float = MAX_AGE_DAYS, notes: list[str] | None = None) -> dict[str, Any]:
    """The Fabric job CU a run on this PC would have used in Fabric, as a classified measurement.

    ``local_run``: workload_id, run_id, at, rows (rows read, optional), version (optional).
    ``baselines``: Fabric runs of the same workload, each with ``cu`` (a fabric_job_cu measurement,
    measured or estimated), at, rows (optional), version (optional).

    Measured baselines are used when there are any, otherwise estimated ones; the median of the most
    recent few is taken. It is scaled by rows read only when both sides recorded them. ``notes`` are
    added to the assumptions, for what the caller knows about how the baselines were chosen."""
    now = now or dt.datetime.now(dt.timezone.utc)
    ids = {"workload_id": local_run.get("workload_id"), "run_id": local_run.get("run_id")}
    usable = [b for b in baselines if _usable(b, now, max_age_days)]
    version = local_run.get("version")
    if version is not None:
        same = [b for b in usable if b.get("version") in (None, version)]
        if usable and not same:
            return ms.unknown(METRIC, ms.CU_SECONDS, "The Fabric runs of this workload ran a different version, "
                              "so they don't show what this one would use.", **ids)
        usable = same
    if not usable:
        why = ("No Fabric run of this workload to compare with." if not baselines else
               f"No Fabric run of this workload in the last {max_age_days:g} days has a known CU figure.")
        return ms.unknown(METRIC, ms.CU_SECONDS, why, **ids)
    measured = [b for b in usable if b["cu"]["classification"] == ms.MEASURED]
    pick = sorted(measured or usable, key=lambda b: ms.parse_time(b["at"]), reverse=True)[:MAX_BASELINES]
    kind = "measured" if measured else "estimated"
    rows = local_run.get("rows")
    scaled = isinstance(rows, (int, float)) and rows > 0 and all(isinstance(b.get("rows"), (int, float)) and b["rows"] > 0
                                                                 for b in pick)
    per = [ms.convert(b["cu"]["value"], b["cu"]["unit"], ms.CU_SECONDS) * (rows / b["rows"] if scaled else 1.0)
           for b in pick]
    newest = ms.parse_time(pick[0]["at"])
    oldest = ms.parse_time(pick[-1]["at"])
    assumptions = [
        (f"Based on the {kind} Fabric job CU of {len(pick)} Fabric run{'s' if len(pick) != 1 else ''} of this workload "
         f"({oldest.date().isoformat()} to {newest.date().isoformat()}); the median is used."),
        ("Scaled by rows read: the CU grows in proportion to the rows read." if scaled
         else "Assumes this run read about as much data as those Fabric runs."),
        "Its time on this PC isn't used: run time here says nothing about CU in Fabric.",
        "Reading and writing OneLake from this PC still uses some capacity, which isn't counted here.",
    ] + list(notes or [])
    if not measured:
        assumptions.append("The Fabric runs' CU is itself an estimate (run time × the configured CUs).")
    sources = sorted({b["cu"].get("source") or "" for b in pick} - {""})
    return ms.make(ms.ESTIMATED, METRIC, ms.CU_SECONDS, statistics.median(per),
                   source="; ".join(sources) or "Fabric runs of this workload",
                   method=f"Median {kind} Fabric job CU of earlier Fabric runs of this workload"
                          + (", scaled by rows read" if scaled else ""),
                   collected_at=local_run.get("at"), assumptions=assumptions,
                   uncertainty=None if measured and len(pick) >= 3 else
                   "Based on few or estimated Fabric runs; treat it as a rough figure.", **ids)
