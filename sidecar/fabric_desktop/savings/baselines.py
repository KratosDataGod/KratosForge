"""Versioned baselines, and the before and after check that verifies an optimization (ESI-302).

A **baseline** is what one run of a workload used in Fabric, worked out from the Fabric runs in a
measurement window. Only runs that are comparable count: the same workload, the same definition
version and the same input scope (ESI-201), each with a valid CU figure. A run that isn't comparable,
ran outside the window, ran on a PC or has no known CU is left out and listed with the reason
(``excluded``); a run whose figure isn't valid CU (a Scenario, NaN, infinite or negative, or a time
in seconds) is refused, naming the run. The figure is the **median** CU per run, so one unusual
run doesn't move it. Measured runs are used on their own when there are at least ``min_runs``
(default 3) of them; otherwise measured and estimated runs together, and the baseline is then
Estimated. Fewer than ``min_runs`` usable runs give an Unknown baseline with the reason, never 0.

Each baseline has a stable ``baseline_id`` (one per workload) and ``baseline_version`` (worked out
from the workload, definition version, input scope, window and the runs and figures it used), so
the same baseline worked out on two PCs is one version. A new version never edits an old one:
building it with ``previous=`` also returns a new row for the old version with ``superseded_by``
set, and the latest row of a version wins (the tables are insert only). ``current`` reads the
version in effect from those rows.

``validate`` compares an optimization's after runs with its before baseline on the same inputs:
the same workload and input scope, and one definition version (the optimized one, or the same one
for a change that doesn't touch the definition, such as table maintenance). The result:

- **Failed** when the outputs differ (``outputs_match=False``), whatever the CU did.
- **Validating** while the before isn't known, there are fewer than ``min_runs`` usable after runs,
  either side is only an estimate, or the outputs weren't compared and nobody said why they can't be.
- **Regressed** when a run uses more after the change than before it. The optimization moves back
  to Validating.
- **Verified** only when the before and after are both measured, the after isn't higher, and the
  outputs matched or the caller stated why output equivalence can't be measured.

The CU change is a measurement as weak as its inputs, of a reduction or of an increase (a
measurement is never negative). It comes only from CU, measured or a labelled estimate: a figure
in seconds is refused, because how long a run took isn't how much capacity it used.

Pure: no I/O. Rows match ``fdopt_baseline`` and ``fdopt_validation`` (schema version 2).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from typing import Any

from .. import measurement as ms
from ..optimize.shared import record_id

METRIC = "fabric_cu_per_run"
REDUCTION, INCREASE = "fabric_cu_per_run_reduction", "fabric_cu_per_run_increase"
UNIT = ms.CU_HOURS
MIN_RUNS = 3
STATISTIC = "median"
VERIFIED, VALIDATING, REGRESSED, FAILED = "verified", "validating", "regressed", "failed"
RESULTS = (VERIFIED, VALIDATING, REGRESSED, FAILED)
_STRENGTH = {ms.MEASURED: 0, ms.ESTIMATED: 1, ms.UNKNOWN: 2}
_KEYS = ("workload_key", "definition_version", "input_scope")
_KEY_WORDS = {"workload_key": "workload", "definition_version": "definition version", "input_scope": "input scope"}


def _time(value: Any, what: str) -> dt.datetime:
    t = ms.parse_time(value)
    if t is None:
        raise ValueError(f"{what} isn't a time: {value!r}.")
    return t


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat()


def _text(value: Any, what: str) -> str:
    out = "" if value is None else str(value).strip()
    if not out:
        raise ValueError(f"A baseline needs its {what}.")
    return out


def _weakest(*classes: str) -> str:
    return max(classes, key=_STRENGTH.__getitem__)


def cu_of(m: dict[str, Any] | None, what: str) -> tuple[float | None, str]:
    """A CU measurement's value in CU hours and its classification; None and unknown when it isn't known.

    Refuses anything that isn't evidence of CU used: a Scenario figure, a time in seconds (run time
    is never turned into CU), any other unit, and a NaN, infinite or negative figure."""
    if not m or m.get("classification") == ms.UNKNOWN or m.get("value") is None:
        return None, ms.UNKNOWN
    cls = m.get("classification")
    if cls == ms.SCENARIO:
        raise ValueError(f"The {what} is a Scenario figure, which isn't evidence of what a run used. "
                         "Use measured or estimated CU.")
    if cls not in (ms.MEASURED, ms.ESTIMATED):
        raise ValueError(f"The {what} has an unknown classification {cls!r}.")
    unit = m.get("unit")
    if unit == ms.SECONDS:
        raise ValueError(f"The {what} is a time in seconds, not CU. How long a run took isn't how much "
                         "capacity it used, so it can't be a CU change.")
    if unit not in (ms.CU_SECONDS, ms.CU_HOURS):
        raise ValueError(f"The {what} must be in CU seconds or CU hours, not {unit!r}.")
    value = m["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"The {what} must be a finite CU figure of zero or more, not {value!r}.")
    return ms.convert(float(value), unit, UNIT), str(cls)


def comparable(a: dict[str, Any], b: dict[str, Any], *, keys: tuple[str, ...] = _KEYS) -> dict[str, Any]:
    """Whether two runs or baselines can be compared: the same workload, definition version and input
    scope (ESI-201). A value that wasn't recorded matches nothing. Returns ``comparable`` and, when
    they can't be compared, the ``reason``. Time is never one of the keys: two runs at similar times
    aren't comparable for that alone."""
    for key in keys:
        x, y = a.get(key), b.get(key)
        if x in (None, "") or y in (None, ""):
            return {"comparable": False, "reason": f"The {_KEY_WORDS[key]} wasn't recorded, so it can't be matched."}
        if str(x).strip() != str(y).strip():
            return {"comparable": False, "reason": f"A different {_KEY_WORDS[key]} ({y} instead of {x})."}
    return {"comparable": True, "reason": ""}


def _unique(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per run id: the same run read twice (a second device, a retry) counts once, and two
    different figures for one run are refused."""
    out: dict[str, dict[str, Any]] = {}
    for r in runs:
        rid = str(r.get("run_id") or "").strip()
        if not rid:
            raise ValueError("Every run needs its run_id.")
        seen = out.get(rid)
        if seen is not None:
            if (seen.get("cu") or {}).get("value") != (r.get("cu") or {}).get("value") or \
                    (seen.get("cu") or {}).get("classification") != (r.get("cu") or {}).get("classification"):
                raise ValueError(f"Run {rid} appears twice with different CU figures. Keep one.")
            continue
        out[rid] = r
    return list(out.values())


def _pick(runs: list[dict[str, Any]], min_runs: int, what: str) -> dict[str, Any]:
    """The runs a figure rests on and its median: measured runs alone when there are enough of them,
    otherwise measured and estimated together. Each run in ``runs`` has a known, checked CU."""
    measured = [r for r in runs if r["_cls"] == ms.MEASURED]
    use = measured if len(measured) >= min_runs else runs
    left = [r for r in runs if r not in use]
    if len(use) < min_runs:
        return {"use": use, "left": [], "value": None, "classification": ms.UNKNOWN,
                "reason": (f"Only {len(use)} usable {what} run{'s' if len(use) != 1 else ''}, and at least {min_runs} "
                           "are needed.")}
    cls = _weakest(*(r["_cls"] for r in use))
    return {"use": use, "left": left, "value": statistics.median(r["_cu"] for r in use), "classification": cls,
            "reason": ""}


def _figure(picked: dict[str, Any], metric: str, workload_key: str, what: str, mid: str,
            period: tuple[Any, Any]) -> dict[str, Any]:
    """The median as a measurement, labelled as weak as its runs."""
    use = picked["use"]
    if picked["classification"] == ms.UNKNOWN:
        m = ms.unknown(metric, UNIT, picked["reason"], workload_id=workload_key)
    else:
        sources = sorted({str(r["cu"].get("source") or "") for r in use} - {""})
        latest = max(r["_at"] for r in use)
        if picked["classification"] == ms.MEASURED:
            m = ms.make(ms.MEASURED, metric, UNIT, picked["value"], workload_id=workload_key,
                        source="; ".join(sources) or "Fabric runs of this workload", collected_at=latest,
                        method=f"Median measured CU of {len(use)} {what} runs", period_start=period[0],
                        period_end=period[1])
        else:
            n_est = sum(1 for r in use if r["_cls"] == ms.ESTIMATED)
            inputs = sorted({a for r in use for a in r["cu"].get("assumptions") or []})
            m = ms.make(ms.ESTIMATED, metric, UNIT, picked["value"], workload_id=workload_key,
                        source="; ".join(sources), method=f"Median CU of {len(use)} {what} runs",
                        period_start=period[0], period_end=period[1],
                        assumptions=[f"{n_est} of the {len(use)} runs' CU is an estimate, not measured.",
                                     "The median CU per run is used."] + inputs,
                        uncertainty="Rests on estimated CU, so treat it as a rough figure.")
    m["id"] = mid
    return m


def _checked_runs(runs: list[dict[str, Any]], key: dict[str, Any], window: tuple[dt.datetime, dt.datetime | None],
                  what: str, skip: set[str] | frozenset[str] = frozenset()) -> tuple[list[dict[str, Any]],
                                                                                    list[dict[str, Any]]]:
    """Runs that can be used, each with its CU in CU hours, and the ones left out with the reason.
    Invalid CU figures are refused, naming the run."""
    usable, excluded = [], []
    start, end = window
    for r in _unique(runs):
        rid = str(r["run_id"]).strip()
        cu, cls = cu_of(r.get("cu"), f"CU of {what} run {rid}")
        why = ""
        at = ms.parse_time(r.get("at"))
        match = comparable(key, r)
        if rid in skip:
            why = "It is part of the before baseline, so it can't also be an after run."
        elif r.get("ran") not in (None, "", "fabric"):
            why = "It didn't run in Fabric."
        elif not match["comparable"]:
            why = match["reason"]
        elif at is None:
            why = "It has no run time, so it can't be placed in the window."
        elif at < start or (end is not None and at >= end):
            why = "It ran outside the window."
        elif cu is None:
            why = "Its CU isn't known."
        if why:
            excluded.append({"run_id": rid, "reason": why})
        else:
            usable.append(dict(r, run_id=rid, _cu=cu, _cls=cls, _at=at))
    return usable, excluded


def _ids(runs: list[dict[str, Any]]) -> str:
    return json.dumps(sorted(str(r["cu"].get("id")) for r in runs))


def baseline(workload_key: str, runs: list[dict[str, Any]], *, definition_version: str, input_scope: str,
             window_start: Any, window_end: Any, min_runs: int = MIN_RUNS, previous: dict[str, Any] | None = None,
             at: Any = None) -> dict[str, Any]:
    """A versioned baseline of a workload's Fabric CU per run, from its runs in the window.

    ``runs``: each with run_id, workload_key, definition_version, input_scope, at, ``cu`` (a CU
    measurement, measured or estimated) and optionally ``ran`` (``fabric``). The window runs from
    ``window_start`` up to, not including, ``window_end``. ``previous``: the version this one
    replaces. ``at``: when the baseline was made (default: the window's end).

    Returns ``row`` (the ``fdopt_baseline`` columns), ``supersedes`` (a new row for ``previous`` with
    ``superseded_by`` set, or None), the figure ``cu`` (Unknown with its reason when too few runs
    count), the runs ``excluded`` and why, and the keys it was built on."""
    key = {"workload_key": _text(workload_key, "workload"),
           "definition_version": _text(definition_version, "definition version"),
           "input_scope": _text(input_scope, "input scope")}
    if not runs:
        raise ValueError(f"A baseline of {key['workload_key']} needs at least one Fabric run.")
    if isinstance(min_runs, bool) or not isinstance(min_runs, int) or min_runs < 1:
        raise ValueError(f"min_runs is a whole number of 1 or more, not {min_runs!r}.")
    start, end = _time(window_start, "window_start"), _time(window_end, "window_end")
    if end <= start:
        raise ValueError("The window's end must be after its start.")
    made = _time(at, "at") if at not in (None, "") else end
    usable, excluded = _checked_runs(runs, key, (start, end), "Fabric")
    picked = _pick(usable, min_runs, "Fabric")
    excluded += [{"run_id": r["run_id"], "reason": "Enough measured runs, so estimated ones aren't needed."}
                 for r in picked["left"]]
    use = picked["use"]  # with too few, the runs that did count (the figure stays Unknown)
    baseline_id = "bl-" + record_id("baseline", key["workload_key"])[:16]
    version = "bv-" + record_id("baseline_version", baseline_id, key["definition_version"], key["input_scope"],
                                _iso(start), _iso(end), min_runs,
                                *sorted(f"{r['cu'].get('id')}={r['_cu']:.6f}/{r['_cls']}" for r in use))[:16]
    cu = _figure(picked, METRIC, key["workload_key"], "Fabric", "m-" + record_id("baseline_cu", version)[:16],
                 (start, end))
    row = {"baseline_id": baseline_id, "baseline_version": version, "workload_key": key["workload_key"],
           "definition_version": key["definition_version"], "window_start": _iso(start), "window_end": _iso(end),
           "runs": len(use), "measurement_ids": _ids(use), "superseded_by": None, "at": _iso(made)}
    supersedes = None
    if previous is not None:
        old = previous.get("row", previous)
        if old.get("workload_key") != key["workload_key"]:
            raise ValueError(f"Baseline {old.get('baseline_version')} is of {old.get('workload_key')}, not "
                             f"{key['workload_key']}. A baseline only replaces one of the same workload.")
        if old.get("superseded_by") not in (None, "", version):
            raise ValueError(f"Baseline {old['baseline_version']} was already replaced by {old['superseded_by']}. "
                             "Replace the current version instead.")
        if old.get("baseline_version") != version:
            if _time(old.get("at"), "The previous baseline's at") > made:
                raise ValueError("A new baseline version must be made after the one it replaces.")
            supersedes = dict(old, superseded_by=version, at=_iso(made))
    return {**key, "baseline_id": baseline_id, "baseline_version": version, "cu": cu, "statistic": STATISTIC,
            "min_runs": min_runs, "excluded": excluded, "row": row, "supersedes": supersedes}


def current(rows: list[dict[str, Any]], workload_key: str) -> dict[str, Any] | None:
    """The baseline row in effect for a workload, from ``fdopt_baseline`` rows: the latest row of each
    version wins, and the current version is the one not superseded. None when there is none; two
    versions in effect at once are refused, since a run's figure would depend on which was read first."""
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("workload_key") != workload_key:
            continue
        seen = latest.get(r["baseline_version"])
        if seen is None or _time(r["at"], "at") >= _time(seen["at"], "at"):
            latest[r["baseline_version"]] = r
    live = [r for r in latest.values() if not r.get("superseded_by")]
    if len(live) > 1:
        raise ValueError(f"{workload_key} has {len(live)} baseline versions in effect; supersede all but one.")
    return live[0] if live else None


def change(before: dict[str, Any] | None, after: dict[str, Any] | None, *, workload_key: str | None = None,
           run_id: str | None = None) -> dict[str, Any]:
    """The CU change from ``before`` to ``after`` (CU measurements per run): a reduction or an increase,
    as weak as the weaker side, or Unknown when either isn't known. A time in seconds is refused."""
    b, b_cls = cu_of(before, "before figure")
    a, a_cls = cu_of(after, "after figure")
    ids: dict[str, Any] = {"workload_id": workload_key, "run_id": run_id}
    if b is None or a is None:
        why = "The before figure isn't known." if b is None else "The after figure isn't known."
        return ms.unknown(REDUCTION, UNIT, why, **ids) | {"direction": None, "change_cu_hours": None}
    cls = _weakest(b_cls, a_cls)
    metric = INCREASE if a > b else REDUCTION
    assert before is not None and after is not None  # known figures above
    if cls == ms.MEASURED:
        sources = sorted({str(m.get("source") or "") for m in (before, after)} - {""})
        m = ms.make(ms.MEASURED, metric, UNIT, abs(b - a), source="; ".join(sources) or "Fabric runs",
                    collected_at=after.get("collected_at") or before.get("collected_at"),
                    method="After median minus before median, per run", **ids)
    else:
        inputs = sorted({x for m in (before, after) for x in m.get("assumptions") or []})
        m = ms.make(ms.ESTIMATED, metric, UNIT, abs(b - a), method="After median minus before median, per run",
                    assumptions=["The before or after figure is an estimate, not measured."] + inputs,
                    uncertainty="Rests on estimated CU, so it can't verify an optimization.", **ids)
    return m | {"direction": "increase" if a > b else ("reduction" if a < b else "none"),
                "change_cu_hours": round(a - b, 6)}


def validate(optimization_id: str, before: dict[str, Any], after_runs: list[dict[str, Any]], *,
             outputs_match: bool | None = None, outputs_not_measurable: str = "", after_definition_version: str = "",
             implemented_at: Any = None, min_runs: int = MIN_RUNS, at: Any = None) -> dict[str, Any]:
    """Compare an optimization's after runs with its before baseline (from ``baseline``).

    ``after_runs``: Fabric runs shaped as for ``baseline``. They must be of the before's workload and
    input scope and of ``after_definition_version`` (default: the before's, for a change that doesn't
    touch the definition); runs that don't match, ran before ``implemented_at`` or are part of the
    before baseline are left out with the reason. ``outputs_match``: True, False or None (not
    compared); with None, ``outputs_not_measurable`` says why equivalence can't be measured.

    Returns ``row`` (the ``fdopt_validation`` columns), ``result``, ``moves_to`` (the lifecycle state:
    verified or validating), ``reason``, the ``before`` and ``after`` figures, the ``change`` (a
    measurement as weak as its inputs) and the after runs ``excluded``."""
    oid = "" if optimization_id is None else str(optimization_id).strip()
    if not oid:
        raise ValueError("A validation needs the optimization_id.")
    if outputs_match not in (True, False, None):
        raise ValueError(f"outputs_match is True, False or None (not compared), not {outputs_match!r}.")
    if isinstance(min_runs, bool) or not isinstance(min_runs, int) or min_runs < 1:
        raise ValueError(f"min_runs is a whole number of 1 or more, not {min_runs!r}.")
    for k in ("baseline_version", "cu", "row", *_KEYS):
        if k not in before:
            raise ValueError(f"The before baseline has no {k}; build it with baselines.baseline.")
    if not after_runs:
        raise ValueError(f"Validating {oid} needs at least one after run.")
    key = {"workload_key": before["workload_key"], "input_scope": before["input_scope"],
           "definition_version": (after_definition_version or "").strip() or before["definition_version"]}
    since = _time(implemented_at, "implemented_at") if implemented_at not in (None, "") else _time(
        before["row"]["window_end"], "The before window's end")
    used_before = set(json.loads(before["row"]["measurement_ids"] or "[]"))
    before_runs = {str(r.get("run_id")) for r in after_runs if str((r.get("cu") or {}).get("id")) in used_before}
    usable, excluded = _checked_runs(after_runs, key, (since, None), "after", skip=before_runs)
    for x in excluded:
        if x["reason"] == "It ran outside the window.":
            x["reason"] = "It ran before the change was in place."
    picked = _pick(usable, min_runs, "after")
    excluded += [{"run_id": r["run_id"], "reason": "Enough measured runs, so estimated ones aren't needed."}
                 for r in picked["left"]]
    use = picked["use"]  # with too few, the runs that did count (the figure stays Unknown)
    span = (min(r["_at"] for r in usable), max(r["_at"] for r in usable)) if usable else (None, None)
    after_id = "m-" + record_id("after_cu", oid, before["baseline_version"], key["definition_version"],
                                *sorted(f"{r['cu'].get('id')}={r['_cu']:.6f}/{r['_cls']}" for r in use))[:16]
    after = _figure(picked, METRIC, key["workload_key"], "after", after_id, span)
    delta = change(before["cu"], after, workload_key=key["workload_key"])
    b_cls, a_cls = before["cu"]["classification"], after["classification"]

    if outputs_match is False:
        result, reason = FAILED, "The outputs differ from before the change, so it isn't equivalent."
    elif b_cls == ms.UNKNOWN:
        result, reason = VALIDATING, f"The before baseline isn't known: {before['cu'].get('reason')}"
    elif a_cls == ms.UNKNOWN:
        result, reason = VALIDATING, after["reason"]
    elif delta["direction"] == "increase":
        result, reason = REGRESSED, "A run uses more CU after the change than before it."
    elif delta["classification"] != ms.MEASURED:
        result, reason = VALIDATING, "The before or after is only an estimate; verifying needs measured CU."
    elif outputs_match is None and not (outputs_not_measurable or "").strip():
        result, reason = VALIDATING, "The outputs weren't compared, and nothing says why they can't be."
    else:
        result = VERIFIED
        reason = ("Measured CU didn't go up on the same inputs, and the outputs match." if outputs_match else
                  "Measured CU didn't go up on the same inputs; output equivalence can't be measured: "
                  + outputs_not_measurable.strip())
    made = _time(at, "at") if at not in (None, "") else (span[1] or since)
    validation_id = "va-" + record_id("validation", oid, before["baseline_version"], after_id, outputs_match,
                                      result)[:16]
    row = {"validation_id": validation_id, "optimization_id": oid, "baseline_version": before["baseline_version"],
           "before_measurement_id": before["cu"]["id"], "after_measurement_id": after_id,
           "outputs_match": outputs_match, "result": result, "reason": reason, "at": _iso(made)}
    return {"row": row, "result": result, "moves_to": VERIFIED if result == VERIFIED else VALIDATING,
            "reason": reason, "before": before["cu"], "after": after, "change": delta, "excluded": excluded,
            "definition_version": key["definition_version"]}
