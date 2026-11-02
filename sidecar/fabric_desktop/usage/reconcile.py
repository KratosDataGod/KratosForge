"""Which imported usage rows belong to which of this app's Fabric runs, and what is covered.

A row is matched to a run by name and time: same workspace name, same item name, the row starting
inside the run (between when this app started it and when it ended), and no other run fitting.
Names and times are all an export has, so a match is likely, not proven: someone else running the
same item while this app's run was going would be counted with it. A row that started before the
run, such as a schedule of the same notebook that ended just before, isn't matched. Several rows
can match one run (a job can show as more than one operation). Everything else stays unmatched
with its reason, and its CU is still shown: it is use on the capacity that this app didn't start
(other people, schedules, other items) or can't tie to one run.

Exports carry names, not ids, and the Capacity Metrics app's operation ids aren't documented to
equal Fabric job instance ids, so ids aren't used to match.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .. import measurement as ms
from ..fabric_compute import RUNNER

TOLERANCE_S = 300  # how far past an import's first and last row a run still counts as covered by it
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
              now: dt.datetime | None = None, capacity_envs: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Reconcile imported rows in ``[since, until)`` with this app's Fabric runs started then.

    ``estimates`` maps a job id to its estimated ``fabric_job_cu`` (costs.activities);
    ``ws_names`` maps workspace ids to names for runs that didn't record one. Rows of a whole
    capacity (cost exports) aren't matched to runs; they are summed per capacity in ``billed``."""
    now = now or dt.datetime.now(dt.timezone.utc)
    ws_names = ws_names or {}
    tol = dt.timedelta(seconds=TOLERANCE_S)
    rows: list[dict[str, Any]] = []
    outside = 0
    sources: dict[str, str] = {}
    for imp in imports:
        # How far a cost export's data reaches: of two exports with the same day, the later-reaching one
        # has that day complete, whichever was imported last.
        ends = [ms.parse_time(r["cost"]["period"]["end"]) for r in imp.get("rows", []) if r.get("level") == "capacity"]
        through = max((e for e in ends if e), default=None)
        for r in imp.get("rows", []):
            p = (r.get("cu") or r["cost"])["period"]
            start, end = ms.parse_time(p["start"]), ms.parse_time(p["end"])
            if r.get("start"):
                if start and since <= start < until:
                    rows.append(r | {"_import": imp["id"], "_imported_at": imp.get("imported_at") or "", "_through": through,
                                     "_start": start, "_end": end or start})
            elif start and end and since <= start and end <= until:
                rows.append(r | {"_import": imp["id"], "_imported_at": imp.get("imported_at") or "", "_through": through,
                                 "_start": start, "_end": end})
            elif start and end and start < until and since < end:
                outside += 1  # a period total that only partly falls in the period can't be split
        sources[imp["id"]] = imp.get("imported_at") or ""

    billed_rows = _latest(r for r in rows if r.get("level") == "capacity")
    rows = [r for r in rows if r.get("level") != "capacity"]

    app_runs: list[dict[str, Any]] = []
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
                and a["start"] <= r["_start"] <= a["end"]]
        if len(fits) == 1:
            fits[0]["rows"].append(r)
        else:
            unmatched.setdefault(NO_RUN if not fits else SEVERAL, []).append(r)

    # What the imports cover: a workspace name, over the time span of that import's timed rows.
    cover: list[tuple[str, dt.datetime, dt.datetime]] = []
    for imp in imports:
        # A start that can't be read covers nothing (it isn't compared with the times that can).
        timed = [(r, t) for r in imp.get("rows", []) if r.get("start") and r.get("level") != "capacity"
                 if (t := ms.parse_time(r["start"])) is not None]
        if timed:
            first = min(t for _, t in timed)
            last = max(ms.parse_time(r.get("end")) or t for r, t in timed)
            cover += [(w, first, last) for w in {_name(r["workspace"]) for r, _ in timed}]

    def covered(a: dict[str, Any]) -> bool:
        return any(w == a["ws"] and first - tol <= a["start"] <= last + tol for w, first, last in cover)

    by_run: list[dict[str, Any]] = []
    counts = {"total": len(app_runs), "matched": 0, "no_row": 0, "not_covered": 0, "no_item_name": 0}
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
                                      "matched by workspace name, item name and start time",
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
                       "item": item_name(run), "started_at": a["start"].isoformat(timespec="seconds"),
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
        "imports": len({r["_import"] for r in rows + billed_rows}), "rows": len(rows), "rows_outside_period": outside,
        "imported": tot([r["cu"] for r in rows], "fabric_capacity_cu"),
        "matched": {"rows": sum(len(a["rows"]) for a in app_runs), "runs": counts["matched"],
                    "measured": measured_total, "estimated": estimated_total, "measured_to_estimate": ratio},
        "unmatched": {"rows": len(unmatched_rows), "measured": tot([r["cu"] for r in unmatched_rows], "fabric_capacity_cu"),
                      "by_reason": {k: len(v) for k, v in unmatched.items()}},
        "runs": counts,
        "by_run": sorted(by_run, key=lambda b: b["started_at"], reverse=True)[:_MAX_RUNS],
        "tolerance_s": TOLERANCE_S,
        "billed": billed(billed_rows, capacity_envs or {}),
    }


def _latest(rows: Any) -> list[dict[str, Any]]:
    """Cost rows with one row per capacity, meter, day and charge: when several imports have the
    same one (month-to-date exports repeat earlier days), the one from the export whose data reaches
    furthest counts, so an older file imported later doesn't replace a finished day with a partial
    one; then the one imported last. Imports come newest first (Store.all), so on a full tie the
    first one seen is kept."""
    def rank(r: dict[str, Any]) -> tuple[float, str]:
        return (r["_through"].timestamp() if r.get("_through") else 0.0, r.get("_imported_at") or "")

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        slot = r.get("slot") or r["key"]
        if slot not in out or rank(r) > rank(out[slot]):
            out[slot] = r
    return list(out.values())


def billed(rows: list[dict[str, Any]], capacity_envs: dict[str, list[str]]) -> list[dict[str, Any]]:
    """What each capacity was billed over the period, from imported cost exports: CU hours of its
    compute meters and its cost per currency, both measured. ``capacity_envs`` maps a capacity
    name (lower case) to the environments whose workspaces are on it."""
    from . import cost_totals

    caps: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        caps.setdefault(r["capacity_id"], []).append(r)
    out = []
    for cap_id, rs in sorted(caps.items(), key=lambda kv: kv[1][0]["capacity"].lower()):
        name = rs[0]["capacity"]
        cu = [r["cu"] for r in rs if r.get("cu")]
        days = sorted({r["start"][:10] for r in rs})
        meters: dict[str, int] = {}
        for r in rs:
            meters[r.get("meter") or "?"] = meters.get(r.get("meter") or "?", 0) + 1
        out.append({"capacity_id": cap_id, "capacity": name, "environments": capacity_envs.get(name.lower(), []),
                    "rows": len(rs), "days": len(days), "first_day": days[0], "last_day": days[-1],
                    "cu": ms.total(cu, "billed_capacity_cu", ms.CU_HOURS) if cu else None,
                    "cost": cost_totals([r["cost"] for r in rs]),
                    "meters": dict(sorted(meters.items(), key=lambda kv: -kv[1])[:8])})
    return out
