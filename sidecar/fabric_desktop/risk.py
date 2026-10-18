"""Before a run starts: what could stop it or make it costly, with the evidence (FINOPS-004).

The analyzer composes checks this app already has (organization policy, sign-in, the capacity
reading, notebook compatibility, the workload's definition and version, its run history,
measured and estimated CU, the memory guard, schedules) into one result:

- **pass**: nothing found that should stop or slow the run.
- **warning**: something the person should see first (a recent failure, a capacity last seen
  paused, a run already going, a configured CU threshold). They may go ahead.
- **blocked**: an actual technical, permission or policy restriction: the run would be refused
  or fail. Never a cost estimate on its own.
- **unknown**: an essential fact (the capacity's state) couldn't be read, and nothing else
  was found.

Each finding says what was checked, what it found, the evidence, where it came from and what
to do. Facts that aren't known stay unknown: no CU number is made up, and a cost estimate is
labeled as one. Everything here is pure: :func:`analyze` takes facts gathered by the service
and calls nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import statistics
from typing import Any

from . import measurement as ms

PASS, WARNING, BLOCKED, UNKNOWN = "pass", "warning", "blocked", "unknown"
OUTCOMES = (PASS, WARNING, BLOCKED, UNKNOWN)
LABELS = {PASS: "Pass", WARNING: "Warning", BLOCKED: "Blocked", UNKNOWN: "Unknown"}
_ORDER = {BLOCKED: 0, WARNING: 1, UNKNOWN: 2, PASS: 3}

DEFAULTS: dict[str, Any] = {
    "warn_run_cu_hours": None,  # warn when this item's last run used more than this (off unless set)
    "warn_failed_runs": 2,  # warn when this many of the last five runs failed
    "schedule_window_minutes": 60,  # warn when a schedule on this PC runs the item this soon
    "require_acknowledgement": False,  # a warning must be acknowledged before the run starts
}

CAPACITY_SOURCE = "Capacity reading (Fabric REST API: GET /workspaces/{id}, GET /capacities)"
POLICY_SOURCE = "Organization policy (allow_fabric_runs)"
HISTORY_SOURCE = "Runs of this workload recorded by this app"


def settings(saved: dict[str, Any] | None) -> dict[str, Any]:
    """The risk settings with defaults filled in; unknown keys are dropped."""
    out = dict(DEFAULTS)
    for k, v in (saved or {}).items():
        if k in DEFAULTS:
            out[k] = v
    return out


def finding(check: str, outcome: str, title: str, detail: str, *, evidence: list[str] | None = None,
            remedy: str = "", source: str = "", observed_at: str | None = None, essential: bool = False,
            measurement: dict[str, Any] | None = None) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    return {"check": check, "outcome": outcome, "label": LABELS[outcome], "title": title, "detail": detail,
            "evidence": [e for e in (evidence or []) if e], "remedy": remedy, "source": source,
            "observed_at": observed_at, "essential": essential, "measurement": measurement,
            "can_override": outcome == WARNING}


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "at an unknown time"
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes ago"
    if seconds < 2 * 86400:
        return f"{round(seconds / 3600)} hours ago"
    return f"{round(seconds / 86400)} days ago"


def duration(seconds: float) -> str:
    if seconds < 90:
        return f"{round(seconds)} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


# ---------------------------------------------------------------- checks


def check_policy(f: dict[str, Any]) -> dict[str, Any] | None:
    if f["where"] != "fabric":
        return None
    if f.get("policy", {}).get("allow_fabric_runs", True):
        return finding("policy", PASS, "Fabric runs are allowed", "Your organization's policy lets this app start jobs on "
                       "Fabric capacity.", source=POLICY_SOURCE)
    return finding("policy", BLOCKED, "Your organization doesn't allow Fabric runs from this app",
                   "IT has set allow_fabric_runs to false, so the app won't start jobs on Fabric capacity.",
                   remedy="Run it on this PC instead, or ask your IT team.", source=POLICY_SOURCE, essential=True)


def check_sign_in(f: dict[str, Any]) -> dict[str, Any] | None:
    if f["where"] != "fabric" and not f.get("needs_sign_in"):
        return None
    if f.get("signed_in"):
        return None
    return finding("sign_in", BLOCKED, "You're not signed in", "This action uses Fabric, so it needs your sign-in.",
                   remedy="Sign in first.", source="This PC's sign-in", essential=True)


def check_capacity(f: dict[str, Any], now: dt.datetime) -> dict[str, Any] | None:
    """The capacity the run would use, from the last reading. Paused is blocked only on a reading
    from the last hour; an older reading says when it was seen."""
    if f["where"] != "fabric":
        return finding("capacity", PASS, "Runs on this PC", "No Fabric job is started, so the capacity's state doesn't "
                       "matter. Reading or writing OneLake from this PC still uses some of its capacity.",
                       source="The workload's location")
    obs = f.get("capacity")
    if not obs:
        return finding("capacity", UNKNOWN, "The capacity hasn't been read",
                       "This workspace's capacity hasn't been read on this PC, so its state isn't known.",
                       remedy="Check again, or open Monitor > Cost > Capacities and choose Refresh.",
                       source=CAPACITY_SOURCE, essential=True)
    access = obs.get("access")
    cap = obs.get("capacity") or {}
    name = cap.get("name") or obs.get("capacity_id") or "its capacity"
    age = obs.get("age_seconds")
    stale = bool(obs.get("stale")) or bool(obs.get("cached"))
    when = obs.get("observed_at")
    evidence = [f"Workspace: {obs.get('workspace_name') or obs.get('workspace_id') or 'not set'}",
                f"Capacity: {name}" + (f" ({cap['sku']})" if cap.get("sku") else ""),
                f"Read {_age(age)}" + (" (an older reading: the last one failed)" if obs.get("cached") else "")]
    common = {"source": CAPACITY_SOURCE, "observed_at": when, "essential": True}
    if access in ("no_workspace", "no_capacity", "workspace_no_access", "workspace_not_found"):
        title = {"no_workspace": "No workspace is assigned", "no_capacity": "The workspace isn't on a capacity",
                 "workspace_no_access": "You can't open the workspace",
                 "workspace_not_found": "The workspace doesn't exist any more"}[access]
        return finding("capacity", BLOCKED, title, obs.get("status_text") or obs.get("access_text") or title,
                       evidence=evidence, remedy=obs.get("action", ""), **common)
    if access in ("capacity_not_visible", "scope_not_granted"):
        return finding("capacity", UNKNOWN, obs.get("access_text") or "The capacity can't be read",
                       "The workspace is on a capacity, but you can't read its state. That isn't an error and "
                       "doesn't stop the run; it only means this check can't tell whether the capacity is running.",
                       evidence=evidence, remedy=obs.get("action", ""), **common)
    if access != "ok":
        return finding("capacity", UNKNOWN, obs.get("access_text") or "The capacity couldn't be read",
                       "Its state isn't known.", evidence=evidence, remedy=obs.get("action", ""), **common)
    status = obs.get("status")
    if status == "not_running":
        if stale:
            return finding("capacity", WARNING, f"{name} was paused when last read",
                           f"It was not running {_age(age)}. If it is still paused, the run will fail with "
                           "CapacityNotActive.", evidence=evidence,
                           remedy="Check again before you run, or ask your capacity admin to resume it.", **common)
        return finding("capacity", BLOCKED, f"{name} is paused",
                       obs.get("status_text") or "The capacity isn't running, so Fabric refuses new jobs.",
                       evidence=evidence, remedy="Ask your capacity admin to resume it, or run on this PC.", **common)
    if status == "running":
        if stale:
            return finding("capacity", UNKNOWN, f"{name} was running when last read",
                           f"The reading is from {_age(age)}, so its state now isn't known.", evidence=evidence,
                           remedy="Check again for a current reading.", **common)
        return finding("capacity", PASS, f"{name} is running",
                       "Running only means work can start. How busy it is isn't known to this app: Fabric has no "
                       "utilization API for developers, and an overloaded capacity can still delay or throttle jobs.",
                       evidence=evidence, **common)
    return finding("capacity", UNKNOWN, f"{name}'s state isn't known", obs.get("status_text") or "Its state isn't known.",
                   evidence=evidence, remedy="Check again.", **common)


def check_definition(f: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if f.get("definition_errors"):
        out.append(finding("definition", BLOCKED, "The definition has problems", "; ".join(f["definition_errors"][:3]),
                           remedy="Fix the definition in Data mesh, then run it.", source="The project's definitions",
                           essential=True))
    v = f.get("version") or {}
    if v.get("behind"):
        out.append(finding("definition", WARNING, "This environment runs an older version",
                           f"{f.get('env_label') or 'This environment'} has version {str(v.get('deployed'))[:8]}; your "
                           "newer changes aren't included until you promote them.",
                           remedy="Promote first if the run should include your changes.", source="Deployment records"))
    return out


def check_compatibility(f: dict[str, Any]) -> list[dict[str, Any]]:
    """Notebooks only: whether it can run where it's going, and whether this PC could run it instead."""
    compat = f.get("compatibility")
    if not compat:
        return []
    src = "Notebook compatibility check (the same one as Runs on)"
    out = []
    here, other = ("fabric", "local") if f["where"] == "fabric" else ("local", "fabric")
    reasons = compat.get(here, {}).get("reasons") or []
    place = "Fabric" if here == "fabric" else "this PC"
    if reasons:
        out.append(finding("compatibility", WARNING, f"Parts of the notebook may not run on {place}",
                           "The notebook uses things that don't work there; the run is likely to fail at those cells.",
                           evidence=[f"Cell {r['cell']}: {r['text']}" if r.get("cell") else r["text"] for r in reasons[:5]],
                           remedy="Change those cells, or run it where they work.", source=src))
    if f["where"] == "fabric":
        local = compat.get(other, {}).get("reasons") or []
        if local:
            out.append(finding("local_alternative", PASS, "Needs Fabric",
                               "It can't run on this PC, so Fabric is the right place for it.",
                               evidence=[f"Cell {r['cell']}: {r['text']}" if r.get("cell") else r["text"] for r in local[:5]],
                               source=src))
        else:
            out.append(finding("local_alternative", PASS, "This PC could run it too",
                               "Nothing in the notebook needs Fabric, so you could try it on this PC first without "
                               "a Fabric job (its OneLake reads and writes still use some capacity). That doesn't promise the same results: compare with Prove or Side by side.",
                               source=src))
    return out


def _finished(r: dict[str, Any]) -> float | None:
    if r.get("seconds") not in (None, ""):
        return float(r["seconds"])
    a, b = ms.parse_time(r.get("started_at")), ms.parse_time(r.get("ended_at"))
    return (b - a).total_seconds() if a and b and b >= a else None


def check_history(f: dict[str, Any], cfg: dict[str, Any], now: dt.datetime) -> list[dict[str, Any]]:
    runs = sorted(f.get("runs") or [], key=lambda r: str(r.get("started_at") or ""), reverse=True)
    out = []
    running = [r for r in runs if r.get("state") == "running"]
    if running:
        started = ms.parse_time(running[0].get("started_at"))
        out.append(finding("concurrency", WARNING, "A run is still going",
                           f"A run of this workload started {_age((now - started).total_seconds()) if started else 'earlier'} "
                           "and hasn't finished. Two runs at once can write the same tables and use capacity twice.",
                           evidence=[f"{r.get('ran_on') or ''} · started {r.get('started_at')}" for r in running[:3]],
                           remedy="Wait for it to finish, or cancel it first.", source=HISTORY_SOURCE))
    done = [r for r in runs if r.get("state") in ("succeeded", "failed", "cancelled")][:5]
    if not done:
        out.append(finding("history", UNKNOWN, "No earlier runs", "There's no run history for this workload here, so "
                           "its run time and failures aren't known.", source=HISTORY_SOURCE))
        return out
    failed = [r for r in done if r.get("state") == "failed"]
    last = done[0]
    threshold = int(cfg.get("warn_failed_runs") or 0)
    if last.get("state") == "failed":
        out.append(finding("failures", WARNING, "The last run failed",
                           "A run now may fail the same way until the cause is fixed.",
                           evidence=[f"{last.get('started_at')}: {last.get('message') or 'no message recorded'}"],
                           remedy="Read the failure first.", source=HISTORY_SOURCE))
    elif threshold and len(failed) >= threshold:
        out.append(finding("failures", WARNING, f"{len(failed)} of the last {len(done)} runs failed",
                           "The last run succeeded, but this workload fails often.",
                           evidence=[f"{r.get('started_at')}: {r.get('message') or 'no message recorded'}" for r in failed[:3]],
                           source=HISTORY_SOURCE))
    times = [s for r in done if r.get("state") == "succeeded" and (s := _finished(r)) is not None]
    if times:
        out.append(finding("runtime", PASS, f"Usually takes about {duration(statistics.median(times))}",
                           f"Median of {len(times)} successful run{'s' if len(times) != 1 else ''}; the longest took "
                           f"{duration(max(times))}.", source=HISTORY_SOURCE))
    else:
        out.append(finding("runtime", UNKNOWN, "Run time isn't known", "No successful run with a recorded end time.",
                           source=HISTORY_SOURCE))
    return out


def check_consumption(f: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any] | None:
    """The CU of this workload's recent Fabric runs: measured when an import matched them, otherwise
    the app's estimate, labeled as one. Warns only above a threshold the person set."""
    if f["where"] != "fabric":
        return None
    measured = [m for m in f.get("measured") or [] if m and m.get("value") is not None]
    estimated = [m for m in f.get("estimated") or [] if m and m.get("value") is not None]
    pick = measured[0] if measured else estimated[0] if estimated else None
    if pick is None:
        return finding("consumption", UNKNOWN, "CU use isn't known",
                       "No measured CU for this workload's runs, and no estimate. It stays unknown rather than 0.",
                       remedy="Import a Capacity Metrics export in Monitor > Cost to measure it.",
                       source="Measured usage imports and cost estimates")
    hours = ms.convert(pick["value"], pick["unit"], ms.CU_HOURS)
    kind = ms.LABELS[pick["classification"]].lower()
    evidence = [f"{ms.LABELS[pick['classification']]}: {hours:.3f} CU hours" + (f" (run {pick.get('run_id')})" if pick.get("run_id") else "")]
    if not measured:
        evidence.append("No import measured these runs; the figure is the app's estimate from run time.")
    limit = cfg.get("warn_run_cu_hours")
    if limit not in (None, "") and hours > float(limit):
        return finding("consumption", WARNING, f"The last run used more than {float(limit):g} CU hours ({kind})",
                       f"You asked to be warned above {float(limit):g} CU hours. This is the {kind} figure for the "
                       "last run; the next run may differ.", evidence=evidence,
                       remedy="Consider trying it on this PC, or on a smaller input, first.",
                       source=pick.get("source") or pick.get("method") or "", measurement=pick)
    return finding("consumption", PASS, f"The last run used about {hours:.2f} CU hours ({kind})",
                   "For reference; the next run may differ. A price for it is in Monitor > Cost.",
                   evidence=evidence, source=pick.get("source") or pick.get("method") or "", measurement=pick)


def check_input(f: dict[str, Any]) -> dict[str, Any] | None:
    pre = f.get("input")
    if not pre:
        return None
    if pre.get("error"):
        return finding("input", UNKNOWN, "Input size isn't known", f"The source couldn't be sized: {pre['error']}",
                       source="Delta log of the source")
    est = pre.get("estimate")
    if not est:
        return finding("input", UNKNOWN, "Input size isn't known", pre.get("note") or "Only Delta sources are sized.",
                       source="Delta log of the source")
    from .meta.preflight import mb
    text = (f"about {est['rows']:,} rows in {est['files']} of {est['files_total']} files "
            f"({mb(est['bytes_on_disk'])} on disk)")
    if f["where"] != "fabric" and pre.get("stop"):
        return finding("input", BLOCKED, "Too big for this PC's free memory", pre["stop"], evidence=[text],
                       remedy="Free memory, set [load] batch_by, or run it in Fabric.", source="Memory guard (Delta log)",
                       essential=True)
    return finding("input", PASS, f"Reads {text}", pre.get("note") or "From the Delta log, without reading the data.",
                   source="Delta log of the source")


def check_schedules(f: dict[str, Any], cfg: dict[str, Any], now: dt.datetime) -> dict[str, Any] | None:
    scheds = [s for s in f.get("schedules") or [] if s.get("enabled")]
    if not scheds:
        return None
    window = dt.timedelta(minutes=float(cfg.get("schedule_window_minutes") or 0))
    soon = [s for s in scheds if (t := ms.parse_time(s.get("next_run"))) and now <= t <= now + window]
    evidence = [f"{s.get('where')}: {s.get('text') or 'scheduled'}" + (f", next {s['next_run']}" if s.get("next_run") else "")
                for s in scheds[:4]]
    if soon:
        return finding("schedule", WARNING, "A schedule runs it soon",
                       "A schedule on this PC runs this workload within the next "
                       f"{round(window.total_seconds() / 60)} minutes; running it now as well does the work twice.",
                       evidence=evidence, remedy="Let the schedule run it, or run now and skip the next schedule.",
                       source="Schedules")
    return finding("schedule", PASS, "Also on a schedule", "None is due soon on this PC. Fabric schedules' next times "
                   "aren't read here.", evidence=evidence, source="Schedules")


# ---------------------------------------------------------------- the result


def _id(result: str, findings: list[dict[str, Any]], facts: dict[str, Any]) -> str:
    """Identifies this assessment's outcome for an acknowledgement: the same findings give the same id."""
    key = [facts.get("workload_id"), facts.get("action"), facts.get("env"), result,
           [(x["check"], x["outcome"], x["title"]) for x in findings]]
    return "r-" + hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:16]


def analyze(facts: dict[str, Any], cfg: dict[str, Any] | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    """The assessment for one action on one workload. ``facts`` are gathered by the service; this
    calls nothing.

    ``facts``: workload_id, workload, action, label, env, env_label, where ("fabric" or "this_pc"),
    needs_sign_in, policy {allow_fabric_runs}, signed_in, capacity (a capacity reading with its
    freshness, or None), compatibility (fabric_run.compute_check, notebooks only),
    definition_errors, version, runs (normalized workload runs), measured and estimated (CU
    measurements of recent runs, newest first), input (meta preflight), schedules, reads."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = settings(cfg)
    found: list[dict[str, Any] | None] = [check_policy(facts), check_sign_in(facts), check_capacity(facts, now)]
    found += check_definition(facts)
    found += check_compatibility(facts)
    found.append(check_input(facts))
    found += check_history(facts, cfg, now)
    found.append(check_consumption(facts, cfg))
    found.append(check_schedules(facts, cfg, now))
    findings = sorted((x for x in found if x), key=lambda x: _ORDER[x["outcome"]])
    outcomes = {x["outcome"] for x in findings}
    if BLOCKED in outcomes:
        result = BLOCKED
    elif WARNING in outcomes:
        result = WARNING
    elif any(x["outcome"] == UNKNOWN and x["essential"] for x in findings):
        result = UNKNOWN
    else:
        result = PASS
    first = next((x for x in findings if x["outcome"] == result), None)
    summary = {
        BLOCKED: "This run can't start: " + (first["title"] if first else ""),
        WARNING: f"{sum(x['outcome'] == WARNING for x in findings)} thing(s) to check before you run",
        UNKNOWN: "Couldn't check everything: " + (first["title"] if first else ""),
        PASS: "Nothing found that should stop this run",
    }[result]
    return {"id": _id(result, findings, facts), "result": result, "label": LABELS[result], "summary": summary,
            "workload_id": facts.get("workload_id"), "workload": facts.get("workload"), "action": facts.get("action"),
            "action_label": facts.get("label"), "env": facts.get("env"), "where": facts["where"],
            "findings": findings, "can_proceed": result != BLOCKED,
            "needs_acknowledgement": result == WARNING and bool(cfg.get("require_acknowledgement")),
            "evaluated_at": now.isoformat(timespec="seconds"),
            "reads": facts.get("reads") or [],
            "note": "Read only: nothing was started, changed or paused to work this out."}
