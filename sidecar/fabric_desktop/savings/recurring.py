"""Period figures and the recurring monthly benefit, both read from execution accruals (ESI-304).

The ledger has one accrual grain: one run (``attribution.attribute``). Everything here is computed
when it is read and is never written:

- ``by_period`` groups runs' gross and net CU avoided by the day, ISO week or month they ran in,
  cut in UTC (a run at 23:30 in Chicago on the 31st is in the next month).
  A run synced from two devices, or attributed twice, counts once (by execution id). A period's
  figure is as weak as its weakest run, and runs whose figure is unknown are counted beside it,
  never as 0.
- ``project`` estimates the monthly benefit of one workload from its recent runs: the average net
  CU avoided per run times how often it ran. It is always **Estimated**, whatever its inputs,
  because it is about runs that haven't happened. It stops (no projection) once the workload is
  retired or hasn't run in the window, and it uses only runs under the workload's current baseline
  version: when the baseline is re-evaluated after a material change, runs under the old one don't
  project the new one. Too few runs give Unknown with the reason. Runs whose figure is unknown
  are left out of the projection, never filled in with an average: the projection then counts
  only the runs with evidence, so it may be low, and it says so (``unknown_runs``, ``complete``
  and its assumptions).

Pure: no I/O.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .. import measurement as ms

PERIODS = ("day", "week", "month")
METRIC = "fabric_cu_avoided_monthly"
WINDOW_DAYS = 30
MIN_RUNS = 3
DAYS_PER_MONTH = 365.25 / 12
_STRENGTH = {ms.MEASURED: 0, ms.ESTIMATED: 1, ms.UNKNOWN: 2}


def _time(value: Any) -> dt.datetime:
    t = ms.parse_time(value)
    if t is None:
        raise ValueError(f"Not a time: {value!r}")
    return t


def _period(t: dt.datetime, period: str) -> str:
    t = t.astimezone(dt.timezone.utc)
    if period == "day":
        return t.date().isoformat()
    if period == "week":
        year, week, _ = t.isocalendar()
        return f"{year}-W{week:02d}"
    return f"{t.year}-{t.month:02d}"


def _weakest(classes: list[str]) -> str:
    return max(classes, key=_STRENGTH.__getitem__) if classes else ms.UNKNOWN


def _unique(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One result per run. The same run attributed twice (a second device, a retry) is the same
    run; two different figures for one run are refused, since one would be counted wrongly."""
    out: dict[Any, dict[str, Any]] = {}
    for r in results:
        key = r["execution_id"]
        seen = out.get(key)
        if seen is not None and (seen["gross_cu_hours"], seen["net_cu_hours"], seen["baseline_version"]) != (
                r["gross_cu_hours"], r["net_cu_hours"], r["baseline_version"]):
            raise ValueError(f"Run {key} has two different attributions; correct one instead of adding both.")
        out.setdefault(key, r)
    return list(out.values())


def by_period(results: list[dict[str, Any]], period: str = "month") -> list[dict[str, Any]]:
    """Gross and net CU avoided per period, oldest first, from ``attribution.attribute`` results."""
    if period not in PERIODS:
        raise ValueError(f"period is one of {', '.join(PERIODS)}.")
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in _unique(results):
        groups.setdefault(_period(_time(r["at"]), period), []).append(r)
    out = []
    for key in sorted(groups):
        runs = groups[key]
        known = [r for r in runs if r["gross_cu_hours"] is not None]
        net_known = [r for r in known if r["net_cu_hours"] is not None]
        out.append({
            "period": key, "runs": len(runs), "unknown_runs": len(runs) - len(known),
            "gross_cu_hours": round(sum(r["gross_cu_hours"] for r in known), 6) if known else None,
            "gross_classification": _weakest([r["gross_classification"] for r in known]),
            "net_cu_hours": round(sum(r["net_cu_hours"] for r in net_known), 6) if net_known else None,
            "net_classification": _weakest([r["net_classification"] for r in net_known]),
            # Net is complete only when every run's net is known and includes its overhead.
            "complete": bool(runs) and all(r["complete"] for r in runs),
            "ledger_ready": bool(known) and all(r["ledger_ready"] for r in known),
        })
    return out


def project(results: list[dict[str, Any]], *, workload_key: str, baseline_version: str, now: Any,
            window_days: float = WINDOW_DAYS, min_runs: int = MIN_RUNS,
            retired_at: Any = None) -> dict[str, Any]:
    """The estimated monthly benefit of one workload, from its runs in the last ``window_days``
    under ``baseline_version`` (its current one). Never written to the ledger."""
    if window_days <= 0 or min_runs < 1:
        raise ValueError("window_days must be above 0 and min_runs at least 1.")
    end = _time(now)
    start = end - dt.timedelta(days=window_days)
    base = {"workload_key": workload_key, "baseline_version": baseline_version, "window_days": window_days,
            "window_start": start.isoformat(), "window_end": end.isoformat(), "written": False}

    def unknown(state: str, reason: str, **extra: Any) -> dict[str, Any]:
        return base | {"state": state, "reason": reason, **extra,
                       "projection": ms.unknown(METRIC, ms.CU_HOURS, reason, workload_id=workload_key)}

    retired = ms.parse_time(retired_at)
    if retired is not None and retired <= end:
        return unknown("stopped", "The workload is retired, so nothing more is projected.")
    mine = [r for r in _unique(results) if r["workload_key"] == workload_key]
    recent = [r for r in mine if start < _time(r["at"]) <= end]
    current = [r for r in recent if r["baseline_version"] == baseline_version]
    if not recent:
        return unknown("stopped", f"No runs in the last {window_days:g} days, so nothing is projected.", runs=0)
    if not current:
        return unknown("rebaselined", "Every recent run was measured against an older baseline; the new "
                                      "baseline needs runs of its own before anything is projected.",
                       runs=0, older_baseline_runs=len(recent))
    known = [r for r in current if r["net_cu_hours"] is not None]
    if len(known) < min_runs:
        return unknown("too_few_runs", f"{len(known)} run(s) with a known figure under this baseline; "
                                       f"at least {min_runs} are needed to project.",
                       runs=len(current), older_baseline_runs=len(recent) - len(current))
    per_run = sum(r["net_cu_hours"] for r in known) / len(known)
    # Only runs with a known figure are projected: an unknown run isn't given the average.
    runs_per_month = len(known) * DAYS_PER_MONTH / window_days
    value = per_run * runs_per_month
    assumptions = [f"{len(known)} runs with a known figure in the last {window_days:g} days continue at the same rate",
                   f"average net CU avoided per run of those {len(known)}"]
    if len(known) < len(current):
        assumptions.append(f"{len(current) - len(known)} run(s) with an unknown figure are left out, so this may be low")
    if any(not r["complete"] for r in known):
        assumptions.append("some runs' overhead is unknown, so net may be lower")
    projection = ms.make(ms.ESTIMATED, METRIC, ms.CU_HOURS, value, workload_id=workload_key,
                         method="average net per run x runs per month", period_start=start, period_end=end,
                         assumptions=assumptions, uncertainty="a projection of runs that haven't happened")
    return base | {"state": "projected", "reason": "", "runs": len(current), "known_runs": len(known),
                   "unknown_runs": len(current) - len(known), "complete": len(known) == len(current),
                   "older_baseline_runs": len(recent) - len(current), "runs_per_month": round(runs_per_month, 6),
                   "net_cu_hours_per_run": round(per_run, 6),
                   "inputs_classification": _weakest([r["net_classification"] for r in known]),
                   "projection": projection}

