"""Execution ledger accruals: one run's CU avoided as ledger events (ESI-203).

Every accrual in the savings ledger is per execution: one observed run of a workload, in Fabric or
on a PC. A day, week or month is only a grouping of these by run time (plan section 4.5). ``accrue``
turns one ``attribution.attribute`` result into those events: one ``accrual`` (or ``regression``)
per layer of the waterfall, shaped as the ``fdopt_savings`` columns of schema version 2, plus the
run's gross, overhead and net. Until Phase 3 records optimizations, a run has one layer: this PC
(``local``). Several layers work the same way.

- **Same run, same ids.** Each event's id is the one ``attribute`` gave its layer: ``record_id``
  over the event type, run, workload, layer, baseline version, pricing version and ``corrects``
  (empty for an accrual). The same run accrued twice, or on two PCs, gives the same events, and
  ``unsynced`` finds nothing new to add. Corrections (``corrections.py``) point at these ids through
  ``corrects``, so an accrual and its corrections form one chain.
- **Kinds stay apart.** Each event keeps its layer's classification: Measured, Estimated or
  Unknown. An Unknown layer is an event with no figure (None), never 0, and is left out of totals.
  Scenario figures are refused: a price is never CU avoided.
- **Gross, overhead and net.** Gross is never more than the baseline and never less than 0; net is
  never more than gross. Both are checked, and a result that breaks them is refused. Unknown
  overhead leaves net shown as gross and marked incomplete.
- **The gate (decision of 2026-11-06).** No savings rows are written until a measured Fabric
  baseline exists for the workload. ``writable`` is True only when the baseline is Measured. An
  estimated or unknown baseline gives the same events with ``writable`` False and the reason in
  plain words, so they can be shown on this PC. A layer that is itself Estimated (a run on a PC is
  calibrated, so it always is) keeps that label on its row.

Pure: nothing here reads or writes a lakehouse, Fabric or this PC. ``written`` is always False.
"""

from __future__ import annotations

import math
from typing import Any

from .. import measurement as ms
from ..optimize import schema
from ..optimize.shared import record_id
from ..timeutil import iso
from .attribution import ACCRUAL, LOCAL, METRIC, REGRESSION, UNATTRIBUTED, UNIT
from .corrections import TOLERANCE, _check

TABLE = schema.table("savings")
# Every column of fdopt_savings at schema version 2: provenance, version 1's and version 2's.
COLUMNS = tuple(schema.expected(2)[TABLE])
# What the waterfall says that fdopt_savings v2 has no column for. Corrections and reconciliation
# read these, so the events carry them beside the columns.
NO_COLUMN = ("ledger_event_id", "layer", "layer_order", "optimization_ids", "cu_hours", "reason")
# Who did it (tenant and user) is left for a later schema version, as schema.py says.
PROVENANCE_FROM_WRITER = ("installation_id", "created_at", "app_version", "schema_version")
# The attribution column: what a layer credits.
OPTIMIZATION, LOCAL_EXECUTION = "optimization", "local_execution"
_CATEGORY = {ms.MEASURED: "measured_cu", ms.ESTIMATED: "estimated_cu"}
SCOPE_KEYS = ("artifact_key", "workspace_id", "capacity_id", "source", "counts_for")
NOT_READY = ("The baseline for workload {w} is {c}, not measured. No savings rows are written until a "
             "measured Fabric baseline exists for it, so these figures are shown on this PC only.")


def _attribution(layer: dict[str, Any]) -> str:
    if layer["layer"] == LOCAL:
        return LOCAL_EXECUTION
    if layer["layer"] == UNATTRIBUTED:
        return UNATTRIBUTED
    return OPTIMIZATION


def _figure(value: Any, what: str, eid: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"Run {eid} can't be accrued: its {what} must be a finite number of CU-hours, "
                         f"not {value!r}.")
    return float(value)


def _event(result: dict[str, Any], layer: dict[str, Any], pricing_version: Any,
           scope: dict[str, Any]) -> dict[str, Any]:
    at = iso(result["at"])
    row: dict[str, Any] = dict.fromkeys(COLUMNS)
    row.update({
        "record_id": layer["ledger_event_id"], "savings_id": layer["ledger_event_id"],
        "kind": "execution",  # the ledger's only accrual grain
        "category": _CATEGORY.get(layer["classification"]), "classification": layer["classification"],
        "workload_key": result["workload_key"], "metric": METRIC, "unit": UNIT, "value": layer["cu_hours"],
        "method": "attribution waterfall", "period_start": at, "period_end": at,
        "event_type": layer["event_type"],
        "optimization_id": None if layer.get("optimization_id") is None else str(layer["optimization_id"]),
        "execution_id": result["execution_id"],
        "money": None, "currency": None,  # CU avoided only; a price is a Scenario and never a saving
        "pricing_version": None if pricing_version is None else str(pricing_version),
        "baseline_version": result["baseline_version"], "attribution": _attribution(layer), "corrects": None,
    })
    row.update({k: scope.get(k) for k in SCOPE_KEYS})
    row.update({"ledger_event_id": layer["ledger_event_id"], "layer": str(layer["layer"]),
                "layer_order": layer.get("layer_order"), "optimization_ids": layer.get("optimization_ids"),
                "cu_hours": layer["cu_hours"], "reason": layer.get("reason") or ""})
    return row


def accrue(result: dict[str, Any], *, pricing_version: Any = None,
           scope: dict[str, Any] | None = None) -> dict[str, Any]:
    """The ledger events for one run, from its ``attribution.attribute`` result.

    ``pricing_version``: the one ``attribute`` was called with (part of each event id).
    ``scope``: optional ``artifact_key``, ``workspace_id``, ``capacity_id``, ``source`` and
    ``counts_for`` for the rows.

    Returns the run's ``events`` in layer order, its gross, overhead and net, ``net_complete``,
    ``ledger_ready`` and ``writable`` (True only for a Measured baseline), ``reason`` (why not
    writable, or empty) and ``written`` (always False: this module writes nothing)."""
    if not isinstance(result, dict):
        raise ValueError("Accruals are made from one attribution result, a dict.")
    eid = result.get("execution_id")
    for key in ("execution_id", "workload_key", "baseline_version", "at", "layers"):
        if result.get(key) is None:
            raise ValueError(f"Run {eid} can't be accrued: its attribution has no {key}.")
    if iso(result["at"]) is None:
        raise ValueError(f"Run {eid} can't be accrued: {result['at']!r} isn't a time it ran.")
    scope = dict(scope or {})
    unknown_keys = sorted(set(scope) - set(SCOPE_KEYS))
    if unknown_keys:
        raise ValueError(f"Run {eid} can't be accrued: the scope has {', '.join(unknown_keys)}, which the "
                         f"ledger doesn't hold. It takes {', '.join(SCOPE_KEYS)}.")

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for layer in sorted(result["layers"], key=lambda x: (x.get("layer_order") is None, x.get("layer_order") or 0,
                                                         str(x.get("layer")))):
        try:
            _check(layer)
        except ValueError as exc:
            raise ValueError(f"Run {eid} can't be accrued: {exc}") from None
        name = str(layer["layer"])
        if layer["execution_id"] != eid:
            raise ValueError(f"Run {eid} can't be accrued: its layer {name} is for run {layer['execution_id']}.")
        if layer.get("workload_key") not in (None, result["workload_key"]) or \
                layer.get("baseline_version") not in (None, result["baseline_version"]):
            raise ValueError(f"Run {eid} can't be accrued: its layer {name} is for another workload or "
                             "baseline version.")
        if name in seen:
            raise ValueError(f"Run {eid} can't be accrued: it has layer {name} twice.")
        seen.add(name)
        if layer["event_type"] not in (ACCRUAL, REGRESSION):
            raise ValueError(f"Run {eid} can't be accrued: its layer {name} is a {layer['event_type']}, and an "
                             "attribution has accruals and regressions only.")
        want = record_id("savings", layer["event_type"], eid, result["workload_key"], name,
                         result["baseline_version"], pricing_version, None)
        if layer["ledger_event_id"] != want:
            raise ValueError(f"Run {eid} can't be accrued: the id of its layer {name} isn't the one this run "
                             "gives. It was attributed with another pricing version or as a correction, or "
                             "changed since.")
        events.append(_event(result, layer, pricing_version, scope))

    b = _figure(result.get("baseline_cu_hours"), "baseline", eid)
    b_cls = result.get("baseline_classification")
    gross = _figure(result.get("gross_cu_hours"), "gross", eid)
    overhead = _figure(result.get("overhead_cu_hours"), "overhead", eid)
    net = _figure(result.get("net_cu_hours"), "net", eid)
    if b_cls not in (ms.MEASURED, ms.ESTIMATED, ms.UNKNOWN) or (b is None) != (b_cls == ms.UNKNOWN):
        raise ValueError(f"Run {eid} can't be accrued: its baseline is {b_cls!r} with figure {b!r}; a baseline "
                         "is Measured or Estimated with a figure, or Unknown without one.")
    if bool(result.get("ledger_ready")) != (b_cls == ms.MEASURED):
        raise ValueError(f"Run {eid} can't be accrued: it says ledger_ready is {result.get('ledger_ready')!r} "
                         f"but its baseline is {b_cls}. Only a measured baseline is ready for the ledger.")
    known = [e["cu_hours"] for e in events if e["cu_hours"] is not None]
    if b is None and (known or gross is not None):
        raise ValueError(f"Run {eid} can't be accrued: it has CU avoided without a baseline to cap it.")
    if (gross is None) != (not known):
        raise ValueError(f"Run {eid} can't be accrued: its gross doesn't fit its layers.")
    if gross is not None:
        if gross < -TOLERANCE or gross > b + TOLERANCE:  # type: ignore[operator]
            raise ValueError(f"Run {eid} can't be accrued: its gross ({gross:g} CU-hours) is outside 0 to its "
                             f"baseline ({b:g}).")
        if sum(known) > b + TOLERANCE * len(known):  # type: ignore[operator]  # each layer is rounded
            raise ValueError(f"Run {eid} can't be accrued: its layers add up to {sum(known):g} CU-hours, more "
                             f"than its baseline ({b:g}).")
    if overhead is not None and overhead < 0:
        raise ValueError(f"Run {eid} can't be accrued: its overhead ({overhead:g} CU-hours) is negative.")
    if (gross is None) != (net is None):
        raise ValueError(f"Run {eid} can't be accrued: it has a gross or a net, but not both.")
    if net is not None and (net > gross + TOLERANCE or net < -TOLERANCE):  # type: ignore[operator]
        raise ValueError(f"Run {eid} can't be accrued: its net ({net:g} CU-hours) is more than its gross "
                         f"({gross:g}) or below 0.")
    complete = bool(result.get("complete")) and overhead is not None
    if overhead is None and result.get("complete"):
        raise ValueError(f"Run {eid} can't be accrued: it says it is complete with Unknown overhead.")

    ready = b_cls == ms.MEASURED
    reason = "" if ready else NOT_READY.format(w=result["workload_key"], c=ms.LABELS[b_cls])
    return {"execution_id": eid, "workload_key": result["workload_key"],
            "baseline_version": result["baseline_version"], "at": iso(result["at"]),
            "pricing_version": pricing_version, "events": events,
            "baseline_cu_hours": b, "baseline_classification": b_cls,
            "gross_cu_hours": gross, "gross_classification": result.get("gross_classification", ms.UNKNOWN),
            "overhead_cu_hours": overhead,
            "net_cu_hours": net, "net_classification": result.get("net_classification", ms.UNKNOWN),
            "net_complete": complete, "net_includes_overhead": overhead is not None,
            "ledger_ready": ready, "writable": ready, "reason": reason, "written": False}


def unsynced(events: list[dict[str, Any]], ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The events the ledger doesn't hold yet, by event id: a resync of a run already in the
    ledger gives nothing. An event whose id is there with another figure isn't new: the run was
    attributed again, which is a correction (``corrections.correct``), so it is refused."""
    have = {e.get("ledger_event_id"): e for e in ledger}
    out = []
    for e in events:
        seen = have.get(e["ledger_event_id"])
        if seen is None:
            out.append(e)
            have[e["ledger_event_id"]] = e
            continue
        if (seen.get("cu_hours"), seen.get("classification")) != (e["cu_hours"], e["classification"]):
            raise ValueError(f"Run {e['execution_id']} already has layer {e['layer']} in the ledger as "
                             f"{seen.get('cu_hours')!r} ({seen.get('classification')}), not {e['cu_hours']!r} "
                             f"({e['classification']}). A changed figure is written as a correction, not "
                             "as a second accrual.")
    return out

