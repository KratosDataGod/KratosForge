"""CSV text for the eight savings reports (ESI-507, engine only).

The reports the brief asks for, each built from the same ledger reads the Cost and savings page
uses: the ledger's events (``accruals.accrue`` events and the corrections and reversals of
``corrections.py``), read layer by layer as rule 4 of the reporting schema says (each layer's
current value is what its chain's head says, never the sum of its rows), plus, where a report
needs them, ``baselines.validate`` results, ``realized.realize`` results, a ``pricing.value_runs``
result and ``recurring.project`` results.

==========================  =====================================================================
``executive_summary``       CU avoided by classification, realized savings, capacity value at
                            scenario prices and the recurring monthly benefit (a projection)
``workspace``               CU avoided per workspace
``capacity``                CU avoided and realized savings per capacity
``developer_contribution``  CU avoided per installation (device); see below
``monthly_statement``       CU avoided, realized savings and capacity value per UTC month
``audit_history``           every ledger event, chain by chain, with what each layer is after it
``estimated_vs_verified``   CU avoided per source and classification, with the latest validation
``capacity_readiness``      the evidence a capacity has today; it recommends nothing
==========================  =====================================================================

Each returns a report: ``{"report", "columns", "rows", "notes"}``, rows in a stable order with
numbers left as numbers. ``to_csv`` turns one into CSV text.

**Classifications never mix.** Every figure column has its classification in the next column:
Measured, Estimated, Scenario or Unknown. Figures of different classifications are on separate
rows and never added into one cell. An Unknown figure is an empty cell labelled Unknown, never 0.
A price applied to CU avoided is "Capacity value at scenario prices", labelled Scenario, never
savings. Realized savings come only from ``realized.realize`` results; anything else is refused.

**Who.** No user identity is stored yet: ESI-102 waits on decision D3 (user identity) and the
leaderboard and names wait on D5. Developer contribution is therefore per installation (device)
and has no user columns. When one event id reaches the ledger from two devices, the copy written
first (``created_at``, then ``installation_id``) is the one read, so the device is the same
whatever order rows are read in.

**Capacity readiness** reports the evidence that exists: CU avoided on the capacity, validation
results of the optimizations that ran there, and realized savings. Headroom (measure B) needs
Capacity Metrics timepoints and is Unknown here. Right-sizing recommendations belong to the
capacity planner (OE-040, OE-041), not to this module.

**Net CU avoided** isn't reported: the ledger holds no overhead events yet, so the reports show
gross CU avoided per layer.

**CSV.** Written with the ``csv`` module, a header row first, then the rows, then (after an empty
row) one ``Note`` row per note. Text cells that start like a formula (``= + - @``, a tab or a
carriage return) get an apostrophe in front (``csv_cells``, WR-228). Numbers are written as plain
decimals to 6 places and are never changed, so ``-1`` (a regression) stays ``-1``. ``to_bytes``
encodes the text as UTF-8.

Pure: nothing here reads or writes a file, a lakehouse or Fabric.
"""

from __future__ import annotations

import csv
import io
import math
from typing import Any, Callable, Iterable

from .. import csv_cells
from .. import measurement as ms
from ..timeutil import iso as _iso
from . import realized as rz
from . import recurring as rc
from .attribution import LOCAL, UNATTRIBUTED
from .baselines import RESULTS, VERIFIED
from .corrections import CORRECTION, REVERSAL, _chains, _state

CU = "CU hours"
CU_AVOIDED = "CU hours avoided"
REALIZED = "Realized savings"
NOT_CLAIMED = "Billed drop not claimed"
CAPACITY_VALUE = "Capacity value at scenario prices"
PROJECTION = "Recurring monthly benefit (projection)"
HEADROOM = "Capacity headroom"
_ORDER = {c: i for i, c in enumerate(ms.CLASSIFICATIONS)}  # Measured, Estimated, Scenario, Unknown
_CLASS = {ms.LABELS[c]: i for c, i in _ORDER.items()}  # the same order, by label
_SOURCES = {LOCAL: "This PC", UNATTRIBUTED: "Unattributed"}
_RESULT_LABEL = {"verified": "Verified", "validating": "Validating", "regressed": "Regressed", "failed": "Failed"}

D3_D5 = ("Per-person reporting waits on decisions D3 (user identity, ESI-102) and D5 (leaderboard and "
         "names). Until then this report is per installation (device) and has no user columns.")
PLANNER = ("Right-sizing recommendations belong to the capacity planner (OE-040, OE-041), which needs peak, "
           "concurrency and throttling evidence. This report shows the evidence that exists and recommends "
           "nothing.")
NO_NET = ("Net CU avoided isn't reported: the ledger has no overhead events yet, so these are gross CU "
          "avoided per layer.")
APART = "Measured, Estimated, Scenario and Unknown figures are on separate rows and are never added together."
PRICE_NOTE = "A price applied to CU avoided. Not savings and not a change in the bill."
VERIFIED_NOTE = ("Verified means a measured before and after on the same inputs (baselines.validate). "
                 "Estimated CU avoided never verifies.")
NO_BILL = ("No confirmed billing evidence. Realized savings need a Cost Management export and a person's "
           "confirmation of the cause.")
NO_HEADROOM = ("Headroom (measure B) needs Capacity Metrics timepoints and the planner's demand model "
               "(OE-040); it isn't worked out here.")


# ---- shared reads ----------------------------------------------------------------------------------


def _key(v: Any) -> tuple[bool, str]:
    """Sort key: missing values last, the rest as text."""
    return (v is None or v == "", "" if v is None else str(v))


def _label(cls: str) -> str:
    return ms.LABELS[cls]


def _month(at: Any) -> str | None:
    t = ms.parse_time(at)
    return None if t is None else rc._period(t, "month")


def _ordered(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events in a fixed order, so the copy of an id read first (and its device) never depends on
    the order rows came in: the one written first, then by installation."""
    return sorted(events, key=lambda e: (str(e.get("ledger_event_id")), _key(_iso(e.get("created_at"))),
                                         _key(e.get("installation_id"))))


def layers(events: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Each layer of each run in the ledger with its current value (rule 4: the chain's head), and the
    ledger's problems in plain sentences. A removed layer (reversed) is left out. A chain that doesn't
    fit (reported in the problems) is read as ``corrections.reconcile`` reads it: what it holds, as
    weak as its weakest event."""
    chains, problems = _chains(_ordered(events))
    out = []
    for (eid, name), chain in sorted(chains.items()):
        root = min(chain["roots"], key=lambda r: str(r["ledger_event_id"]))
        value, cls, removed = _state(chain)
        if chain["head"] is None:
            value, cls, removed = chain["counted"], chain["classification"], False
            if cls == ms.UNKNOWN:
                value = None
        if removed:
            continue
        out.append({
            "execution_id": eid, "layer": name, "value": None if value is None else round(value, 6),
            "classification": cls, "at": _iso(root.get("period_start")),
            "workload_key": root.get("workload_key"), "baseline_version": root.get("baseline_version"),
            "optimization_id": root.get("optimization_id") or (None if name in _SOURCES else name),
            "source": _SOURCES.get(name, "Optimization"), "workspace_id": root.get("workspace_id"),
            "capacity_id": root.get("capacity_id"), "installation_id": root.get("installation_id"),
            "corrected": len(chain["events"]) > 1})
    return out, problems


def _groups(rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], tuple[Any, ...]]
            ) -> list[tuple[tuple[Any, ...], str, float | None, int, int]]:
    """(group, classification, CU hours or None, runs, layers), one per group and classification,
    sorted. Figures of different classifications are never added."""
    acc: dict[tuple[tuple[Any, ...], str], dict[str, Any]] = {}
    for r in rows:
        g = acc.setdefault((key(r), r["classification"]), {"sum": 0.0, "runs": set(), "layers": 0})
        if r["value"] is not None:
            g["sum"] += r["value"]
        g["runs"].add(r["execution_id"])
        g["layers"] += 1
    out = []
    for (k, cls), g in acc.items():
        out.append((k, cls, None if cls == ms.UNKNOWN else round(g["sum"], 6), len(g["runs"]), g["layers"]))
    return sorted(out, key=lambda x: (tuple(_key(v) for v in x[0]), _ORDER[x[1]]))


def _problems(problems: list[str]) -> list[str]:
    if not problems:
        return []
    return [f"The ledger has {len(problems)} problem(s), read as reconciliation reads them; run reconciliation "
            "to fix them with corrections: " + " ".join(problems)]


def _report(name: str, columns: list[str], rows: list[list[Any]], notes: list[str]) -> dict[str, Any]:
    return {"report": name, "columns": columns, "rows": rows, "notes": notes}


# ---- checks on the other modules' results -------------------------------------------------------


def _realized(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """``realized.realize`` results only: a Measured figure when realized, Unknown with the reason when not."""
    out = []
    for i, r in enumerate(results):
        m = r.get("realized_savings") if isinstance(r, dict) else None
        if not isinstance(m, dict) or "realized" not in r or m.get("metric") != rz.METRIC:
            raise ValueError(f"Realized result {i + 1} isn't a realized.realize result; realized savings come only "
                             "from a bill and a person's confirmation.")
        ok = (m["classification"] == ms.MEASURED and m["value"] is not None) if r["realized"] else (
            m["classification"] == ms.UNKNOWN and m["value"] is None)
        if not ok:
            raise ValueError(f"Realized result {i + 1} says realized is {r['realized']} with a {m['classification']} "
                             "figure; realized savings are Measured, or Unknown with no figure.")
        out.append(r)
    return out


def _realized_capacity(r: dict[str, Any]) -> Any:
    m = r["realized_savings"]
    return r.get("billing_capacity_id") or r.get("capacity_id") or m.get("capacity_id")


def _realized_period(r: dict[str, Any]) -> tuple[Any, Any]:
    after = r.get("after") or {}
    period = r["realized_savings"].get("period") or {}
    return _iso(after.get("start") or period.get("start")), _iso(after.get("end") or period.get("end"))


def _value_runs(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The runs of a ``pricing.value_runs`` result, each priced as a Scenario or Unknown."""
    if result is None:
        return []
    if not isinstance(result, dict) or "runs" not in result or "scenario" not in result:
        raise ValueError("Capacity value is a pricing.value_runs result.")
    for x in result["runs"]:
        m = x["value"]
        if m["classification"] not in (ms.SCENARIO, ms.UNKNOWN) or (m["value"] is None) != (
                m["classification"] == ms.UNKNOWN):
            raise ValueError(f"Run {x.get('execution_id')} is priced as {m['classification']}; a price applied to "
                             "CU is a Scenario, never savings.")
    return list(result["runs"])


def _validations(validations: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The latest validation per optimization (by ``at``, then id), from ``baselines.validate``
    results or ``fdopt_validation`` rows."""
    latest: dict[str, dict[str, Any]] = {}
    for v in validations:
        row = v.get("row", v)
        oid, result = row.get("optimization_id"), row.get("result")
        if not oid or result not in RESULTS:
            raise ValueError(f"Validation {row.get('validation_id')!r} needs an optimization_id and a result of "
                             f"{', '.join(RESULTS)}.")
        key = (_iso(row.get("at")) or "", str(row.get("validation_id") or ""))
        seen = latest.get(str(oid))
        if seen is None or key > (_iso(seen.get("at")) or "", str(seen.get("validation_id") or "")):
            latest[str(oid)] = row
    return latest


# ---- the eight reports -----------------------------------------------------------------------------

SUMMARY_COLUMNS = ["Measure", "Value", "Classification", "Unit", "Count", "Note"]


def _money_rows(measure: str, items: list[tuple[Any, str, float | None]],
                note: Callable[[str], str]) -> list[list[Any]]:
    """Rows of (currency, classification, value) items: summed per currency within one
    classification, Unknown counted on a row of its own."""
    acc: dict[tuple[str, Any], list[float | None]] = {}
    for cur, cls, v in items:
        acc.setdefault((cls, None if cls == ms.UNKNOWN else cur), []).append(v)
    rows = []
    for (cls, cur), vals in sorted(acc.items(), key=lambda kv: (_ORDER[kv[0][0]], _key(kv[0][1]))):
        value = None if cls == ms.UNKNOWN else round(sum(v for v in vals if v is not None), 6)
        rows.append([measure, value, _label(cls), cur or "", len(vals), note(cls)])
    return rows


def executive_summary(events: Iterable[dict[str, Any]], *, realized: Iterable[dict[str, Any]] = (),
                      capacity_value: dict[str, Any] | None = None,
                      projections: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """The headline figures, one row per measure and classification. ``Count`` is runs for CU
    avoided, results for realized savings, runs for capacity value and workloads for the projection."""
    rows_in, problems = layers(events)
    rows: list[list[Any]] = []
    for _k, cls, value, runs, _n in _groups(rows_in, lambda r: ()):
        rows.append([CU_AVOIDED, value, _label(cls), CU, runs,
                     "Layers whose CU avoided isn't known; never counted as 0." if cls == ms.UNKNOWN else ""])
    if not rows_in:
        rows.append([CU_AVOIDED, 0.0, _label(ms.MEASURED), CU, 0, "No runs in the ledger."])

    found = _realized(realized)
    if found:
        rows += _money_rows(REALIZED, [(r["realized_savings"].get("currency"), r["realized_savings"]["classification"],
                                        r["realized_savings"]["value"]) for r in found],
                            lambda c: "Not realized; the capacity report gives each reason." if c == ms.UNKNOWN
                            else "From a bill and a person's confirmation.")
    else:
        rows.append([REALIZED, None, _label(ms.UNKNOWN), "", 0, NO_BILL])

    priced = _value_runs(capacity_value)
    if capacity_value is not None:
        scenario = capacity_value["scenario"]
        rows += _money_rows(CAPACITY_VALUE, [(x["value"].get("currency"), x["value"]["classification"],
                                              x["value"]["value"]) for x in priced],
                            lambda c: ("No price or no CU avoided for these runs." if c == ms.UNKNOWN
                                       else f"Scenario {scenario}. {PRICE_NOTE}"))
        if not priced:
            rows.append([CAPACITY_VALUE, 0.0, "", _label(ms.SCENARIO), 0, f"Scenario {scenario}. No runs priced."])

    projections = list(projections)
    if projections:
        known, unknown = [], 0
        for p in projections:
            m = p.get("projection") or {}
            if m.get("metric") != rc.METRIC or m.get("classification") not in (ms.ESTIMATED, ms.UNKNOWN):
                raise ValueError(f"Projection for {p.get('workload_key')!r} isn't a recurring.project result; a "
                                 "projection is always Estimated.")
            if m["value"] is None:
                unknown += 1
            else:
                known.append(m["value"])
        if known:
            rows.append([PROJECTION, round(sum(known), 6), _label(ms.ESTIMATED), CU, len(known),
                         "Runs that haven't happened, read now and never stored."])
        if unknown:
            rows.append([PROJECTION, None, _label(ms.UNKNOWN), CU, unknown,
                         "Workloads with too few runs, retired or rebaselined; nothing projected."])
    return _report("executive_summary", SUMMARY_COLUMNS, rows, [APART, NO_NET, *_problems(problems)])


def workspace(events: Iterable[dict[str, Any]], *, names: dict[str, str] | None = None) -> dict[str, Any]:
    """CU avoided per workspace and classification. ``names``: optional display names by id; the id is the key."""
    rows_in, problems = layers(events)
    names = names or {}
    rows = [[k[0] or "", names.get(k[0] or "", ""), value, _label(cls), runs, n]
            for k, cls, value, runs, n in _groups(rows_in, lambda r: (r["workspace_id"],))]
    return _report("workspace", ["Workspace", "Name", CU_AVOIDED, "Classification", "Runs", "Layers"], rows,
                   [APART, *_problems(problems)])


CAPACITY_COLUMNS = ["Capacity", "Measure", "Value", "Classification", "Unit", "Count", "Period start",
                    "Period end", "Note"]
_MEASURES = {CU_AVOIDED: 0, REALIZED: 1, NOT_CLAIMED: 2, CAPACITY_VALUE: 3, PROJECTION: 4, HEADROOM: 5}


def _span(rows_in: list[dict[str, Any]]) -> dict[tuple[Any, str], tuple[Any, Any]]:
    out: dict[tuple[Any, str], list[str]] = {}
    for r in rows_in:
        if r["at"]:
            out.setdefault((r["capacity_id"], r["classification"]), []).append(r["at"])
    return {k: (min(v), max(v)) for k, v in out.items()}


def _realized_rows(found: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for r in found:
        m = r["realized_savings"]
        start, end = _realized_period(r)
        cap = _realized_capacity(r) or ""
        note = m.get("method") if r["realized"] else (r.get("reason") or m.get("reason") or "")
        rows.append([cap, REALIZED, m["value"], _label(m["classification"]), m.get("currency") or "", 1, start, end,
                     note])
        rest = r.get("not_claimed")
        if r["realized"] and isinstance(rest, dict) and rest.get("value") is not None:
            rows.append([cap, NOT_CLAIMED, rest["value"], _label(rest["classification"]), rest.get("currency") or "",
                         1, start, end, "Part of the bill's drop the confirmed change doesn't explain; not claimed."])
    return rows


def _sort_capacity(rows: list[list[Any]]) -> list[list[Any]]:
    return sorted(rows, key=lambda r: (_key(r[0]), _MEASURES[r[1]], _key(r[6]),
                                       _CLASS[r[3]], _key(r[4]), _key(r[8])))


def capacity(events: Iterable[dict[str, Any]], *, realized: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """CU avoided per capacity and classification (the period is the first and last run), and each
    realized result at its capacity and after period, with what the bill's drop left unclaimed."""
    rows_in, problems = layers(events)
    spans = _span(rows_in)
    rows = []
    for k, cls, value, runs, _n in _groups(rows_in, lambda r: (r["capacity_id"],)):
        start, end = spans.get((k[0], cls), (None, None))
        rows.append([k[0] or "", CU_AVOIDED, value, _label(cls), CU, runs, start, end, ""])
    rows += _realized_rows(_realized(realized))
    return _report("capacity", CAPACITY_COLUMNS, _sort_capacity(rows), [APART, *_problems(problems)])


def developer_contribution(events: Iterable[dict[str, Any]], *,
                           device_labels: dict[str, str] | None = None) -> dict[str, Any]:
    """CU avoided per installation (device) and classification. Per-person reporting waits on D3
    and D5, so there are no user columns. ``device_labels``: optional aliases by installation id."""
    rows_in, problems = layers(events)
    labels = device_labels or {}
    rows = [[k[0] or "", labels.get(k[0] or "", ""), value, _label(cls), runs, n]
            for k, cls, value, runs, n in _groups(rows_in, lambda r: (r["installation_id"],))]
    return _report("developer_contribution", ["Device", "Device label", CU_AVOIDED, "Classification", "Runs",
                                              "Layers"], rows, [D3_D5, APART, *_problems(problems)])


STATEMENT_COLUMNS = ["Month", "Measure", "Value", "Classification", "Unit", "Count", "Note"]


def monthly_statement(events: Iterable[dict[str, Any]], *, realized: Iterable[dict[str, Any]] = (),
                      capacity_value: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per UTC month: CU avoided by the month each run ran in, realized savings by the month their
    after period starts, and capacity value at scenario prices by the month each priced run ran in."""
    rows_in, problems = layers(events)
    rows = [[k[0] or "", CU_AVOIDED, value, _label(cls), CU, runs, ""]
            for k, cls, value, runs, _n in _groups(rows_in, lambda r: (_month(r["at"]),))]
    by_month: dict[Any, list[tuple[Any, str, float | None]]] = {}
    for r in _realized(realized):
        m = r["realized_savings"]
        by_month.setdefault(_month(_realized_period(r)[0]), []).append((m.get("currency"), m["classification"],
                                                                         m["value"]))
    for month, items in by_month.items():
        rows += [[month or "", *row] for row in _money_rows(
            REALIZED, items,
            lambda c: "Not realized; the capacity report gives each reason." if c == ms.UNKNOWN else "")]
    priced: dict[Any, list[tuple[Any, str, float | None]]] = {}
    for x in _value_runs(capacity_value):
        priced.setdefault(_month(x["at"]), []).append((x["value"].get("currency"), x["value"]["classification"],
                                                       x["value"]["value"]))
    scenario = capacity_value["scenario"] if capacity_value else ""
    priced_note = f"Scenario {scenario}. {PRICE_NOTE}"
    for month, items in priced.items():
        rows += [[month or "", *row] for row in _money_rows(
            CAPACITY_VALUE, items, lambda c: "No price or no CU avoided." if c == ms.UNKNOWN else priced_note)]
    rows.sort(key=lambda r: (_key(r[0]), _MEASURES[r[1]], _CLASS[r[3]], _key(r[4])))
    return _report("monthly_statement", STATEMENT_COLUMNS, rows, [APART, NO_NET, *_problems(problems)])


AUDIT_COLUMNS = ["Event", "Type", "Corrects", "Run", "Run time", "Workload", "Layer", "Optimization", "Device",
                 "Workspace", "Capacity", "Baseline version", "Pricing version", "Change CU hours", "Classification",
                 "Layer after CU hours", "Layer after classification", "Layer removed", "Current", "Reason"]


def audit_history(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Every ledger event once, chain by chain (by run time, run and layer), each chain from its
    accrual through its corrections. ``Change`` is the event's own figure; ``Layer after`` is what the
    layer is once the event is applied. ``Current`` marks the chain's head, the value the reports use."""
    chains, problems = _chains(_ordered(events))
    rows = []
    for (eid, name), chain in sorted(chains.items(), key=lambda kv: (
            _key(_iso(min(kv[1]["roots"], key=lambda r: str(r["ledger_event_id"])).get("period_start"))), kv[0])):
        root = min(chain["roots"], key=lambda r: str(r["ledger_event_id"]))
        ids = {e["ledger_event_id"]: e for e in chain["events"]}

        def depth(e: dict[str, Any], ids: dict[str, dict[str, Any]] = ids) -> int:
            n, seen = 0, {e["ledger_event_id"]}
            while e.get("corrects") and str(e["corrects"]) in ids and str(e["corrects"]) not in seen:
                e = ids[str(e["corrects"])]
                seen.add(e["ledger_event_id"])
                n += 1
            return n

        for e in sorted(chain["events"], key=lambda x: (depth(x), str(x["ledger_event_id"]))):
            if e["event_type"] in (CORRECTION, REVERSAL):
                removed = bool(e.get("layer_removed"))
                after, after_cls = e.get("result_cu_hours"), e.get("result_classification")
            else:
                removed, after, after_cls = False, e["cu_hours"], e["classification"]
            rows.append([e["ledger_event_id"], e["event_type"], e.get("corrects") or "", eid,
                         _iso(root.get("period_start")), root.get("workload_key"), name,
                         e.get("optimization_id") or root.get("optimization_id") or "",
                         e.get("installation_id") or root.get("installation_id") or "",
                         root.get("workspace_id"), root.get("capacity_id"), e.get("baseline_version"),
                         e.get("pricing_version"), e["cu_hours"], _label(e["classification"]),
                         None if removed else after, "" if removed else _label(after_cls), removed,
                         chain["head"] is e, e.get("reason") or ""])
    return _report("audit_history", AUDIT_COLUMNS, rows,
                   ["A removed layer has no figure after its reversal; it no longer counts.",
                    *_problems(problems)])


def estimated_vs_verified(events: Iterable[dict[str, Any]], *,
                          validations: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """CU avoided per source (each optimization, this PC, unattributed) and classification, with
    each optimization's latest validation. An optimization with a validation and nothing in the
    ledger is listed with its CU avoided Unknown."""
    rows_in, problems = layers(events)
    latest = _validations(validations)
    rows = []
    seen: set[str] = set()
    for (source, oid), cls, value, runs, n in _groups(rows_in, lambda r: (r["source"], r["optimization_id"])):
        v = latest.get(str(oid)) if oid else None
        if oid:
            seen.add(str(oid))
        status = (_RESULT_LABEL[v["result"]] if v else "Not validated") if oid else ""
        rows.append([source, oid or "", value, _label(cls), runs, n, status, _iso(v.get("at")) if v else None,
                     v.get("reason") if v else ""])
    for oid in sorted(set(latest) - seen):
        v = latest[oid]
        rows.append(["Optimization", oid, None, _label(ms.UNKNOWN), 0, 0, _RESULT_LABEL[v["result"]],
                     _iso(v.get("at")), v.get("reason") or ""])
    rows.sort(key=lambda r: (r[0] != "Optimization", _key(r[1]), _key(r[0]),
                             _CLASS[r[3]]))
    return _report("estimated_vs_verified", ["Source", "Optimization", CU_AVOIDED, "Classification", "Runs",
                                             "Layers", "Validation", "Validated at", "Validation reason"], rows,
                   [APART, VERIFIED_NOTE, *_problems(problems)])


READINESS_COLUMNS = ["Capacity", "Evidence", "Value", "Classification", "Unit", "Count", "Note"]
_EVIDENCE = {CU_AVOIDED: 0, **{f"Optimizations {_RESULT_LABEL[r].lower()}": i + 1 for i, r in enumerate(RESULTS)},
             "Optimizations not validated": 5, REALIZED: 6, HEADROOM: 7}


def capacity_readiness(events: Iterable[dict[str, Any]], *, validations: Iterable[dict[str, Any]] = (),
                       realized: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """The evidence each capacity has: CU avoided on it, the latest validation result of each
    optimization that ran on it (counted per result), its latest realized result and headroom
    (Unknown: it needs the planner). It recommends nothing; right-sizing belongs to OE-040 and OE-041."""
    rows_in, problems = layers(events)
    latest = _validations(validations)
    found = _realized(realized)
    rows: list[list[Any]] = []
    caps = {r["capacity_id"] or "" for r in rows_in} | {_realized_capacity(r) or "" for r in found}
    for k, cls, value, runs, _n in _groups(rows_in, lambda r: (r["capacity_id"],)):
        rows.append([k[0] or "", CU_AVOIDED, value, _label(cls), CU, runs, ""])
    opts: dict[str, set[str]] = {}
    for r in rows_in:
        if r["optimization_id"]:
            opts.setdefault(r["capacity_id"] or "", set()).add(str(r["optimization_id"]))
    for cap, ids in opts.items():
        counts: dict[str, int] = {}
        for oid in ids:
            v = latest.get(oid)
            what = f"Optimizations {_RESULT_LABEL[v['result']].lower()}" if v else "Optimizations not validated"
            counts[what] = counts.get(what, 0) + 1
        for what, n in counts.items():
            note = ("Measured before and after on the same inputs." if what == f"Optimizations {VERIFIED}" else "")
            rows.append([cap, what, n, _label(ms.MEASURED), "optimizations", n, note])
    newest: dict[str, dict[str, Any]] = {}
    for r in found:
        cap = _realized_capacity(r) or ""
        if cap not in newest or _key(_realized_period(r)[1]) > _key(_realized_period(newest[cap])[1]):
            newest[cap] = r
    for cap in sorted(caps, key=_key):
        r = newest.get(cap)
        if r is None:
            rows.append([cap, REALIZED, None, _label(ms.UNKNOWN), "", 0, NO_BILL])
        else:
            m = r["realized_savings"]
            start, end = _realized_period(r)
            what = (f"Latest after period {start} to {end}. " if start else "") + (
                (m.get("method") or "") if r["realized"] else (r.get("reason") or m.get("reason") or ""))
            rows.append([cap, REALIZED, m["value"], _label(m["classification"]), m.get("currency") or "", 1,
                         what.strip()])
        rows.append([cap, HEADROOM, None, _label(ms.UNKNOWN), "", 0, NO_HEADROOM])
    rows.sort(key=lambda r: (_key(r[0]), _EVIDENCE[r[1]], _CLASS[r[3]]))
    return _report("capacity_readiness", READINESS_COLUMNS, rows, [PLANNER, APART, *_problems(problems)])


REPORTS: dict[str, Callable[..., dict[str, Any]]] = {
    "executive_summary": executive_summary, "workspace": workspace, "capacity": capacity,
    "developer_contribution": developer_contribution, "monthly_statement": monthly_statement,
    "audit_history": audit_history, "estimated_vs_verified": estimated_vs_verified,
    "capacity_readiness": capacity_readiness,
}


# ---- CSV -------------------------------------------------------------------------------------------


def _number(v: float) -> str:
    if not math.isfinite(v):
        raise ValueError(f"A report figure must be finite, not {v!r}.")
    text = f"{round(v, 6):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return _number(v)
    return str(v)


def _lines(report: dict[str, Any]) -> list[list[Any]]:
    width = len(report["columns"])
    lines: list[list[Any]] = [list(report["columns"])]
    for row in report["rows"]:
        if len(row) != width:
            raise ValueError(f"A {report['report']} row has {len(row)} cells, not {width}.")
        lines.append(list(row))
    if report["notes"]:
        lines += [[], *(["Note", n] for n in report["notes"])]
    return lines


def to_csv(report: dict[str, Any]) -> str:
    """The report as CSV text: the header row, the rows, then an empty row and one ``Note`` row per
    note, with RFC 4180 line ends. Text cells that start like a formula get an apostrophe in front
    (``csv_cells``, WR-228); numbers are formatted after that, so they are never changed."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    for line in _lines(report):
        protected, _n = csv_cells.protect_row(line)
        writer.writerow([_cell(v) for v in protected])
    return buf.getvalue()


def table(report: dict[str, Any]) -> list[list[str]]:
    """The CSV's rows of text (header first), exactly as ``to_csv`` writes them."""
    return list(csv.reader(io.StringIO(to_csv(report), newline="")))


def to_bytes(report: dict[str, Any]) -> bytes:
    """The CSV text as UTF-8."""
    return to_csv(report).encode("utf-8")


def export(name: str, *args: Any, **kwargs: Any) -> str:
    """CSV text of the report ``name`` (one of :data:`REPORTS`), built from the inputs given."""
    if name not in REPORTS:
        raise ValueError(f"There is no {name!r} report; the reports are {', '.join(REPORTS)}.")
    return to_csv(REPORTS[name](*args, **kwargs))
