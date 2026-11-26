"""Optimization demand changes as inputs for the capacity planner, verified and estimated apart (ESI-602).

The capacity planner (OE-040 demand model, OE-041 consolidation simulator in
``docs/optimize/engine-plan.md``) isn't built yet, so there is no planner contract to fit. This
module shapes what the savings program knows into the input the planner can read, and builds no
planner logic. The contract it offers (``CONTRACT``, version 1) is this:

- **Two inputs, never merged.** Per capacity, and per workload on it, the Fabric CU demand change
  comes as ``verified`` and ``estimated``, in that order. ``verified`` holds only optimizations whose
  latest validation (``baselines.validate``) is Verified, and only their Measured accruals.
  ``estimated`` holds the rest: Estimated accruals, and optimizations still Validating, Regressed
  (which moves back to Validating) or not validated yet. The two are never added together, and no
  field holds their sum.
- **What a change is.** From accrual events (``accruals.accrue``): the CU avoided by each
  optimization layer of each run. Demand change is the negative of CU avoided: below 0 means the
  capacity needs less, above 0 means it needs more. ``reduction_cu_hours`` and ``increase_cu_hours``
  are also given apart. Each workload also lists the per-run change of each optimization from its
  latest validation.
- **Regressions count.** A regression event, or a Regressed validation, is an increase in demand.
  It is included, labelled ``increase (regression)``, and its optimization is listed under
  ``regressions``.
- **Unknown stays unknown.** A run whose CU avoided isn't known makes its input's change None with
  the reason, never 0 and never an average of the others. An input with no runs is None with the
  reason too.
- **Not enough to right-size.** Every capacity, workload and the result itself has
  ``sufficient_for_right_sizing`` False, with the reason: an average change in demand doesn't show
  peaks, concurrency or throttling. The planner reads those from its own inputs (imported Capacity
  Metrics timepoints). Nothing here recommends a SKU, a resize, a pause or a retirement.
- **Left out, with a reason.** Layers that aren't an optimization's (this PC, unattributed),
  optimizations whose latest validation Failed, and runs whose capacity isn't known are listed
  under ``excluded`` or ``unplaced``, never counted.

Inputs: ``validations`` are ``baselines.validate`` results. ``accruals`` are accrual events, or
``accruals.accrue`` results whose ``events`` are read. ``optimizations`` are ``links`` proposals
(``from_finding``, ``from_opportunity``); their link rows give an optimization's capacity when an
event doesn't name one. Every optimization a validation or event names must be among them.

Pure: nothing is read or written, and the inputs are not changed. Bad input is refused with a
ValueError in a full sentence. The same input gives the same output in the same order.
"""

from __future__ import annotations

import math
from typing import Any

from .. import measurement as ms
from .accruals import OPTIMIZATION
from .attribution import ACCRUAL, REGRESSION
from .baselines import FAILED, REGRESSED, RESULTS, VERIFIED

CONTRACT = "esi-602/1"
PLANNER = ("OE-040", "OE-041")
VERIFIED_INPUT, ESTIMATED_INPUT = "verified", "estimated"
INPUTS = (VERIFIED_INPUT, ESTIMATED_INPUT)  # verified first
UNIT = ms.CU_HOURS
REDUCTION, INCREASE, NO_CHANGE, UNKNOWN_CHANGE = "reduction", "increase (regression)", "no change", "unknown"
NOT_VALIDATED = "not_validated"
RIGHT_SIZING_REASON = ("An average change in demand isn't enough to right-size a capacity. The planner needs peak, "
                       "concurrency and throttling evidence, and it reads those from its own inputs (OE-040). "
                       "Nothing here recommends a SKU.")
_TOL = 1e-9


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


# ---------- checking the inputs


def _optimizations(optimizations: Any) -> dict[str, set[str]]:
    """Each optimization id with the capacities its link rows name."""
    if not isinstance(optimizations, list):
        raise ValueError("The optimizations must be a list of proposals from savings/links.py.")
    out: dict[str, set[str]] = {}
    for p in optimizations:
        if not isinstance(p, dict) or not isinstance(p.get("record"), dict):
            raise ValueError("Each optimization must be a proposal from savings/links.py, with its record and links.")
        oid = _text(p["record"].get("optimization_id"))
        if not oid:
            raise ValueError("An optimization record has no optimization_id, so nothing can be matched to it.")
        links = p.get("links") or []
        if not isinstance(links, list) or not all(isinstance(x, dict) for x in links):
            raise ValueError(f"Optimization {oid} has links that aren't optimization_link rows.")
        caps = out.setdefault(oid, set())
        for x in links:
            if _text(x.get("optimization_id")) not in ("", oid):
                raise ValueError(f"Optimization {oid} has a link row for optimization {x.get('optimization_id')}.")
            if _text(x.get("capacity_id")):
                caps.add(_text(x["capacity_id"]))
    return out


def _validations(validations: Any, known: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
    """The latest validation of each optimization. Two different results at one time are refused."""
    if not isinstance(validations, list):
        raise ValueError("The validations must be a list of results from baselines.validate.")
    latest: dict[str, dict[str, Any]] = {}
    for v in validations:
        if not isinstance(v, dict) or not isinstance(v.get("row"), dict) or not isinstance(v.get("change"), dict):
            raise ValueError("Each validation must be a result of baselines.validate, with its row and change.")
        row = v["row"]
        oid = _text(row.get("optimization_id"))
        if not oid:
            raise ValueError("A validation has no optimization_id, so it can't be matched to an optimization.")
        if oid not in known:
            raise ValueError(f"Validation {row.get('validation_id')} is of optimization {oid}, which isn't among "
                             "the optimization records given.")
        if v.get("result") not in RESULTS or row.get("result") != v.get("result"):
            raise ValueError(f"Validation {row.get('validation_id')} of {oid} has result {v.get('result')!r}; it "
                             f"must be one of {', '.join(RESULTS)}, the same on the result and its row.")
        at = ms.parse_time(row.get("at"))
        if at is None:
            raise ValueError(f"Validation {row.get('validation_id')} of {oid} has no time, so the latest can't be "
                             "told.")
        c = v["change"]
        if c.get("classification") not in (ms.MEASURED, ms.ESTIMATED, ms.UNKNOWN):
            raise ValueError(f"Validation {row.get('validation_id')} of {oid} has a change classified "
                             f"{c.get('classification')!r}; it must be Measured, Estimated or Unknown.")
        signed = c.get("change_cu_hours")
        if (signed is None) != (c["classification"] == ms.UNKNOWN):
            raise ValueError(f"Validation {row.get('validation_id')} of {oid} has a change that is known without "
                             "a figure, or unknown with one.")
        if signed is not None and (isinstance(signed, bool) or not isinstance(signed, (int, float))
                                   or not math.isfinite(signed)):
            raise ValueError(f"Validation {row.get('validation_id')} of {oid} has a change of {signed!r}; it must "
                             "be a finite number of CU hours.")
        if v["result"] == VERIFIED and (c["classification"] != ms.MEASURED or signed > _TOL):  # type: ignore[operator]
            raise ValueError(f"Validation {row.get('validation_id')} of {oid} says Verified, but its change isn't "
                             "a measured reduction or no change.")
        seen = latest.get(oid)
        if seen is not None:
            seen_at = ms.parse_time(seen["row"]["at"])
            assert seen_at is not None  # checked when it was kept
            if at == seen_at and seen["result"] != v["result"]:
                raise ValueError(f"Optimization {oid} has two validations at {row['at']} with different results, "
                                 f"{seen['result']} and {v['result']}. Keep one.")
            if at <= seen_at:
                continue
        latest[oid] = v
    return latest


def _events(accruals: Any, known: dict[str, set[str]]) -> list[dict[str, Any]]:
    """The accrual events, each once, checked. The same event twice counts once; with two figures it is refused."""
    if not isinstance(accruals, list):
        raise ValueError("The accruals must be a list of accrual events or results of accruals.accrue.")
    flat: list[Any] = []
    for a in accruals:
        flat.extend(a["events"] if isinstance(a, dict) and isinstance(a.get("events"), list) else [a])
    out: dict[str, dict[str, Any]] = {}
    for e in flat:
        if not isinstance(e, dict):
            raise ValueError("Each accrual event must be a ledger event (a dict) from accruals.accrue.")
        eid = _text(e.get("ledger_event_id"))
        if not eid:
            raise ValueError("An accrual event has no ledger_event_id, so it can't be counted once.")
        if e.get("corrects") not in (None, ""):
            raise ValueError(f"Event {eid} is a correction. Pass accrual events only; corrections are applied by "
                             "savings/corrections.py first.")
        if e.get("event_type") not in (ACCRUAL, REGRESSION):
            raise ValueError(f"Event {eid} is a {e.get('event_type')!r}; only accruals and regressions are demand "
                             "changes.")
        cls, v = e.get("classification"), e.get("cu_hours")
        if cls not in (ms.MEASURED, ms.ESTIMATED, ms.UNKNOWN):
            raise ValueError(f"Event {eid} is classified {cls!r}; it must be Measured, Estimated or Unknown.")
        if (v is None) != (cls == ms.UNKNOWN):
            raise ValueError(f"Event {eid} is {cls} with figure {v!r}; a known event has a figure and an unknown "
                             "one has none.")
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"Event {eid} has CU avoided of {v!r}; it must be a finite number of CU hours.")
            if (e["event_type"] == REGRESSION) != (v < 0):
                raise ValueError(f"Event {eid} is a {e['event_type']} with CU avoided of {v!r}. A regression avoids "
                                 "less than 0 and an accrual 0 or more.")
        if not _text(e.get("workload_key")):
            raise ValueError(f"Event {eid} has no workload_key.")
        oid = _text(e.get("optimization_id"))
        if e.get("attribution") == OPTIMIZATION and oid not in known:
            raise ValueError(f"Event {eid} is of optimization {oid or 'none'}, which isn't among the optimization "
                             "records given.")
        seen = out.get(eid)
        if seen is not None:
            if (seen.get("cu_hours"), seen.get("classification")) != (v, cls):
                raise ValueError(f"Event {eid} appears twice with different figures. A changed figure is a "
                                 "correction, so keep one.")
            continue
        out[eid] = e
    return [out[k] for k in sorted(out)]


# ---------- shaping


def _status(oid: str, latest: dict[str, dict[str, Any]]) -> str:
    v = latest.get(oid)
    return NOT_VALIDATED if v is None else str(v["result"])


def _bucket(name: str) -> dict[str, Any]:
    return {"input": name, "events": [], "optimizations": set(), "regressions": set(), "per_run": {}}


def _per_run(oid: str, v: dict[str, Any] | None) -> dict[str, Any]:
    """One optimization's per-run demand change from its latest validation, labelled."""
    if v is None:
        return {"optimization_id": oid, "validation_id": None, "result": NOT_VALIDATED,
                "per_run_change_cu_hours": None, "classification": ms.UNKNOWN, "label": UNKNOWN_CHANGE,
                "reason": "It hasn't been validated yet, so its per-run change isn't known."}
    c = v["change"]
    signed = c.get("change_cu_hours")
    if signed is None:
        label, reason = UNKNOWN_CHANGE, str(c.get("reason") or v.get("reason") or "Its change isn't known.")
    elif v["result"] == REGRESSED or signed > _TOL:
        label, reason = INCREASE, "A run needs more CU after the change than before it."
    elif signed < -_TOL:
        label, reason = REDUCTION, ""
    else:
        label, reason = NO_CHANGE, ""
    return {"optimization_id": oid, "validation_id": v["row"].get("validation_id"), "result": v["result"],
            "per_run_change_cu_hours": None if signed is None else round(float(signed), 6),
            "classification": c["classification"], "label": label, "reason": reason}


def _finish(b: dict[str, Any]) -> dict[str, Any]:
    """A bucket's totals. Unknown runs or no runs give None with the reason, never 0."""
    events = sorted(b["events"], key=lambda e: e["ledger_event_id"])
    unknown = [e for e in events if e["cu_hours"] is None]
    known = [e for e in events if e["cu_hours"] is not None]
    times = sorted(t for t in (_text(e.get("period_start")) for e in events) if t)
    out: dict[str, Any] = {
        "input": b["input"], "unit": UNIT, "runs": len({e.get("execution_id") for e in events}),
        "events": len(events), "unknown_events": len(unknown),
        "measured_events": sum(1 for e in known if e["classification"] == ms.MEASURED),
        "estimated_events": sum(1 for e in known if e["classification"] == ms.ESTIMATED),
        "period_start": times[0] if times else None, "period_end": times[-1] if times else None,
        "optimization_ids": sorted(b["optimizations"]), "regressions": sorted(b["regressions"]),
        "per_run_changes": [b["per_run"][k] for k in sorted(b["per_run"])],
        "event_ids": [e["ledger_event_id"] for e in events]}
    if not events:
        why = "No accrued runs are in this input, so its demand change isn't known."
    elif unknown:
        why = (f"{len(unknown)} of its {len(events)} events have CU avoided that isn't known, so the demand change "
               "isn't known. It isn't treated as 0 or averaged from the others.")
    else:
        why = ""
    if why:
        out.update(demand_change_cu_hours=None, reduction_cu_hours=None, increase_cu_hours=None, reason=why)
    else:
        red = sum(e["cu_hours"] for e in known if e["cu_hours"] > 0)
        inc = -sum(e["cu_hours"] for e in known if e["cu_hours"] < 0)
        out.update(demand_change_cu_hours=round(inc - red, 6), reduction_cu_hours=round(red, 6),
                   increase_cu_hours=round(inc, 6), reason="")
    if b["input"] == VERIFIED_INPUT:
        out["classification"] = ms.MEASURED if not unknown and events else ms.UNKNOWN
    else:
        out["classification"] = ms.ESTIMATED if not unknown and events else ms.UNKNOWN
    return out


def _not_enough() -> dict[str, Any]:
    return {"sufficient_for_right_sizing": False, "right_sizing_reason": RIGHT_SIZING_REASON}


def demand_inputs(validations: list[dict[str, Any]], accruals: list[dict[str, Any]],
                  optimizations: list[dict[str, Any]]) -> dict[str, Any]:
    """Per capacity, and per workload on it, the CU demand change as two planner inputs: verified, then estimated.

    Returns ``contract``, ``planner``, ``inputs`` (the order of the two inputs), ``capacities`` (each with
    ``verified``, ``estimated`` and ``workloads``, sorted by id), ``optimizations`` (which input each
    one feeds and its per-run change), ``unplaced`` (events and validations with no single capacity),
    ``excluded`` (what isn't an optimization's demand change, with the reason) and, everywhere,
    ``sufficient_for_right_sizing`` False with the reason. Raises ValueError on bad input."""
    known = _optimizations(optimizations)
    latest = _validations(validations, known)
    events = _events(accruals, known)

    caps: dict[str, dict[str, Any]] = {}
    unplaced: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    fed: dict[str, set[str]] = {}

    def entry(cap: str) -> dict[str, Any]:
        return caps.setdefault(cap, {"buckets": {n: _bucket(n) for n in INPUTS}, "workloads": {}})

    def slot(cap: str, wl: str) -> dict[str, Any]:
        return entry(cap)["workloads"].setdefault(wl, {n: _bucket(n) for n in INPUTS})

    def capacity_of(oid: str, named: Any) -> tuple[str, str]:
        if _text(named):
            return _text(named), ""
        linked = sorted(known.get(oid, ()))
        if len(linked) == 1:
            return linked[0], ""
        if not linked:
            return "", "Neither it nor its optimization's links name a capacity."
        return "", f"Its optimization is linked to {len(linked)} capacities ({', '.join(linked)}), so it can't be placed."

    for e in events:
        eid, oid = e["ledger_event_id"], _text(e.get("optimization_id"))
        if e.get("attribution") != OPTIMIZATION:
            excluded.append({"kind": "event", "id": eid, "optimization_id": oid or None,
                             "reason": (f"It is a {e.get('attribution') or 'unnamed'} layer, not an optimization's "
                                        "demand change. Runs moved off Fabric reach the planner through its own "
                                        "usage inputs.")})
            continue
        status = _status(oid, latest)
        if status == FAILED:
            excluded.append({"kind": "event", "id": eid, "optimization_id": oid,
                             "reason": "Its optimization's latest validation Failed (the outputs differ), so its "
                                       "demand change isn't one to plan on."})
            continue
        cap, why = capacity_of(oid, e.get("capacity_id"))
        if not cap:
            unplaced.append({"kind": "event", "id": eid, "optimization_id": oid, "reason": why})
            continue
        verified = status == VERIFIED and e["classification"] in (ms.MEASURED, ms.UNKNOWN)
        name = VERIFIED_INPUT if verified else ESTIMATED_INPUT
        wl = _text(e["workload_key"])
        for b in (entry(cap)["buckets"][name], slot(cap, wl)[name]):
            b["events"].append(e)
            b["optimizations"].add(oid)
            if e["event_type"] == REGRESSION or status == REGRESSED:
                b["regressions"].add(oid)
            b["per_run"][oid] = _per_run(oid, latest.get(oid))
        fed.setdefault(oid, set()).add(name)

    # Validated optimizations with no accrued runs still show their per-run change on their workload.
    for oid in sorted(latest):
        v = latest[oid]
        if v["result"] == FAILED:
            excluded.append({"kind": "validation", "id": v["row"].get("validation_id"), "optimization_id": oid,
                             "reason": "Its latest validation Failed (the outputs differ), so its demand change "
                                       "isn't one to plan on."})
            continue
        name = VERIFIED_INPUT if v["result"] == VERIFIED else ESTIMATED_INPUT
        if name in fed.get(oid, set()):
            continue
        wl = _text(v["change"].get("workload_id")) or _text((v.get("before") or {}).get("workload_id"))
        cap, why = capacity_of(oid, None)
        if not cap or not wl:
            unplaced.append({"kind": "validation", "id": v["row"].get("validation_id"), "optimization_id": oid,
                             "reason": why or "It doesn't name its workload."})
            continue
        for b in (entry(cap)["buckets"][name], slot(cap, wl)[name]):
            b["optimizations"].add(oid)
            if v["result"] == REGRESSED:
                b["regressions"].add(oid)
            b["per_run"][oid] = _per_run(oid, v)
        fed.setdefault(oid, set()).add(name)

    capacities = []
    for cap in sorted(caps):
        c = caps[cap]
        capacities.append({"capacity_id": cap, **{n: _finish(c["buckets"][n]) for n in INPUTS},
                           "workloads": [{"workload_key": wl, **{n: _finish(w[n]) for n in INPUTS}, **_not_enough()}
                                         for wl, w in sorted(c["workloads"].items())],
                           **_not_enough()})
    opts = []
    for oid in sorted(known):
        info = _per_run(oid, latest.get(oid))
        opts.append(info | {"inputs": [n for n in INPUTS if n in fed.get(oid, set())],
                            "capacity_ids": sorted(known[oid])})
    return {"contract": CONTRACT, "planner": list(PLANNER), "inputs": list(INPUTS), "unit": UNIT,
            "capacities": capacities, "optimizations": opts,
            "unplaced": sorted(unplaced, key=lambda x: (x["kind"], str(x["id"]))),
            "excluded": sorted(excluded, key=lambda x: (x["kind"], str(x["id"]))),
            **_not_enough(), "written": False}
