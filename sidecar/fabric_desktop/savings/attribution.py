"""The attribution waterfall: how much Fabric CU one run avoided, and what avoided it (ESI-303).

A workload has a baseline B: the Fabric CU one run used before any optimization (a versioned
baseline, measured or estimated). Optimizations change what a run needs; running on a PC avoids
the Fabric job altogether. Adding those up naively counts twice: a notebook optimized from 10 to 6
CU-hours and then run on a PC did not save 4 + 10. This module splits one run into layers that
never add up to more than B:

1. **Optimizations**, in the order they took effect (implementation time, then the opportunity
   engine's action order, then id). Each takes its measured reduction from what was left:
   O1 = B - after1, O2 = after1 - after2. What remains after them is R = B - sum(O).
2. **This PC**: a run on a PC avoids at most R (the optimized demand), not B. The avoided figure
   comes from ``calibration.py``; it is capped at R.
3. **Overhead** (OneLake reads and writes, sync): subtracted for net. Unknown overhead leaves net
   marked incomplete rather than equal to gross.

Edge cases, as the plan states them:

- **Regression.** An optimization whose after is higher than what came before has a negative layer
  (event type ``regression``); later layers start from the higher figure. Gross stays between 0 and B.
- **Unknown after.** An optimization whose after isn't known yet (still validating) can't be split
  from the ones after it, so its layer and every later optimization layer are Unknown, and the
  run's known gross (B minus the last known demand, plus this PC's share) goes to one
  ``unattributed`` layer. The total is the same; only the split waits. When the after is measured,
  the run is attributed again and the difference is written as corrections.
- **A run on a PC calibrated only against pre-optimization Fabric runs** (``local_basis`` =
  ``original``) avoids at most R as well; if the split is unknown, it goes to ``unattributed``
  capped at B.
- **A run in Fabric** has no PC layer: its benefit is only the optimization layers (it really used
  about R).

Each layer has a classification as weak as its inputs (``measurement.py``: measured beats
estimated beats unknown; scenario figures are refused). A layer's event id is the same on every PC
for the same run, layer, baseline version, pricing version and correction, so a second desktop
syncing the same run, or an assessment seeing the same problem again, adds nothing.

Pure: no I/O.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .. import measurement as ms
from ..optimize.shared import record_id
from ..opportunities.model import ACTION_ORDER

METRIC = "fabric_cu_avoided"
UNIT = ms.CU_HOURS
LOCAL, UNATTRIBUTED, OVERHEAD = "local", "unattributed", "overhead"
ACCRUAL, REGRESSION = "accrual", "regression"
FABRIC, PC = "fabric", "pc"
ORIGINAL, OPTIMIZED = "original", "optimized"
# A reverted, rejected or superseded optimization doesn't change what later runs need.
INACTIVE = ("reverted", "rejected", "dismissed", "superseded")
_STRENGTH = {ms.MEASURED: 0, ms.ESTIMATED: 1, ms.UNKNOWN: 2}


def _cu(m: dict[str, Any] | None, what: str) -> tuple[float | None, str]:
    """A CU measurement's value in CU-hours and its classification (None and unknown when missing)."""
    if not m or m.get("value") is None or m.get("classification") == ms.UNKNOWN:
        return None, ms.UNKNOWN
    if m.get("classification") == ms.SCENARIO:
        raise ValueError("A scenario figure isn't evidence of what a run used; attribution takes measured or "
                         "estimated CU only.")
    if m.get("classification") not in (ms.MEASURED, ms.ESTIMATED):
        raise ValueError(f"Unknown classification {m.get('classification')!r}.")
    value = float(m["value"])
    if not math.isfinite(value) or value < 0:
        # CU used is a finite figure of zero or more. A negative, NaN or infinite one would fabricate a
        # layer bigger than the baseline (NaN slips past comparisons and min()).
        raise ValueError(f"The {what} must be a finite CU figure of zero or more, not {value}.")
    return ms.convert(value, m["unit"], UNIT), m["classification"]


def _weakest(*classes: str) -> str:
    return max(classes, key=_STRENGTH.__getitem__)


def _time(value: Any) -> dt.datetime:
    t = ms.parse_time(value)
    if t is None:
        raise ValueError(f"Not a time: {value!r}")
    return t


def in_effect(optimizations: list[dict[str, Any]], run_at: Any) -> list[dict[str, Any]]:
    """The optimizations that applied to a run at ``run_at``, in waterfall order: implemented before
    the run, not reverted before it, and not rejected or superseded."""
    at = _time(run_at)
    out = []
    for o in optimizations:
        implemented = ms.parse_time(o.get("implemented_at"))
        if implemented is None or implemented > at:
            continue
        reverted = ms.parse_time(o.get("reverted_at"))
        if reverted is not None and reverted <= at:
            continue
        if o.get("state") in INACTIVE and reverted is None:
            continue
        out.append(o)
    order = {a: i for i, a in enumerate(ACTION_ORDER)}
    return sorted(out, key=lambda o: (_time(o["implemented_at"]), order.get(str(o.get("action")), len(order)),
                                      str(o.get("optimization_id"))))


def _layer(run: dict[str, Any], name: str, order: int, value: float | None, cls: str, *, optimization_id: Any = None,
           event_type: str = ACCRUAL, reason: str = "", pricing_version: Any = None, corrects: Any = None,
           optimization_ids: list[Any] | None = None) -> dict[str, Any]:
    value = None if cls == ms.UNKNOWN else round(float(value or 0.0), 6)
    return {"execution_id": run["execution_id"], "workload_key": run["workload_key"],
            "baseline_version": run["baseline_version"], "layer": name, "layer_order": order,
            "optimization_id": optimization_id, "optimization_ids": optimization_ids,
            "event_type": event_type if cls != ms.UNKNOWN else ACCRUAL,
            "classification": cls, "cu_hours": value, "reason": reason,
            "ledger_event_id": record_id("savings", event_type, run["execution_id"], run["workload_key"], name,
                                         run["baseline_version"], pricing_version, corrects)}


def attribute(run: dict[str, Any], baseline: dict[str, Any], optimizations: list[dict[str, Any]] | tuple[()] = (),
              *, local_avoided: dict[str, Any] | None = None, local_basis: str = OPTIMIZED,
              overhead: dict[str, Any] | None = None, pricing_version: Any = None,
              corrects: Any = None) -> dict[str, Any]:
    """Split one run's avoided Fabric CU into layers.

    ``run``: execution_id, workload_key, baseline_version, at (when it ran), ran (``fabric`` or ``pc``).
    ``baseline``: the workload's Fabric CU per run before any optimization, a measurement.
    ``optimizations``: each with optimization_id, implemented_at, action, state, reverted_at
    (optional) and ``after``: the measured or estimated Fabric CU per run once it was in place.
    ``local_avoided``: for a run on a PC, ``calibration.calibrate``'s figure; ``local_basis`` says
    whether it was calibrated against Fabric runs from before the optimizations (``original``) or
    after them (``optimized``). ``overhead``: Fabric CU the run still caused (OneLake I/O, sync).

    Returns the layers, gross (never more than the baseline, never less than 0), net, and whether the
    figures may go to the ledger (only when the baseline is measured: decision of 2026-11-06)."""
    for key in ("execution_id", "workload_key", "baseline_version", "at", "ran"):
        if not run.get(key):
            raise ValueError(f"A run needs {key}.")
    if run["ran"] not in (FABRIC, PC):
        raise ValueError("ran is fabric or pc.")
    if local_basis not in (ORIGINAL, OPTIMIZED):
        raise ValueError("local_basis is original or optimized.")
    b, b_cls = _cu(baseline, "baseline")
    applied = in_effect(list(optimizations), run["at"])
    extra = {"pricing_version": pricing_version, "corrects": corrects}
    layers: list[dict[str, Any]] = []
    if b is None:
        # Without a baseline nothing can be split or capped: every layer is unknown.
        for i, o in enumerate(applied):
            layers.append(_layer(run, str(o["optimization_id"]), i, None, ms.UNKNOWN,
                                 optimization_id=o["optimization_id"], reason="No baseline for this workload", **extra))
        if run["ran"] == PC:
            layers.append(_layer(run, LOCAL, len(applied), None, ms.UNKNOWN, reason="No baseline for this workload",
                                 **extra))
        return _result(run, b, b_cls, layers, None, ms.UNKNOWN, overhead, applied) | {"local_basis": local_basis}

    current, current_cls = b, b_cls  # what a run needs after the layers so far
    split_known = True
    pending: list[Any] = []  # optimizations whose share can't be split yet
    for i, o in enumerate(applied):
        after, after_cls = _cu(o.get("after"), f"after of {o['optimization_id']}")
        if not split_known or after is None:
            split_known = False
            pending.append(o["optimization_id"])
            layers.append(_layer(run, str(o["optimization_id"]), i, None, ms.UNKNOWN,
                                 optimization_id=o["optimization_id"],
                                 reason="Its after isn't measured yet, so its share can't be split", **extra))
            continue
        cls = _weakest(current_cls, after_cls)
        delta = current - after
        layers.append(_layer(run, str(o["optimization_id"]), i, delta, cls, optimization_id=o["optimization_id"],
                             event_type=REGRESSION if delta < 0 else ACCRUAL,
                             reason="Used more after this change than before it" if delta < 0 else "", **extra))
        current, current_cls = after, cls

    order = len(applied)
    # R, what the optimized workload needs. The optimization layers add up to B - R, so capping this
    # PC's share at R keeps gross at or under B, a regression (R > B) included.
    remaining = max(0.0, current)
    if run["ran"] == PC:
        avoided, avoided_cls = _cu(local_avoided, "local avoided figure")
        if avoided is None:
            layers.append(_layer(run, LOCAL, order, None, ms.UNKNOWN,
                                 reason="No Fabric run of this workload to compare with", **extra))
        elif split_known:
            cls = _weakest(avoided_cls, current_cls)
            layers.append(_layer(run, LOCAL, order, min(avoided, remaining), cls,
                                 reason="Capped at what the optimized workload needs" if avoided > remaining else "",
                                 **extra))
        else:
            # The optimizations' split waits for their afters; the run's total doesn't.
            known = sum(x["cu_hours"] for x in layers if x["cu_hours"] is not None)
            cls = _weakest(avoided_cls, b_cls)
            gross_left = max(0.0, min(avoided, b, b - known))
            layers.append(_layer(run, UNATTRIBUTED, order, gross_left, cls, optimization_ids=pending,
                                 reason="Split between these optimizations and this PC once their afters are measured",
                                 **extra))
    known = [x for x in layers if x["cu_hours"] is not None]
    gross = max(0.0, min(b, sum(x["cu_hours"] for x in known)))
    gross_cls = _weakest(*(x["classification"] for x in known)) if known else ms.UNKNOWN
    return _result(run, b, b_cls, layers, gross if known else None, gross_cls, overhead, applied) | {
        "local_basis": local_basis}


def _result(run: dict[str, Any], b: float | None, b_cls: str, layers: list[dict[str, Any]], gross: float | None,
            gross_cls: str, overhead: dict[str, Any] | None, applied: list[dict[str, Any]]) -> dict[str, Any]:
    complete = all(x["classification"] != ms.UNKNOWN for x in layers)
    if run["ran"] == PC:
        o, o_cls = _cu(overhead, "overhead")
    else:
        o, o_cls = 0.0, ms.MEASURED  # a Fabric run's own use is already what it needed
    if gross is None:
        net, net_cls = None, ms.UNKNOWN
    elif o is None:
        net, net_cls = gross, gross_cls  # shown as gross, flagged incomplete
        complete = False
    else:
        net, net_cls = max(0.0, gross - o), _weakest(gross_cls, o_cls)
    return {"execution_id": run["execution_id"], "workload_key": run["workload_key"],
            "baseline_version": run["baseline_version"], "at": run["at"], "ran": run["ran"], "baseline_cu_hours": b, "baseline_classification": b_cls,
            "layers": layers, "optimizations": [o_["optimization_id"] for o_ in applied],
            "gross_cu_hours": None if gross is None else round(gross, 6), "gross_classification": gross_cls,
            "overhead_cu_hours": None if o is None else round(o, 6),
            "net_cu_hours": None if net is None else round(net, 6), "net_classification": net_cls,
            "complete": complete and o is not None,
            "net_includes_overhead": o is not None,
            "ledger_ready": b_cls == ms.MEASURED}
