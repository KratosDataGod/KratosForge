"""Fabric CU avoided by a run on this PC, from Fabric runs of the same workload (FINOPS-005).

Run time on this PC is never turned into CU: how long a run took here says nothing about what
the same work uses in Fabric. The estimate comes only from Fabric runs of the same workload
(its baseline): their measured job CU when the caller has it, otherwise their estimated job CU. With no usable baseline the saving is unknown, never 0.

Comparison rules (ESI-201): ``calibrate_comparable`` uses a Fabric run as a baseline only when it
is of the same logical workload, definition version and input scope, and its CU figure is valid.
Its input data volume is checked too, when both runs recorded it (``VOLUME_TOLERANCE``). With no
Fabric run that counts, the result is Unknown with the reason for each run left out. ``enrich``
returns a new measurement row for an earlier run whose figure was Unknown, once later Fabric runs
count; the old row stays.

Pure: no I/O and no Fabric calls. The caller gathers the baselines, and writes any rows returned.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Any, cast

from . import measurement as ms

METRIC = "fabric_job_cu_avoided"
MAX_AGE_DAYS = 90
MAX_BASELINES = 5  # the most recent Fabric runs; older ones say less about today's data


def _usable(b: dict[str, Any], now: dt.datetime, max_age_days: float) -> bool:
    cu = b.get("cu") or {}
    if cu.get("classification") not in (ms.MEASURED, ms.ESTIMATED) or cu.get("value") is None:
        return False
    at = ms.parse_time(b.get("at"))
    return at is not None and 0 <= (now - at).total_seconds() <= max_age_days * 86400  # not dated in the future


def _when(b: dict[str, Any]) -> dt.datetime:
    """A usable baseline's time: _usable has checked that it parses."""
    return cast(dt.datetime, ms.parse_time(b["at"]))


def _pick(usable: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The measured baselines, and the ones ``calibrate`` uses: measured when there are any, otherwise
    estimated, the most recent few."""
    measured = [b for b in usable if b["cu"]["classification"] == ms.MEASURED]
    return measured, sorted(measured or usable, key=_when, reverse=True)[:MAX_BASELINES]


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
    ids: dict[str, Any] = {"workload_id": local_run.get("workload_id"), "run_id": local_run.get("run_id")}
    usable = [b for b in baselines if _usable(b, now, max_age_days)]
    version = local_run.get("version")
    if version is not None:
        same = [b for b in usable if b.get("version") == version]  # an unrecorded definition doesn't match
        if usable and not same:
            return ms.unknown(METRIC, ms.CU_SECONDS, "The Fabric runs of this workload ran a different (or unrecorded) version, "
                              "so they don't show what this one would use.", **ids)
        usable = same
    if not usable:
        why = ("No Fabric run of this workload to compare with." if not baselines else
               f"No Fabric run of this workload in the last {max_age_days:g} days has a known CU figure.")
        return ms.unknown(METRIC, ms.CU_SECONDS, why, **ids)
    measured, pick = _pick(usable)
    kind = "measured" if measured else "estimated"
    rows = local_run.get("rows")
    scaled = isinstance(rows, (int, float)) and rows > 0 and all(isinstance(b.get("rows"), (int, float)) and b["rows"] > 0
                                                                 for b in pick)
    per = [ms.convert(b["cu"]["value"], b["cu"]["unit"], ms.CU_SECONDS) * (rows / b["rows"] if scaled else 1.0)
           for b in pick]
    newest = _when(pick[0])
    oldest = _when(pick[-1])
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


# Comparison rules (ESI-201) ------------------------------------------------------------------------
#
# A Fabric run counts as a baseline for a run only when it is of the same logical workload, the same
# definition version and the same input scope, ran in Fabric, and its CU figure is valid (measured or
# estimated, in CU seconds or CU hours, finite and not negative). Its input data volume is checked too
# when both runs recorded it. Nothing here writes anything: new measurement rows are returned.

# The data volume check: a Fabric run's input rows (and input bytes) must be within this fraction of
# the run's own figure. 0.25 means at most 25% more or 25% less. A Fabric run outside it read
# different data and doesn't count. When a figure isn't recorded on both sides, the check can't run:
# the Fabric run still counts, and the result says the check didn't run. It never counts as passed.
VOLUME_TOLERANCE = 0.25
VOLUME_MEASURES = (("rows", "input rows"), ("bytes", "input bytes"))
PASSED, FAILED, NOT_RUN = "passed", "failed", "not_run"

COMPARISON_KEYS = ("workload_key", "definition_version", "input_scope")
_KEY_WORDS = {"workload_key": "workload", "definition_version": "definition version", "input_scope": "input scope"}
_CU_UNITS = (ms.CU_SECONDS, ms.CU_HOURS)


def comparable(a: dict[str, Any], b: dict[str, Any], *, keys: tuple[str, ...] = COMPARISON_KEYS) -> dict[str, Any]:
    """Whether two runs or baselines can be compared: the same workload, definition version and input
    scope. A value that wasn't recorded matches nothing. Returns ``comparable`` and, when they can't
    be compared, the ``reason``. Time is never one of the keys: two runs at similar times aren't
    comparable for that alone. This is the one rule; ``savings/baselines.py`` (ESI-302) uses the same."""
    for key in keys:
        x, y = a.get(key), b.get(key)
        if x in (None, "") or y in (None, ""):
            return {"comparable": False, "reason": f"The {_KEY_WORDS[key]} wasn't recorded, so it can't be matched."}
        if str(x).strip() != str(y).strip():
            return {"comparable": False, "reason": f"A different {_KEY_WORDS[key]} ({y} instead of {x})."}
    return {"comparable": True, "reason": ""}


def _volume(value: Any, what: str) -> float | None:
    """A recorded input volume, or None when it wasn't recorded. A bad value is refused, naming it."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"The {what} must be a finite number of zero or more, not {value!r}.")
    return float(value)


def volume_check(run: dict[str, Any], fabric_run: dict[str, Any], *,
                 tolerance: float = VOLUME_TOLERANCE) -> dict[str, Any]:
    """Whether a Fabric run read about as much data as ``run``: its input rows and input bytes, each
    when both sides recorded it, within ``tolerance`` of the run's own figure.

    Returns ``status`` (passed, failed or not_run) and ``reason``. not_run means neither figure was
    recorded on both sides: nothing was checked, and it doesn't count as passed."""
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) \
            or tolerance < 0:
        raise ValueError(f"The data volume tolerance must be a finite fraction of zero or more, not {tolerance!r}.")
    rid, fid = run.get("run_id") or "(no run_id)", fabric_run.get("run_id") or "(no run_id)"
    checked = []
    for key, words in VOLUME_MEASURES:
        mine = _volume(run.get(key), f"{words} of run {rid}")
        theirs = _volume(fabric_run.get(key), f"{words} of Fabric run {fid}")
        if mine is None or theirs is None:
            continue
        if abs(theirs - mine) > tolerance * mine:
            return {"status": FAILED,
                    "reason": (f"It read {theirs:g} {words} and this run read {mine:g}, more than "
                               f"{tolerance:.0%} apart, so it read different data.")}
        checked.append(words)
    if not checked:
        return {"status": NOT_RUN, "reason": ("The data volume check couldn't run: neither input rows nor input "
                                              "bytes were recorded for both this run and the Fabric run.")}
    return {"status": PASSED, "reason": f"Its {' and '.join(checked)} are within {tolerance:.0%} of this run's."}


def _metric_problem(cu: Any) -> str:
    """Why a Fabric run's CU figure isn't valid evidence of what it used; empty when it is."""
    if not isinstance(cu, dict) or not cu:
        return "It has no CU figure."
    cls = cu.get("classification")
    if cls == ms.UNKNOWN or (cls in (ms.MEASURED, ms.ESTIMATED) and cu.get("value") is None):
        return "Its CU isn't known."
    if cls == ms.SCENARIO:
        return "Its CU is a Scenario figure, which isn't evidence of what a run used."
    if cls not in (ms.MEASURED, ms.ESTIMATED):
        return f"Its CU has an unknown classification ({cls!r})."
    if cu.get("unit") not in _CU_UNITS:
        return f"Its CU figure is in {cu.get('unit')!r}, not CU seconds or CU hours; run time is never CU."
    found = ms.problems(cu)
    if found:
        return "Its CU figure isn't valid: " + "; ".join(found) + "."
    return ""


def check_baseline(run: dict[str, Any], fabric_run: dict[str, Any], *, now: dt.datetime | None = None,
                   max_age_days: float = MAX_AGE_DAYS, tolerance: float = VOLUME_TOLERANCE) -> dict[str, Any]:
    """Whether one Fabric run counts as a baseline for ``run``, and why not when it doesn't.

    ``run``: workload_key, definition_version, input_scope, run_id, and optionally rows and bytes
    (its input volume). ``fabric_run``: the same keys, plus at, ``cu`` (a CU measurement) and
    optionally ``ran`` (``fabric``). Returns ``counts``, ``reason`` (empty when it counts) and the
    ``volume`` check's result."""
    now = now or dt.datetime.now(dt.timezone.utc)
    volume = volume_check(run, fabric_run, tolerance=tolerance)
    match = comparable(run, fabric_run)
    at = ms.parse_time(fabric_run.get("at"))
    why = ""
    if fabric_run.get("ran") not in (None, "", "fabric"):
        why = "It didn't run in Fabric."
    elif not match["comparable"]:
        why = match["reason"]
    elif problem := _metric_problem(fabric_run.get("cu")):
        why = problem
    elif at is None:
        why = "It has no run time, so its age can't be checked."
    elif at > now:
        why = "Its run time is in the future."
    elif (now - at).total_seconds() > max_age_days * 86400:
        why = f"It ran more than {max_age_days:g} days ago."
    elif volume["status"] == FAILED:
        why = volume["reason"]
    return {"run_id": fabric_run.get("run_id"), "counts": not why, "reason": why, "volume": volume}


def _fabric_runs(fabric_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per Fabric run id: the same run read twice counts once; two CU figures are refused."""
    out: dict[str, dict[str, Any]] = {}
    for n, r in enumerate(fabric_runs, 1):
        rid = str(r.get("run_id") or "").strip()
        if not rid:
            raise ValueError(f"Fabric run {n} in the list has no run_id; every Fabric run needs one so it "
                             "counts once.")
        seen = out.get(rid)
        if seen is None:
            out[rid] = dict(r, run_id=rid)
        elif (seen.get("cu") or {}).get("value") != (r.get("cu") or {}).get("value") or \
                (seen.get("cu") or {}).get("classification") != (r.get("cu") or {}).get("classification"):
            raise ValueError(f"Fabric run {rid} appears twice with different CU figures; keep one.")
    return list(out.values())


def select_baselines(run: dict[str, Any], fabric_runs: list[dict[str, Any]], *, now: dt.datetime | None = None,
                     max_age_days: float = MAX_AGE_DAYS, tolerance: float = VOLUME_TOLERANCE) -> dict[str, Any]:
    """The Fabric runs that count as baselines for ``run`` (``counted``, each with its ``volume``
    check) and the ones that don't, each with its reason (``excluded``)."""
    counted, excluded = [], []
    for r in _fabric_runs(fabric_runs):
        c = check_baseline(run, r, now=now, max_age_days=max_age_days, tolerance=tolerance)
        if c["counts"]:
            counted.append(dict(r, volume=c["volume"]))
        else:
            excluded.append({"run_id": r["run_id"], "reason": c["reason"]})
    return {"counted": counted, "excluded": excluded}


def _none_count(fabric_runs: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> str:
    """Why no Fabric run counts, naming each run left out."""
    if not fabric_runs:
        return "No Fabric run of this workload to compare with."
    by_reason: dict[str, list[str]] = {}
    for x in excluded:
        by_reason.setdefault(x["reason"], []).append(str(x["run_id"]))
    parts = [f"{why} ({'runs' if len(ids) > 1 else 'run'} {', '.join(ids)})" for why, ids in by_reason.items()]
    return "No Fabric run counts as a baseline for this run. " + " ".join(parts)


def calibrate_comparable(run: dict[str, Any], fabric_runs: list[dict[str, Any]], *, now: dt.datetime | None = None,
                         max_age_days: float = MAX_AGE_DAYS, tolerance: float = VOLUME_TOLERANCE,
                         notes: list[str] | None = None) -> dict[str, Any]:
    """``calibrate`` with the comparison rules: only Fabric runs that count as baselines are used.

    ``run``: a run on this PC with workload_key, definition_version, input_scope, run_id, at, and
    optionally rows and bytes. ``fabric_runs``: as for ``check_baseline``. Returns ``cu`` (the Fabric
    job CU avoided, Unknown with its reason when no Fabric run counts, never 0), ``counted`` (the
    Fabric run ids that count) and ``excluded`` (the others, with their reasons)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    ids: dict[str, Any] = {"workload_id": run.get("workload_key"), "run_id": run.get("run_id")}
    chosen = select_baselines(run, fabric_runs, now=now, max_age_days=max_age_days, tolerance=tolerance)
    counted, excluded = chosen["counted"], chosen["excluded"]
    result: dict[str, Any] = {"counted": [r["run_id"] for r in counted], "excluded": excluded}
    if not counted:
        return result | {"cu": ms.unknown(METRIC, ms.CU_SECONDS, _none_count(fabric_runs, excluded), **ids)}
    used = _pick(counted)[1]
    not_run = sum(1 for r in used if r["volume"]["status"] == NOT_RUN)
    rules = [(f"Only Fabric runs of the same workload, definition version ({run['definition_version']}) and input "
              f"scope ({run['input_scope']}) with a valid CU figure are used.")]
    if not_run < len(used):
        rules.append(f"The input data volume of {len(used) - not_run} of the {len(used)} Fabric runs used is within "
                     f"{tolerance:.0%} of this run's.")
    if not_run:
        rules.append(f"The data volume check couldn't run for {not_run} of the {len(used)} Fabric runs used: "
                     "input rows and bytes weren't recorded on both sides.")
    local = {"workload_id": run.get("workload_key"), "run_id": run.get("run_id"), "at": run.get("at"),
             "rows": run.get("rows"), "version": run.get("definition_version")}
    base = [{"cu": r["cu"], "at": r["at"], "rows": r.get("rows"), "version": r.get("definition_version")}
            for r in counted]
    return result | {"cu": calibrate(local, base, now=now, max_age_days=max_age_days, notes=rules + list(notes or []))}


def enrich(executions: list[dict[str, Any]], fabric_runs: list[dict[str, Any]], *, now: dt.datetime | None = None,
           max_age_days: float = MAX_AGE_DAYS, tolerance: float = VOLUME_TOLERANCE) -> dict[str, Any]:
    """New measurement rows for earlier runs whose Fabric job CU avoided was Unknown, now that later
    Fabric runs can be compared with them. Writes nothing; the old rows stay as they are.

    ``executions``: each ``{"run": ..., "measurement": ...}``: the run, shaped as for
    ``calibrate_comparable``, and a measurement row recorded for it. A run with any known row is left
    alone, so running this again adds nothing. Returns ``rows`` (each with ``run_id``,
    ``previous_measurement_id``, the Unknown row it adds to, the new ``measurement`` and the Fabric
    runs ``counted``) and ``still_unknown`` (run ids with the reason)."""
    runs: dict[str, dict[str, Any]] = {}
    for n, e in enumerate(executions, 1):
        run, m = e.get("run") or {}, e.get("measurement") or {}
        rid = str(run.get("run_id") or "").strip()
        if not rid:
            raise ValueError(f"Execution {n} in the list has no run_id.")
        if m.get("metric") != METRIC:
            raise ValueError(f"The measurement of run {rid} is of {m.get('metric')!r}, not {METRIC}.")
        if m.get("run_id") not in (None, "", rid):
            raise ValueError(f"The measurement given for run {rid} belongs to run {m.get('run_id')}.")
        slot = runs.setdefault(rid, {"run": run, "known": False, "unknown_ids": []})
        if m.get("classification") == ms.UNKNOWN:
            slot["unknown_ids"].append(m.get("id"))
        else:
            slot["known"] = True
    rows, still_unknown = [], []
    for rid, slot in runs.items():
        if slot["known"]:
            continue
        found = calibrate_comparable(slot["run"], fabric_runs, now=now, max_age_days=max_age_days,
                                     tolerance=tolerance)
        cu = found["cu"]
        if cu["classification"] == ms.UNKNOWN:
            still_unknown.append({"run_id": rid, "reason": cu["reason"]})
            continue
        rows.append({"run_id": rid, "previous_measurement_id": slot["unknown_ids"][-1], "measurement": cu,
                     "counted": found["counted"]})
    return {"rows": rows, "still_unknown": still_unknown}
