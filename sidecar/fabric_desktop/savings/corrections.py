"""Corrections, reversals and the reconciliation report (ESI-404).

The savings ledger is append-only: an event, once written, is never edited or deleted. When a
figure turns out to be wrong it is fixed by a new event that points at what it fixes through
``corrects`` (that event's ``ledger_event_id``). Following ``corrects`` back from any event leads
to one accrual (or regression): that accrual and everything correcting it is one **chain**, the
history of one layer of one run. What the ledger counts for that layer is the sum of the chain.

- **Correction.** When a run is attributed again (an after that was Unknown is now measured, a
  Capacity Metrics export was revised), ``correct`` compares the new ``attribution.attribute``
  result with what the ledger already holds for that run and writes, per layer, the difference:
  new minus what the chain counts so far, pointing at the chain's latest event. A correction of
  a correction points at the first correction, so a chain is a line, never a tree. A layer that is
  no longer in the new result gets a ``reversal`` of its full amount; a layer that is new is
  written as the new result's own accrual. Nothing is written for a layer that didn't change.
- **Reversal.** ``reverse`` undoes a run that should never have counted (for example an
  optimization that never actually deployed): one ``reversal`` per chain, each the opposite of
  what the chain counts, so each nets to zero. It needs a reason in plain words. A reverted
  optimization is not a reversal: runs before the revert really used less and keep their
  accruals (plan section 4.5).
- **Unknown stays Unknown.** A layer that becomes Unknown withdraws what it counted and is shown
  as Unknown, not as 0. A layer that was Unknown and is now known adds its whole figure. Every
  correction and reversal says what its layer is afterwards (``result_cu_hours``,
  ``result_classification``, ``layer_removed``), so the ledger can be read without replaying it
  and checked against itself.
- **Same input, same events.** Event ids come from ``record_id`` over the event type, run, layer,
  baseline version, pricing version and the event corrected, so running ``correct`` or ``reverse``
  twice, or on two PCs, gives the same ids, and running it again once its events are in the
  ledger gives nothing new.

``reconcile`` rebuilds per-run and overall totals from the ledger's events alone and compares
them with what they should be (normally the latest ``attribute`` result of each run). It reports
every match and every difference with the amounts, and every event that doesn't fit (a
correction of an event that isn't there, two corrections of one event, two accruals for one
layer). It never fixes anything: a difference is fixed by writing a correction. The ledger holds
layers, so it is compared layer for layer: a run whose regression outweighs its savings sums
below zero in the ledger even though its gross is shown as 0. A total is as weak as its weakest
layer, Unknown layers are counted beside it and never as 0, and scenario figures are refused.

Pure: no I/O.
"""

from __future__ import annotations

import math
from typing import Any

from .. import measurement as ms
from ..optimize.shared import record_id
from .attribution import ACCRUAL, REGRESSION, UNIT

CORRECTION, REVERSAL = "correction", "reversal"
EVENT_TYPES = (ACCRUAL, REGRESSION, CORRECTION, REVERSAL)
TOLERANCE = 1e-6  # figures are rounded to 6 places; closer than this is the same figure
_STRENGTH = {ms.MEASURED: 0, ms.ESTIMATED: 1, ms.UNKNOWN: 2}


def _weakest(*classes: str) -> str:
    return max(classes, key=_STRENGTH.__getitem__)


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"The {what} must be a number of CU-hours, not {value!r}.")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"The {what} must be a finite number of CU-hours, not {value}.")
    return value


def _check(e: dict[str, Any]) -> dict[str, Any]:
    """The event, or ValueError saying in plain words what is wrong with it."""
    eid = e.get("ledger_event_id")
    for key in ("ledger_event_id", "execution_id", "layer"):
        if not e.get(key):
            raise ValueError(f"A ledger event needs {key} (event {eid!r}).")
    kind = e.get("event_type")
    if kind not in EVENT_TYPES:
        raise ValueError(f"Event {eid} is a {kind!r} event; corrections and reconciliation read "
                         f"{', '.join(EVENT_TYPES)} events of CU avoided only.")
    cls = e.get("classification")
    if cls == ms.SCENARIO:
        raise ValueError(f"Event {eid} is a scenario figure; a scenario isn't evidence of CU avoided and never "
                         "goes in the ledger's totals.")
    if cls not in _STRENGTH:
        raise ValueError(f"Event {eid} has unknown classification {cls!r}.")
    value = e.get("cu_hours")
    if cls == ms.UNKNOWN:
        if value is not None:
            raise ValueError(f"Event {eid} is Unknown but has a figure; an Unknown figure is None, never a number.")
    else:
        value = _number(value, f"figure of event {eid}")
        if kind == ACCRUAL and value < 0:
            raise ValueError(f"Accrual {eid} is negative ({value}); CU avoided is zero or more, and a run that "
                             "used more is a regression.")
        if kind == REGRESSION and value > 0:
            raise ValueError(f"Regression {eid} is positive ({value}); a regression is CU used, not avoided.")
    if kind in (CORRECTION, REVERSAL):
        if not e.get("corrects"):
            raise ValueError(f"{kind.capitalize()} {eid} doesn't say which event it corrects.")
        rcls = e.get("result_classification")
        if rcls == ms.SCENARIO or rcls not in _STRENGTH:
            raise ValueError(f"{kind.capitalize()} {eid} doesn't say what its layer is afterwards.")
        result = e.get("result_cu_hours")
        if bool(rcls == ms.UNKNOWN or e.get("layer_removed")) != (result is None):
            raise ValueError(f"{kind.capitalize()} {eid} has a result figure that doesn't fit its classification.")
        if result is not None:
            _number(result, f"result of {kind} {eid}")
    return e


def _state(chain: dict[str, Any]) -> tuple[float | None, str, bool]:
    """What one chain counts now: its figure (None when Unknown or removed), classification and
    whether the layer has been removed."""
    head = chain["head"]
    if head is None:
        return chain["counted"], chain["classification"], False
    if head["event_type"] in (CORRECTION, REVERSAL):
        removed = bool(head.get("layer_removed"))
        cls = head["result_classification"]
        return (None if removed or cls == ms.UNKNOWN else chain["counted"]), cls, removed
    return head["cu_hours"], head["classification"], False


def _chains(events: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], dict[str, Any]], list[str]]:
    """The ledger's chains by (execution id, layer), and what doesn't fit, in plain sentences.

    The same event id twice (a second PC, a retry) is one event; the same id with different figures
    is reported. A correction of an event that isn't in the ledger starts a chain of its own and is
    reported. Two corrections of one event, or two accruals for one layer, are reported and both
    counted, since the ledger counts both."""
    problems: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for e in events:
        _check(e)
        seen = by_id.get(e["ledger_event_id"])
        if seen is not None and (seen["cu_hours"], seen["classification"], seen.get("corrects")) != (
                e["cu_hours"], e["classification"], e.get("corrects")):
            problems.append(f"Event {e['ledger_event_id']} is in the ledger twice with different figures; "
                            "only the first is counted.")
        by_id.setdefault(e["ledger_event_id"], e)
    children: dict[str, list[dict[str, Any]]] = {}
    for e in by_id.values():
        if e.get("corrects"):
            children.setdefault(str(e["corrects"]), []).append(e)

    def root_of(e: dict[str, Any]) -> dict[str, Any]:
        start, seen_ids = e, {e["ledger_event_id"]}
        while e.get("corrects") and str(e["corrects"]) in by_id:
            e = by_id[str(e["corrects"])]
            if e["ledger_event_id"] in seen_ids:
                return start  # corrections that correct each other in a circle: no accrual to start from
            seen_ids.add(e["ledger_event_id"])
        return e

    chains: dict[tuple[str, str], dict[str, Any]] = {}
    for e in sorted(by_id.values(), key=lambda x: str(x["ledger_event_id"])):
        root = root_of(e)
        if root is e and e.get("corrects"):
            problems.append(f"{e['event_type'].capitalize()} {e['ledger_event_id']} corrects event {e['corrects']}, "
                            "which isn't in the ledger (or leads back to itself) rather than to an accrual.")
        if e.get("execution_id") != root.get("execution_id") or e.get("layer") != root.get("layer"):
            problems.append(f"Event {e['ledger_event_id']} corrects an event of another run or layer.")
        key = (str(root["execution_id"]), str(root["layer"]))
        chain = chains.setdefault(key, {"roots": [], "events": [], "counted": 0.0})
        if root is e:
            chain["roots"].append(e)
        chain["events"].append(e)
        if e["cu_hours"] is not None:
            chain["counted"] += e["cu_hours"]
    for key, chain in chains.items():
        chain["counted"] = round(chain["counted"], 6)
        chain["workload_key"] = chain["roots"][0].get("workload_key")
        if len(chain["roots"]) > 1:
            problems.append(f"Run {key[0]} has {len(chain['roots'])} separate accruals for layer {key[1]} "
                            f"({', '.join(sorted(r['ledger_event_id'] for r in chain['roots']))}); one of them "
                            "is counted twice and needs a reversal.")
        # The head is the one event nothing corrects. A chain with one root and one head is a line.
        heads = [x for x in chain["events"] if x["ledger_event_id"] not in children]
        branched = [x for x in chain["events"] if len(children.get(x["ledger_event_id"], [])) > 1]
        for x in branched:
            problems.append(f"Event {x['ledger_event_id']} is corrected {len(children[x['ledger_event_id']])} "
                            f"times ({', '.join(sorted(c['ledger_event_id'] for c in children[x['ledger_event_id']]))}); "
                            "each later correction should correct the one before.")
        chain["head"] = heads[0] if len(heads) == 1 and len(chain["roots"]) == 1 and not branched else None
        chain["classification"] = _weakest(*(x["classification"] for x in chain["events"]))
        head = chain["head"]
        if head is not None:
            # What the chain's events add up to must be what its latest event says the layer is now
            # (0 once it is Unknown or removed: what it counted was withdrawn).
            said = head.get("result_cu_hours") if head["event_type"] in (CORRECTION, REVERSAL) else head["cu_hours"]
            expected = 0.0 if said is None else float(said)
            if abs(chain["counted"] - expected) > TOLERANCE:
                problems.append(f"Layer {key[1]} of run {key[0]} adds up to {chain['counted']:g} CU-hours, but its "
                                f"latest event says {expected:g}.")
    return chains, problems


def _run_chains(events: list[dict[str, Any]], execution_id: Any) -> dict[str, dict[str, Any]]:
    """One run's chains by layer, refusing anything that would make a correction ambiguous."""
    for e in events:
        if e.get("execution_id") != execution_id:
            raise ValueError(f"Event {e.get('ledger_event_id')} is for run {e.get('execution_id')}, not "
                             f"{execution_id}; correct one run at a time.")
    chains, problems = _chains(events)
    if problems:
        raise ValueError("The ledger for this run doesn't add up, so it can't be corrected safely: "
                         + " ".join(problems))
    return {layer: c for (_eid, layer), c in chains.items()}


def _event(chain: dict[str, Any], kind: str, delta: float | None, delta_cls: str, result: float | None,
           result_cls: str, *, removed: bool, base: dict[str, Any], like: dict[str, Any],
           pricing_version: Any, reason: str) -> dict[str, Any]:
    head = chain["head"]
    corrects = head["ledger_event_id"]
    layer = str(like["layer"])
    return {"execution_id": base["execution_id"], "workload_key": base["workload_key"],
            "baseline_version": base["baseline_version"], "layer": layer, "layer_order": like.get("layer_order"),
            "optimization_id": like.get("optimization_id"), "optimization_ids": like.get("optimization_ids"),
            "event_type": kind, "classification": delta_cls,
            "cu_hours": None if delta is None else round(delta, 6),
            "result_cu_hours": None if result is None else round(result, 6), "result_classification": result_cls,
            "layer_removed": removed, "corrects": corrects, "pricing_version": pricing_version, "reason": reason,
            "ledger_event_id": record_id("savings", kind, base["execution_id"], base["workload_key"], layer,
                                         base["baseline_version"], pricing_version, corrects)}


def _was(value: float | None, removed: bool) -> str:
    return "removed" if removed else "Unknown" if value is None else f"{value:g} CU-hours"


def correct(earlier: list[dict[str, Any]], latest: dict[str, Any], *, pricing_version: Any = None,
            reason: str = "") -> list[dict[str, Any]]:
    """The events that bring the ledger's figures for one run in line with ``latest``.

    ``earlier``: every ledger event already written for the run (its accruals and any corrections
    and reversals since), as they were written. ``latest``: a new ``attribution.attribute`` result
    for the same run. ``reason``: why it was attributed again, in plain words, kept on each event.

    Returns new events only, in layer order: a ``correction`` per changed layer, a ``reversal`` per
    layer that is gone, and the new result's own accrual for a layer the ledger hasn't seen. An
    empty list means the ledger already agrees."""
    for key in ("execution_id", "workload_key", "baseline_version", "layers"):
        if latest.get(key) is None:
            raise ValueError(f"The new attribution needs {key}.")
    eid = latest["execution_id"]
    for e in earlier:
        if e.get("workload_key") not in (None, latest["workload_key"]):
            raise ValueError(f"Event {e.get('ledger_event_id')} is for workload {e.get('workload_key')}, not "
                             f"{latest['workload_key']}.")
    chains = _run_chains(earlier, eid)
    new_layers: dict[str, dict[str, Any]] = {}
    for x in latest["layers"]:
        if x.get("execution_id") != eid:
            raise ValueError(f"Layer {x.get('layer')} of the new attribution is for another run.")
        if str(x["layer"]) in new_layers:
            raise ValueError(f"The new attribution has layer {x['layer']} twice.")
        new_layers[str(x["layer"])] = _check(x)
    why = reason.strip()
    out: list[dict[str, Any]] = []
    for name, x in sorted(new_layers.items(), key=lambda kv: (kv[1].get("layer_order") is None,
                                                             kv[1].get("layer_order") or 0, kv[0])):
        chain = chains.get(name)
        if chain is None:
            out.append(dict(x))  # a layer the ledger hasn't seen is an accrual of its own
            continue
        old, old_cls, removed = _state(chain)
        new, new_cls = x["cu_hours"], x["classification"]
        note = f"Re-attributed: was {_was(old, removed)}, now {_was(new, False)}" + (f". {why}" if why else "")
        if new is None:
            if old is None and not removed:
                continue  # Unknown before and after
            # Withdraw what was counted; the layer is Unknown now, not 0.
            delta, delta_cls = (None, ms.UNKNOWN) if old is None else (-chain["counted"], old_cls)
            out.append(_event(chain, CORRECTION, delta, delta_cls, None, ms.UNKNOWN, removed=False, base=latest,
                              like=x, pricing_version=pricing_version, reason=note))
            continue
        if old is None:
            delta, delta_cls = new - chain["counted"], new_cls  # nothing (or nothing left) was counted
        else:
            delta, delta_cls = new - chain["counted"], _weakest(old_cls, new_cls)
            if abs(delta) <= TOLERANCE and old_cls == new_cls:
                continue
        out.append(_event(chain, CORRECTION, delta, delta_cls, new, new_cls, removed=False, base=latest, like=x,
                          pricing_version=pricing_version, reason=note))
    for name, chain in sorted(chains.items()):
        if name in new_layers:
            continue
        old, old_cls, removed = _state(chain)
        if removed:
            continue
        like = chain["head"] if chain["head"]["event_type"] in (ACCRUAL, REGRESSION) else chain["roots"][0]
        note = f"No longer part of this run's attribution (was {_was(old, False)})" + (f". {why}" if why else "")
        delta, delta_cls = (None, ms.UNKNOWN) if old is None else (-chain["counted"], old_cls)
        out.append(_event(chain, REVERSAL, delta, delta_cls, None, old_cls, removed=True, base=latest, like=like,
                          pricing_version=pricing_version, reason=note))
    return out


def reverse(earlier: list[dict[str, Any]], *, reason: str, layers: list[str] | None = None,
            pricing_version: Any = None) -> list[dict[str, Any]]:
    """Reversal events that net every accrual of one run (or only ``layers``) to zero.

    For a run that should never have counted, such as one credited to an optimization that never
    actually deployed. ``earlier``: every ledger event already written for the run. ``reason``:
    why, in plain words; required. A layer already reversed gets nothing more, so reversing twice
    writes nothing the second time."""
    why = (reason or "").strip()
    if not why:
        raise ValueError("A reversal says why the run should never have counted.")
    if not earlier:
        return []
    eid = earlier[0].get("execution_id")
    chains = _run_chains(earlier, eid)
    if layers is not None:
        missing = sorted(set(map(str, layers)) - set(chains))
        if missing:
            raise ValueError(f"Run {eid} has no layer {', '.join(missing)} in the ledger to reverse.")
    out = []
    for name, chain in sorted(chains.items(), key=lambda kv: (kv[1]["roots"][0].get("layer_order") or 0, kv[0])):
        if layers is not None and name not in layers:
            continue
        old, old_cls, removed = _state(chain)
        if removed:
            continue
        root = chain["roots"][0]
        base = {"execution_id": eid, "workload_key": root.get("workload_key"),
                "baseline_version": chain["head"].get("baseline_version")}
        delta, delta_cls = (None, ms.UNKNOWN) if old is None else (-chain["counted"], old_cls)
        out.append(_event(chain, REVERSAL, delta, delta_cls, None, old_cls, removed=True, base=base, like=root,
                          pricing_version=pricing_version, reason=f"Reversed: {why}"))
    return out


def _total(parts: list[tuple[float | None, str]]) -> dict[str, Any]:
    return ms.total([{"classification": c, "value": v, "unit": UNIT} for v, c in parts], "fabric_cu_avoided", UNIT)


def reconcile(events: list[dict[str, Any]], expected: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild per-run and overall CU avoided from ``events`` (the ledger) and compare them with
    ``expected`` (the latest ``attribution.attribute`` result of each run).

    Each run is ``matched`` (every layer the same figure and classification), ``mismatched`` (with
    each difference and its amounts), ``not_in_ledger``, ``not_expected`` (in the ledger but not
    expected) or ``reversed`` (in the ledger, every layer reversed, and not expected). Nothing is
    fixed: ``reconciled`` is true only when every run matches and nothing in the ledger is out of
    place."""
    chains, problems = _chains(events)
    want: dict[str, dict[str, Any]] = {}
    for r in expected:
        key = str(r["execution_id"])
        layers = {}
        for x in r["layers"]:
            _check(x)
            layers[str(x["layer"])] = (x["cu_hours"], x["classification"])
        seen = want.get(key)
        if seen is not None and seen["layers"] != layers:
            raise ValueError(f"Run {key} has two different expected attributions; compare with one of them.")
        want[key] = {"workload_key": r.get("workload_key"), "layers": layers}
    ledger: dict[str, dict[str, tuple[float | None, str]]] = {}
    workloads: dict[str, Any] = {}
    for (eid, layer), chain in chains.items():
        value, cls, removed = _state(chain)
        if chain["head"] is None:
            # Ambiguous (reported in problems): count what the ledger holds, as weak as its weakest event.
            value, cls, removed = chain["counted"], chain["classification"], False
            if cls == ms.UNKNOWN:
                value = None
        workloads.setdefault(eid, chain["workload_key"])
        if not removed:
            ledger.setdefault(eid, {})[layer] = (value, cls)
        else:
            ledger.setdefault(eid, {})
    runs = []
    all_ledger: list[tuple[float | None, str]] = []
    all_expected: list[tuple[float | None, str]] = []
    for eid in sorted(set(ledger) | set(want)):
        have = ledger.get(eid)
        exp = want.get(eid)
        mine = list((have or {}).values())
        theirs = list(exp["layers"].values()) if exp else []
        all_ledger += mine
        all_expected += theirs
        lt, et = _total(mine), _total(theirs)
        mismatches = []
        if have is not None and exp is not None:
            for layer in sorted(set(have) | set(exp["layers"])):
                a, b = have.get(layer), exp["layers"].get(layer)
                if a is None or b is None:
                    kind = "not_in_ledger" if a is None else "not_expected"
                elif (a[0] is None) != (b[0] is None):
                    kind = "unknown_in_ledger" if a[0] is None else "known_in_ledger"
                elif a[0] is not None and b[0] is not None and abs(a[0] - b[0]) > TOLERANCE:
                    kind = "amount"
                elif a[1] != b[1]:
                    kind = "classification"
                else:
                    continue
                mismatches.append({
                    "layer": layer, "kind": kind,
                    "ledger_cu_hours": a[0] if a else None, "ledger_classification": a[1] if a else None,
                    "expected_cu_hours": b[0] if b else None, "expected_classification": b[1] if b else None,
                    "difference": round(a[0] - b[0], 6) if a and b and a[0] is not None and b[0] is not None
                    else None})
        if have is None and exp is not None and not exp["layers"]:
            status = "matched"  # a run with nothing to attribute has nothing in the ledger
        elif have is None:
            status = "not_in_ledger"
        elif exp is None:
            status = "reversed" if not have else "not_expected"
        else:
            status = "mismatched" if mismatches else "matched"
        runs.append({"execution_id": eid, "workload_key": (exp or {}).get("workload_key") or workloads.get(eid),
                     "status": status, "ledger": lt, "expected": et,
                     "difference": _difference(lt, et), "mismatches": mismatches})
    lt, et = _total(all_ledger), _total(all_expected)
    mismatched = sum(1 for r in runs if r["status"] not in ("matched", "reversed"))
    return {"runs": runs, "ledger": lt, "expected": et, "difference": _difference(lt, et),
            "matched": sum(1 for r in runs if r["status"] == "matched"), "mismatched": mismatched,
            "problems": problems, "events": len(events),
            "reconciled": mismatched == 0 and not problems,
            "fixed": False}  # reconciliation only reports; a difference is fixed by a correction


def _difference(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    if a["value"] is None or b["value"] is None:
        return None
    return round(a["value"] - b["value"], 6)
