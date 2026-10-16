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
from .incidents import KIND_LABELS


def signals(project_name: str, entities: dict[str, dict[str, Any]], runs: list[dict[str, Any]], env: str,
            now: dt.datetime | None = None, criticality: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Problems with the entities of ``project_name`` in ``env``. ``entities`` maps a name to its spec
    (the entity file); ``runs`` are that environment's run log rows, newest first."""
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
        base = {"workload": wid, "name": name, "owner": owner}
        if latest and latest.get("status") == "failed":
            out.append(base | {"kind": "run_failed", "evidence_id": latest.get("run_id"),
                               "title": f"{name} failed in {env.title()}",
                               "text": latest.get("message") or "The run failed.",
                               "evidence": _run_evidence(latest)})
        elif latest and latest.get("status") == "needs_decision":
            out.append(base | {"kind": "needs_decision", "evidence_id": latest.get("run_id"),
                               "title": f"{name} needs a decision in {env.title()}",
                               "text": latest.get("message") or "Schema drift or quarantined rows are waiting for review.",
                               "evidence": _run_evidence(latest)})
        p = freshness_mod.promise(spec)
        if p and env in p["envs"]:
            f = freshness_mod.status(p, mine, now)
            if f["status"] == "late":
                out.append(base | {"kind": "late", "evidence_id": f"late:{f.get('last_ok')}",
                                   "title": f"{name} is late in {env.title()}", "text": "; ".join(f["reasons"]),
                                   "evidence": {"source": "freshness promise", "expected": f["expected"],
                                                "last_ok": f["last_ok"], "due_at": f["due_at"]}})
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
    first = "again: " if change["change"] == "reopened" else ""
    owner = f" Owner: {inc['owner']}." if inc.get("owner") else ""
    return f"{first}**{inc['title']}**. {inc.get('text') or ''}{owner}".strip()
