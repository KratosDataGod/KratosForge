"""Which imported usage rows belong to which of this app's Fabric runs, and what is covered.

A row is matched to a run only when it can't be anyone else's: same workspace name, same item
name, its time overlapping the run's (give or take :data:`TOLERANCE_S`), and no other run fitting.
Several rows can match one run (a job can show as more than one operation). Everything else stays
unmatched with its reason, and its CU is still shown: it is use on the capacity that this app
didn't start (other people, schedules, other items) or can't tie to one run.

Exports carry names, not ids, and the Capacity Metrics app's operation ids aren't documented to
equal Fabric job instance ids, so ids aren't used to match.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .. import measurement as ms
from ..fabric_compute import RUNNER

TOLERANCE_S = 300
_MAX_RUNS = 200

NO_RUN = "No run of this item started from this app at that time"
SEVERAL = "Fits more than one of this app's runs"
UNTIMED = "A total per item, which can't be tied to single runs"


def _name(s: Any) -> str:
    return " ".join(str(s or "").split()).casefold()


def item_name(run: dict[str, Any]) -> str | None:
    """The Fabric item a run executed, by name, as the export would show it; None when not recorded."""
    kind = run.get("kind")
    if run.get("item_name"):
        return run["item_name"]
    if kind in ("Notebook run", "Scheduled item run"):
        return run.get("name") or None
    if kind == "Metadata run":
        return RUNNER
    if kind == "Table maintenance":
        name, table = str(run.get("name") or ""), str(run.get("table") or "")
        name = name.removeprefix("Vacuum ")
        if table and name.endswith(" " + table):
            return name[: -len(table) - 1].strip() or None
    return None


def _window(run: dict[str, Any], now: dt.datetime) -> tuple[dt.datetime, dt.datetime] | None:
    start = ms.parse_time(run.get("started_at"))
    if not start:
        return None
    return start, ms.parse_time(run.get("finished_at")) or max(now, start)


def reconcile(imports: list[dict[str, Any]], runs: list[dict[str, Any]], estimates: dict[str, dict[str, Any]],
              since: dt.datetime, until: dt.datetime, ws_names: dict[str, str] | None = None,
              now: dt.datetime | None = None) -> dict[str, Any]:
    """Reconcile imported rows in ``[since, until)`` with this app's Fabric runs started then.

    ``estimates`` maps a job id to its estimated ``fabric_job_cu`` (costs.activities);
    ``ws_names`` maps workspace ids to names for runs that didn't record one."""
    now = now or dt.datetime.now(dt.timezone.utc)
    ws_names = ws_names or {}
    tol = dt.timedelta(seconds=TOLERANCE_S)
    rows: list[dict[str, Any]] = []
    outside = 0
    sources: dict[str, str] = {}
    for imp in imports:
        for r in imp.get("rows", []):
            p = r["cu"]["period"]
            start, end = ms.parse_time(p["start"]), ms.parse_time(p["end"])
            if r.get("start"):
                if start and since <= start < until:
                    rows.append(r | {"_import": imp["id"], "_start": start, "_end": end or start})
            elif start and end and since <= start and end <= until:
                rows.append(r | {"_import": imp["id"], "_start": start, "_end": end})
            elif start and end and start < until and since < end:
                outside += 1  # a period total that only partly falls in the period can't be split
        sources[imp["id"]] = imp.get("imported_at") or ""

    app_runs = []
    for run in runs:
        w = _window(run, now)
        if not w or not since <= w[0] < until or not run.get("job_id"):
            continue
        ws = run.get("workspace_name") or ws_names.get(run.get("workspace_id") or "", "")
        app_runs.append({"run": run, "start": w[0], "end": w[1], "ws": _name(ws), "item": _name(item_name(run)),
                         "rows": []})

    unmatched: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if not r.get("start"):
            unmatched.setdefault(UNTIMED, []).append(r)
            continue
        fits = [a for a in app_runs if a["item"] and a["ws"] == _name(r["workspace"]) and a["item"] == _name(r["item"])
                and r["_start"] <= a["end"] + tol and r["_end"] >= a["start"] - tol]
        if len(fits) == 1:
            fits[0]["rows"].append(r)
        else:
            unmatched.setdefault(NO_RUN if not fits else SEVERAL, []).append(r)

    # What the imports cover: a workspace name, over the time span of that import's timed rows.
    cover: list[tuple[str, dt.datetime, dt.datetime]] = []
    for imp in imports:
        timed = [r for r in imp.get("rows", []) if r.get("start")]
        if timed:
            first = min(ms.parse_time(r["start"]) for r in timed)
            last = max(ms.parse_time(r.get("end") or r["start"]) for r in timed)
            cover += [(w, first, last) for w in {_name(r["workspace"]) for r in timed}]

    def covered(a: dict[str, Any]) -> bool:
        return any(w == a["ws"] and first - tol <= a["start"] <= last + tol for w, first, last in cover)

    by_run, counts = [], {"total": len(app_runs), "matched": 0, "no_row": 0, "not_covered": 0, "no_item_name": 0}
    matched_measured, matched_estimates = [], []
    for a in app_runs:
        run = a["run"]
        est = estimates.get(run["job_id"]) or ms.unknown(
            "fabric_job_cu", ms.CU_SECONDS, "Metadata runs are estimated per table from the run log, not per Fabric job."
            if run.get("kind") == "Metadata run" else "No estimate for this run.", run_id=run["job_id"])
        if a["rows"]:
            counts["matched"] += 1
            measured = ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, sum(r["cu"]["value"] for r in a["rows"]),
                               run_id=run["job_id"], workspace_id=run.get("workspace_id") or None,
                               source="; ".join(sorted({r["cu"]["source"] for r in a["rows"]})),
                               method=f"Sum of {len(a['rows'])} imported row{'s' if len(a['rows']) != 1 else ''} "
                                      "matched by workspace, item and time",
                               collected_at=max(sources[r["_import"]] for r in a["rows"]),
                               period_start=a["start"], period_end=a["end"], uncertainty=a["rows"][0]["cu"]["uncertainty"],
                               reconciliation="matched")
            matched_measured.append(measured)
            matched_estimates.append(est)
            state = "matched"
        elif not a["item"]:
            counts["no_item_name"] += 1
            measured, state = None, "no_item_name"
        elif covered(a):
            counts["no_row"] += 1
            measured, state = None, "no_row"
        else:
            counts["not_covered"] += 1
            measured, state = None, "not_covered"
        by_run.append({"job_id": run["job_id"], "kind": run.get("kind"), "name": run.get("name"), "env": run.get("env"),
                       "workspace": run.get("workspace_name") or ws_names.get(run.get("workspace_id") or ""),
                       "item": item_name(run), "started_at": ms.parse_time(run.get("started_at")).isoformat(timespec="seconds"),
                       "state": state, "rows": len(a["rows"]), "measured": measured, "estimated": est})

    def tot(parts: list[dict[str, Any]], metric: str) -> dict[str, Any] | None:
        return ms.total(parts, metric, ms.CU_HOURS) if parts else None

    unmatched_rows = [r for rs in unmatched.values() for r in rs]
    measured_total, estimated_total = tot(matched_measured, "fabric_job_cu"), tot(matched_estimates, "fabric_job_cu")
    ratio = None
    if measured_total and estimated_total and estimated_total["value"] and estimated_total["complete"]:
        ratio = round(measured_total["value"] / estimated_total["value"], 3)
    return {
        "period": {"start": since.isoformat(timespec="seconds"), "end": until.isoformat(timespec="seconds")},
        "imports": len({r["_import"] for r in rows}), "rows": len(rows), "rows_outside_period": outside,
        "imported": tot([r["cu"] for r in rows], "fabric_capacity_cu"),
        "matched": {"rows": sum(len(a["rows"]) for a in app_runs), "runs": counts["matched"],
                    "measured": measured_total, "estimated": estimated_total, "measured_to_estimate": ratio},
        "unmatched": {"rows": len(unmatched_rows), "measured": tot([r["cu"] for r in unmatched_rows], "fabric_capacity_cu"),
                      "by_reason": {k: len(v) for k, v in unmatched.items()}},
        "runs": counts,
        "by_run": sorted(by_run, key=lambda b: b["started_at"], reverse=True)[:_MAX_RUNS],
        "tolerance_s": TOLERANCE_S,
    }
