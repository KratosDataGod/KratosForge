"""What an evaluation looks for in one environment, from its run log alone.

The agent runs this on a timer and the app runs it when asked; both read the same run log and
the same entity files, so they see the same problems and the incident log (incidents.py) keeps
one record for each. Signals carry an ``evidence_id`` (the failed run, the last good load) so a
new failure is a new occurrence and a problem resolved by hand comes back only on new evidence.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..meta import freshness as freshness_mod
from ..meta import objectives as objectives_mod
from . import model
from .classify import classify
from .incidents import KIND_LABELS


WORKLOAD_KINDS = frozenset({"run_failed", "needs_decision", "late", "objective", "promise"})


def signals(project_name: str, entities: dict[str, dict[str, Any]], runs: list[dict[str, Any]], env: str,
            now: dt.datetime | None = None, criticality: dict[str, str] | None = None,
            team: str = "", promises: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    """Problems with the entities of ``project_name`` in ``env``. ``entities`` maps a name to its spec
    (the entity file); ``runs`` are that environment's run log rows, newest first. ``criticality``
    maps a name to one assigned elsewhere; one declared in the entity's ``[objectives]`` wins.
    ``promises`` (meta.mesh.promises) are what data products promise their readers about an entity."""
    now = now or dt.datetime.now().astimezone()
    criticality = criticality or {}
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        by_entity.setdefault(r.get("entity", ""), []).append(r)
    out: list[dict[str, Any]] = []
    for name, spec in sorted(entities.items()):
        wid = model.meta_id(project_name, name)
        mine = by_entity.get(name, [])
        # The newest run that finished with a verdict; one still going or skipped says nothing yet.
        latest = next((r for r in mine if r.get("status") in ("ok", "failed", "needs_decision")), None)
        owner = str(spec.get("owner") or "")
        owner = "" if owner.strip().lower() in model.PLACEHOLDER_OWNERS else owner
        objectives = spec.get("objectives")
        declared = objectives if isinstance(objectives, dict) else {}
        crit = str(declared.get("criticality") or criticality.get(name) or "")
        base = {"workload": wid, "name": name, "owner": owner, "team": team, "criticality": crit}
        if latest and latest.get("status") == "failed":
            out.append(base | {"kind": "run_failed", "evidence_id": latest.get("run_id"),
                               "title": f"{name} failed in {env.title()}",
                               "text": latest.get("message") or "The run failed.",
                               "classification": classify(latest.get("message")),
                               "evidence": _run_evidence(latest)})
        elif latest and latest.get("status") == "needs_decision":
            out.append(base | {"kind": "needs_decision", "evidence_id": latest.get("run_id"),
                               "title": f"{name} needs a decision in {env.title()}",
                               "text": latest.get("message") or "Schema drift or quarantined rows are waiting for review.",
                               "evidence": _run_evidence(latest)})
        p = freshness_mod.promise(spec)
        own_late = False
        if p and env in p["envs"]:
            f = freshness_mod.status(p, mine, now)
            own_late = f["status"] == "late"
            if f["status"] == "late":
                out.append(base | {"kind": "late", "evidence_id": f"late:{f.get('last_ok')}",
                                   "title": f"{name} is late in {env.title()}", "text": "; ".join(f["reasons"]),
                                   "evidence": {"source": "freshness promise", "expected": f["expected"],
                                                "last_ok": f["last_ok"], "due_at": f["due_at"]}})
        promised = (promises or {}).get(name) or []
        if own_late:
            # The table's own lateness is the one incident; it names the products whose promise it also breaks.
            late_for = [x["product"] for x in promised if env in (x.get("envs") or []) and (x.get("within_hours") or x.get("by"))
                        and freshness_mod.status({"within_hours": x.get("within_hours"), "by": x.get("by")}, mine, now)["status"] == "late"]
            if late_for:
                out[-1]["evidence"]["promise_products"] = late_for
        broken = promise_signal(name, promised, mine, env, now, skip_late=own_late)
        if broken:
            out.append(base | broken)
        o = objectives_mod.objective(spec, criticality.get(name))
        if o and env in o["envs"]:
            res = objectives_mod.success(o, mine)
            if res["status"] == "breached":
                newest = next((r for r in mine if r.get("status") in ("ok", "failed", "needs_decision")), {})
                out.append(base | {"kind": "objective", "evidence_id": f"objective:{newest.get('run_id')}",
                                   "title": f"{name} is below its success objective in {env.title()}",
                                   "text": f"{res['ok']} of the last {res['counted']} runs succeeded ({res['rate']:g} %); "
                                           f"the objective is {res['expected']}.",
                                   "evidence": {"source": "run log", **res}})
    return out


def promise_signal(name: str, promised: list[dict[str, Any]], runs: list[dict[str, Any]], env: str,
                   now: dt.datetime, skip_late: bool = False) -> dict[str, Any] | None:
    """One "promise" signal for an entity whose data products broke their promise to readers in
    ``env``: late against the product's freshness, or more rows rejected (quarantined) by its newest
    load than the product allows. ``skip_late`` leaves out lateness the entity's own freshness
    promise already reports, so one cause opens one incident. None when every promise holds."""
    reasons: list[str] = []
    evidence: list[str] = []
    products: list[str] = []
    for p in promised:
        if env not in (p.get("envs") or []):
            continue
        said = []
        if (p.get("within_hours") or p.get("by")) and not skip_late:
            f = freshness_mod.status({"within_hours": p.get("within_hours"), "by": p.get("by")}, runs, now)
            if f["status"] == "late":
                said += f["reasons"]
                evidence.append(f"late:{f.get('last_ok')}")
        limit = p.get("max_rejected_pct")
        load = next((r for r in runs if r.get("status") in ("ok", "needs_decision")), None)
        if limit is not None and load and int(load.get("rows_read") or 0) > 0:
            pct = 100.0 * int(load.get("quarantined") or 0) / int(load["rows_read"])
            if pct > float(limit):
                said.append(f"{pct:.1f} % of rows rejected in the newest load, promised under {float(limit):g} %")
                evidence.append(f"rejected:{load.get('run_id')}")
        if said:
            products.append(p["product"])
            reasons += [f"{p['product']}: {r}" for r in said]
    if not reasons:
        return None
    return {"kind": "promise", "evidence_id": "|".join(sorted(set(evidence))),
            "title": f"{', '.join(products)} broke {'its' if len(products) == 1 else 'their'} promise in {env.title()} ({name})",
            "text": "; ".join(reasons) + ". Readers of the product rely on it.",
            "evidence": {"source": "product promise", "products": products, "reasons": reasons}}


def agent_signals(records: list[dict[str, Any]], env: str, now: dt.datetime | None = None,
                  me: str = "") -> list[dict[str, Any]]:
    """An incident for each agent serving ``env`` whose status record (``agents/<name>.json``) went
    past its ``stale_after`` without saying it stopped. ``me`` (the evaluating agent) is skipped:
    an agent can't report itself missing."""
    now = now or dt.datetime.now().astimezone()
    out = []
    for r in records:
        name = str(r.get("agent") or "")
        if not name or name == me or r.get("state") == "stopped":
            continue
        try:
            stale = dt.datetime.fromisoformat(str(r.get("stale_after")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if stale.tzinfo is None or now < stale:
            continue
        schedules = [s.get("id") for s in r.get("schedules") or [] if s.get("id")]
        out.append({"kind": "agent_offline", "workload": f"agent:{name}", "name": name, "owner": "", "team": "",
                    "criticality": "", "evidence_id": f"heartbeat:{r.get('heartbeat_at')}",
                    "title": f"Agent {name} stopped reporting in {env.title()}",
                    "text": f"Its last heartbeat was {_utc(r.get('heartbeat_at'))}; it was due again by {_utc(r.get('stale_after'))}."
                            + (f" Its {env.title()} schedules ({', '.join(schedules)}) don't run while it is down."
                               if schedules else ""),
                    "evidence": {"source": f"agents/{name}.json", "host": r.get("host"),
                                 "heartbeat_at": r.get("heartbeat_at"), "stale_after": r.get("stale_after"),
                                 "version": r.get("version")}})
    return out


def _utc(text: Any) -> str:
    """An ISO time as "2026-10-17 07:40 UTC" for messages people read; anything else as it is."""
    try:
        t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return str(text)
    if t.tzinfo is None:
        return str(text)
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def unreadable(env: str, exc: BaseException) -> tuple[list[dict[str, Any]], str]:
    """(signals, not set up message) for an environment whose run log couldn't be read. One that
    isn't set up (no workspace assigned) is a choice, not a problem: it opens no incident and clears
    a "Can't check" one; its workload incidents stay as they are because nothing was read."""
    state, message = model.classify_error(exc)
    if state == "not_set_up":
        return [], message
    return [cant_evaluate(env, state, message)], ""


def cant_evaluate(env: str, state: str, message: str) -> dict[str, Any]:
    """The signal for an environment that couldn't be read: a problem with reading, not with a workload."""
    return {"kind": "cant_evaluate", "workload": None, "name": "", "evidence_id": state,
            "title": f"Can't check {env.title()}: {model.OBS_LABELS.get(state, state).lower()}",
            "text": f"{message} Nothing is known about its workloads until it can be read again.",
            "evidence": {"source": "evaluation", "state": state}}


def _run_evidence(r: dict[str, Any]) -> dict[str, Any]:
    return {"source": "run log", "run_id": r.get("run_id"), "started_at": r.get("started_at"),
            "ran_on": r.get("ran_on") or "", "job_id": r.get("job_id") or ""}


def describe(change: dict[str, Any]) -> str:
    """One line for a Teams message about one change."""
    inc = change["incident"]
    label = KIND_LABELS.get(inc["kind"], inc["kind"])
    if change["change"] == "recovered":
        how = "cleared" if inc.get("resolution") == "cleared" else "recovered"
        return f"**{inc.get('name') or inc['env'].title()}** {how}: {label.lower()} in {inc['env'].title()} is over."
    if change["change"] == "escalated":
        return (f"Not acknowledged, escalated to {inc.get('escalated_to') or 'on-call'}: **{inc['title']}**. "
                f"{inc.get('text') or ''}").strip()
    first = "again: " if change["change"] == "reopened" else ""
    owner = f" Owner: {inc['owner']}." if inc.get("owner") else ""
    return f"{first}**{inc['title']}**. {inc.get('text') or ''}{owner}".strip()
