"""Capacity and FinOps controls from imported Capacity Metrics exports (FEA-202).

Live capacity utilization has no public API, so these controls decide only from two things the app
already has: the capacity's state and SKU (``capacity.state``, ``GET /capacities``) and the Capacity
Metrics app exports the person imported on this PC (``capacity.usage``, built here by :func:`usage`
from the local usage store; nothing is read from Fabric). Imported rows are *measured*.

What an export holds decides what can be checked (usage/metrics_app.py):

- the Timepoint page's operations table: per operation its workspace, item, operation name, status,
  start, end and Total CU (s);
- the Compute page's items matrix: per item its CU (s) over a period the person entered.

Neither says whether the capacity was throttled, or whether an operation was interactive or
background, so no control here claims either. A control whose field isn't in the imported export
is *not evaluated*, never passed. Without an import that has the workspace's use, every control here
is not evaluated and asks for one.

Which capacity an export is of: the Capacity Metrics app shows one capacity at a time, so an export
that holds the assessed workspace's use is taken to be of that workspace's capacity (the record says
so). An export taken before the workspace moved capacity would be attributed wrongly; the record
names its files and period so a person can tell.

Capacity freed is not money saved: a benefit here is capacity, estimated, and money is saved only
when a capacity is resized, paused or retired (docs/finops/plan.md).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter
from typing import Any

from .. import measurement as ms
from . import controls as ct
from . import evidence as ev

KIND = "capacity.usage"
SOURCE = "Capacity Metrics app export imported on this PC (usage imports); no Fabric call"
NEEDS = "an imported Capacity Metrics export"
NEEDS_OPS = "an imported Capacity Metrics export of the Timepoint page's operations table"
NEEDS_ITEMS = "an imported Capacity Metrics export of the Compute page's items matrix"
ATTRIBUTION = ("The Capacity Metrics app shows one capacity at a time, so an export that holds this workspace's use "
               "is taken to be of its capacity. Exports can be sampled, so a file may not hold every operation.")
FAILED_STATUSES = ("failure", "failed")
MAX_FAILED_SHARE = 0.10
MAX_REFRESHES_PER_DAY = 24
MIN_UTILIZATION = 0.10
MIN_ITEMS_DAYS = 7
_TOP = 20
# CU seconds one imported row can plausibly hold: an F2048 running flat out for a year is about 6.5e10.
# A row above it (or NaN, infinity, negative) is junk and left out, as a row without a number is.
MAX_ROW_CU = 1e12
_F_SKU = re.compile(r"F(\d+)", re.IGNORECASE)

_METRICS_DOC = {"title": "What is the Microsoft Fabric Capacity Metrics app?",
                "url": "https://learn.microsoft.com/fabric/enterprise/metrics-app"}
_COMPUTE_DOC = {"title": "Understand the Capacity Metrics app compute page",
                "url": "https://learn.microsoft.com/fabric/enterprise/metrics-app-compute-page"}
_TIMEPOINT_DOC = {"title": "Understand the Capacity Metrics app timepoint page",
                  "url": "https://learn.microsoft.com/fabric/enterprise/metrics-app-timepoint-page"}
_SCALE_DOC = {"title": "Scale your Fabric capacity", "url": "https://learn.microsoft.com/fabric/enterprise/scale-capacity"}
_PAUSE_DOC = {"title": "Pause and resume your capacity", "url": "https://learn.microsoft.com/fabric/enterprise/pause-resume"}
_REFRESH_DOC = {"title": "Data refresh in Power BI", "url": "https://learn.microsoft.com/power-bi/connect-data/refresh-data"}


# ---- evidence: the local usage store -> capacity.usage

def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


def _when(text: Any) -> dt.datetime | None:
    """The time, in UTC, or None when it isn't one (or can't be shown in UTC, like year 1 at +14:00)."""
    if not text:
        return None
    try:
        t = ms.parse_time(text)
        return t.astimezone(dt.timezone.utc) if t else None
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def _iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds") if t else None


def _cu(row: dict[str, Any]) -> float | None:
    cu = row.get("cu")
    v = cu.get("value") if isinstance(cu, dict) else None
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    v = float(v) if abs(v) < 1e300 else math.inf
    return v if math.isfinite(v) and 0 <= v <= MAX_ROW_CU else None


def _operations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = 0.0
    by_status: dict[str, float] = {}
    status_rows = operation_rows = 0
    refreshes: Counter[tuple[str, str, str]] = Counter()
    starts: list[dt.datetime] = []
    ends: list[dt.datetime] = []
    for r in rows:
        cu, start = _cu(r), _when(r.get("start"))
        if cu is None or start is None:
            continue
        total += cu
        starts.append(start)
        ends.append(_when(r.get("end")) or start)
        if r.get("status"):
            status_rows += 1
            key = _norm(ev.text(r["status"]))
            by_status[key] = by_status.get(key, 0.0) + cu
        if r.get("operation"):
            operation_rows += 1
            if "refresh" in _norm(r["operation"]):
                day = start.astimezone(dt.timezone.utc).date().isoformat()
                refreshes[(ev.text(r.get("workspace")), ev.text(r.get("item")), day)] += 1
    top = sorted(refreshes.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP]
    return {"rows": len(starts), "cu_seconds": round(total, 3), "status_rows": status_rows,
            "cu_by_status": {k: round(v, 3) for k, v in sorted(by_status.items())}, "operation_rows": operation_rows,
            "window": {"start": _iso(min(starts)) if starts else None, "end": _iso(max(ends)) if ends else None},
            "refreshes_per_day": [{"workspace": w, "item": i, "day": d, "count": n} for (w, i, d), n in top],
            # counted before the cut to the top _TOP, so a fail gives the true number
            "refresh_days_over": sum(1 for n in refreshes.values() if n > MAX_REFRESHES_PER_DAY)}


def _rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    rows = doc.get("rows")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _items(doc: dict[str, Any]) -> dict[str, Any] | None:
    period = doc.get("period")
    period = period if isinstance(period, dict) else {}
    start, end = _when(period.get("start")), _when(period.get("end"))
    cus = [c for r in _rows(doc) if (c := _cu(r)) is not None]
    if not start or not end or end <= start or not cus:
        return None
    return {"import_id": ev.text(doc.get("id")), "period": {"start": _iso(start), "end": _iso(end)},
            "seconds": (end - start).total_seconds(), "rows": len(cus), "cu_seconds": round(sum(cus), 3)}


def usage(capacity_id: str, capacity_name: str | None, workspace_name: str | None,
          imports: list[dict[str, Any]] | None, *, collected_at: str) -> list[dict[str, Any]]:
    """One ``capacity.usage`` record (measured) from the Capacity Metrics imports that hold
    ``workspace_name``'s use, or none: no import, no workspace name, or no import has it."""
    if not capacity_id or not isinstance(workspace_name, str) or not workspace_name or not imports:
        return []
    if not isinstance(imports, list):
        raise ValueError("the imports aren't a list")
    want = _norm(workspace_name)
    mine = [d for d in imports if isinstance(d, dict) and d.get("provider") == "metrics_app"
            and any(_norm(r.get("workspace")) == want for r in _rows(d))]
    if not mine:
        return []
    mine.sort(key=lambda d: ev.text(d.get("id")))
    ops_rows = [r for d in mine if d.get("layout") == "operations" for r in _rows(d)]
    items = [x for d in mine if d.get("layout") == "items" and (x := _items(d))]
    payload = {"matched_by": {"workspace": ev.text(workspace_name)}, "attribution": ATTRIBUTION,
               "imports": [{"id": ev.text(d.get("id")), "file_name": ev.text(d.get("file_name")),
                            "layout": ev.text(d.get("layout")), "imported_at": ev.text(d.get("imported_at")) or None,
                            "rows": len(_rows(d))} for d in mine],
               "operations": _operations(ops_rows) if ops_rows else None, "items": items}
    subj = ev.subject("capacity", capacity_id.lower(), capacity_name)
    return [ev.record(KIND, subj, source=SOURCE, confidence="measured", collected_at=collected_at, payload=payload)]


# ---- controls

def _usage(ctx: ct.Context) -> dict[str, Any] | None:
    return ctx.one(KIND)


def _hours(cu_seconds: float) -> str:
    return f"{cu_seconds / 3600:,.1f} CU-hours"


def _failed_work(ctx: ct.Context) -> dict[str, Any]:
    """CU spent on operations that failed, as a share of the imported operations' CU.

    Severity medium: a failed operation used capacity and produced nothing, and its retry uses it
    again. At 10% of measured use it is a material part of the capacity, but it doesn't put data or
    access at risk, so not high. The 10% threshold is KratosForge's; Microsoft gives none."""
    u = _usage(ctx)
    if u is None:
        return ct.not_evaluated("No imported Capacity Metrics export has this workspace's use.", needs=NEEDS)
    ops = u["payload"].get("operations")
    if not ops:
        return ct.not_evaluated("The imported export has no operations, only totals per item, so failures can't be "
                                "seen.", needs=NEEDS_OPS)
    if not ops["status_rows"]:
        return ct.not_evaluated("The imported operations have no Status column, so failures can't be seen.",
                                needs=NEEDS_OPS + " with its Status column")
    by = ops["cu_by_status"]
    total = sum(by.values())
    if total <= 0:
        return ct.not_evaluated("The imported operations used no CU, so there is no share to work out.", needs=NEEDS_OPS)
    failed = sum(v for k, v in by.items() if k in FAILED_STATUSES)
    share = failed / total
    window = f"{(ops['window']['start'] or '')[:10]} to {(ops['window']['end'] or '')[:10]}"
    if share < MAX_FAILED_SHARE:
        return ct.passed([u["id"]], f"Failed operations used {share:.0%} of the imported operations' CU ({window}).")
    return ct.failed([u["id"]], f"Failed operations used {_hours(failed)}, {share:.0%} of the {_hours(total)} the "
                     f"imported operations used ({window}).",
                     benefit=f"Up to {_hours(failed)} of capacity freed over the imported window, estimated, if the "
                             "failures stop. Capacity freed isn't money saved.",
                     detail={"failed_cu_seconds": round(failed, 3), "cu_seconds": round(total, 3),
                             "share": round(share, 4), "window": ops["window"]})


def _refreshes(ctx: ct.Context) -> dict[str, Any]:
    """No item is refreshed more than 24 times on one day in the imported operations.

    Severity low: refreshing more often than the data changes uses capacity for nothing, but some
    reports do need near-real-time data, and the cost is capacity, not risk. The threshold (more often
    than hourly) is KratosForge's; Microsoft documents refresh limits, not a recommended frequency.
    A sampled export can only hide refreshes, so a fail is a lower bound; a pass needs a window of at
    least a day."""
    u = _usage(ctx)
    if u is None:
        return ct.not_evaluated("No imported Capacity Metrics export has this workspace's use.", needs=NEEDS)
    ops = u["payload"].get("operations")
    if not ops:
        return ct.not_evaluated("The imported export has no operations, only totals per item, so refreshes can't be "
                                "counted.", needs=NEEDS_OPS)
    if not ops["operation_rows"]:
        return ct.not_evaluated("The imported operations have no Operation column, so refreshes can't be told apart.",
                                needs=NEEDS_OPS + " with its Operation column")
    over = [r for r in ops["refreshes_per_day"] if r["count"] > MAX_REFRESHES_PER_DAY]
    if over:
        worst = over[0]
        names = ", ".join(f"{r['item']} ({r['count']} on {r['day']})" for r in over[:5])
        n = int(ops.get("refresh_days_over") or len(over))
        return ct.failed([u["id"]], f"An item refreshed more than {MAX_REFRESHES_PER_DAY} times in one day, {n} "
                                    f"{'time' if n == 1 else 'times'}: {names}.",
                         benefit=f"Refreshing {worst['item']} hourly instead would leave out about "
                                 f"{worst['count'] - MAX_REFRESHES_PER_DAY} refreshes that day. The capacity freed is "
                                 "estimated, and measured only by a new export. Capacity freed isn't money saved.",
                         detail={"over": over})
    start, end = _when(ops["window"]["start"]), _when(ops["window"]["end"])
    if not start or not end or end - start < dt.timedelta(days=1):
        return ct.not_evaluated("The imported operations cover less than a day, too little to say no item refreshes "
                                "too often.", needs=NEEDS_OPS + " covering at least a day")
    return ct.passed([u["id"]], f"No item refreshed more than {MAX_REFRESHES_PER_DAY} times on any day of the imported "
                     f"window ({start.date()} to {end.date()}).")


def _apart(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Compute-page imports whose periods don't overlap, longest first, so no day or CU counts
    twice when two exports cover the same days."""
    kept: list[dict[str, Any]] = []
    for x in sorted(items, key=lambda x: (-x["seconds"], x["period"]["start"], x["import_id"])):
        if all(x["period"]["end"] <= k["period"]["start"] or x["period"]["start"] >= k["period"]["end"] for k in kept):
            kept.append(x)
    return sorted(kept, key=lambda x: x["period"]["start"])


def _utilization(ctx: ct.Context) -> dict[str, Any]:
    """A running, paid F capacity uses at least 10% of its size over the imported Compute-page period.

    Severity medium: a capacity billed for every hour it runs but used under a tenth of that is the
    largest spend there is to look at, yet sizing for peaks, smoothing and reserved pricing can make
    a low average right, so it is a prompt to review, not high. The 10% threshold and the 7-day
    minimum are KratosForge's. Only the Compute page's items matrix gives use over a stated period;
    an export of operations doesn't, so it isn't used here."""
    st = ctx.one("capacity.state")
    assert st is not None
    sku, state = str(st["payload"].get("sku") or ""), str(st["payload"].get("state") or "")
    m = _F_SKU.fullmatch(sku)
    if not m or int(m.group(1)) <= 0:
        return ct.not_applicable(f"Not a paid F SKU ({sku or 'size not given'}).")
    if state.lower() != "active":
        return ct.not_applicable(f"The capacity is {state or 'in an unknown state'}, not running (FEA-CAP-001).")
    u = _usage(ctx)
    if u is None:
        return ct.not_evaluated("No imported Capacity Metrics export has this workspace's use.", needs=NEEDS)
    items = u["payload"].get("items") or []
    if not items:
        return ct.not_evaluated("No imported Compute-page export gives use over a stated period.", needs=NEEDS_ITEMS)
    items = _apart(items)
    seconds = sum(x["seconds"] for x in items)
    if seconds < MIN_ITEMS_DAYS * 86400:
        return ct.not_evaluated(f"The imported Compute-page periods cover {seconds / 86400:.1f} days, less than "
                                f"{MIN_ITEMS_DAYS}.", needs=NEEDS_ITEMS + f" covering at least {MIN_ITEMS_DAYS} days")
    cu_n = int(m.group(1))
    used = sum(x["cu_seconds"] for x in items)
    available = cu_n * seconds
    share = used / available
    days = f"{seconds / 86400:.0f} days"
    cited = [st["id"], u["id"]]
    if share >= MIN_UTILIZATION:
        return ct.passed(cited, f"Items used {share:.0%} of the {sku}'s capacity over {days} imported.")
    return ct.failed(cited, f"Over {days} imported, items used {_hours(used)}, which is {share:.1%} of the {_hours(available)} "
                     f"an {sku} provides. The size is today's; if it changed during the period, so does the share.",
                     benefit=f"About {_hours(available - used)} of capacity unused over the period, from the imported rows. "
                             "If the export was filtered, to one workspace for example, the capacity's real use is "
                             "higher. A smaller "
                             "SKU or pausing outside working hours could free capacity, but how much isn't "
                             "estimated yet. Money is saved only when the capacity is resized or paused, and on a "
                             "reservation not until it renews.",
                     detail={"cu_seconds": round(used, 3), "available_cu_seconds": round(available, 3),
                             "share": round(share, 4), "sku": sku, "periods": [x["period"] for x in items]})


def _t(why: str, solution: str, steps: list[str], benefit: str, effort: str, risk: str, validation: str,
       rollback: str) -> dict[str, Any]:
    return {"why": why, "solution": solution, "steps": steps, "benefit": benefit, "effort": effort, "risk": risk,
            "validation": validation, "rollback": rollback}


CONTROLS = (
    ct.Control(
        id="FEA-CAP-002", version="1.0.0", dimension="capacity_finops", title="Failed operations use capacity",
        severity="medium", requirement="kratosforge_recommendation", scope="capacity",
        evidence_kinds=("capacity.state",), optional_kinds=(KIND,), evaluate=_failed_work,
        references=(_TIMEPOINT_DOC, _METRICS_DOC),
        description=f"Operations with status Failure used at least {MAX_FAILED_SHARE:.0%} of the imported operations' "
                    "CU. Needs an imported Timepoint operations export with its Status column.",
        remediation=_t("A failed operation uses capacity and produces nothing, and its retry uses it again.",
                       "Fix the cause of the most frequent failures, and stop retries that can't succeed.",
                       ["Open the Capacity Metrics app's Timepoint page and filter Status to Failure.",
                        "Group the failures by item and find the cause of the largest.",
                        "Fix it, or pause its schedule until it is fixed.",
                        "Import a new export after a few days and assess again."],
                       "Capacity freed, estimated from the failed operations' CU. Not money saved.", "medium", "low",
                       "A new export shows the failed share under the threshold.", "Turn the paused schedule back on.")),
    ct.Control(
        id="FEA-CAP-003", version="1.0.0", dimension="capacity_finops", title="Item refreshed more often than hourly",
        severity="low", requirement="kratosforge_recommendation", scope="capacity",
        evidence_kinds=("capacity.state",), optional_kinds=(KIND,), evaluate=_refreshes,
        references=(_REFRESH_DOC, _TIMEPOINT_DOC),
        description=f"An item's refresh operations ran more than {MAX_REFRESHES_PER_DAY} times on one day of the "
                    "imported operations. Needs an imported Timepoint operations export with its Operation column.",
        remediation=_t("Refreshing more often than the data changes uses capacity for the same result.",
                       "Refresh when the source changes, or on a schedule that matches how fresh the report must be.",
                       ["Check how often the item's source data changes.",
                        "Ask its readers how fresh it must be.",
                        "Reduce the schedule or the on-demand calls (a pipeline or an API loop) that start it.",
                        "Import a new export and assess again."],
                       "Capacity freed, estimated from the left-out refreshes' CU. Not money saved.", "low", "low",
                       "A new export shows no item refreshed more than hourly.", "Restore the earlier schedule.")),
    ct.Control(
        id="FEA-CAP-004", version="1.0.0", dimension="capacity_finops", title="Paid capacity mostly unused",
        severity="medium", requirement="kratosforge_recommendation", scope="capacity",
        evidence_kinds=("capacity.state",), optional_kinds=(KIND,), evaluate=_utilization,
        references=(_COMPUTE_DOC, _SCALE_DOC, _PAUSE_DOC),
        description=f"A running F capacity's items used under {MIN_UTILIZATION:.0%} of its size over at least "
                    f"{MIN_ITEMS_DAYS} days of imported Compute-page exports. Doesn't apply to trial, A or P SKUs, "
                    "or to a capacity that isn't running.",
        remediation=_t("An F capacity is billed for every hour it runs, used or not.",
                       "Check whether a smaller SKU, or pausing when nothing runs, still covers the peaks.",
                       ["Open the Capacity Metrics app's Compute page and look at utilization over the same days.",
                        "Check the peaks and what runs at them, so a smaller SKU wouldn't throttle them.",
                        "Decide with the capacity admin: resize, pause outside working hours, or keep it.",
                        "Import a new export after the change and assess again."],
                       "Capacity freed, estimated. Money is saved only when the capacity is resized or paused, "
                       "and on a reservation not until it renews.", "medium", "medium",
                       "Utilization in a new export is above the threshold and nothing is throttled.",
                       "Scale the capacity back up, or stop the pause schedule.")),
)
