"""Health and readiness: rule-based findings, each with the evidence it rests on.

Health says how the workload's runs are going where it is deployed. Readiness says whether it
can run now. A source that couldn't be read gives an "unknown" finding about the source, never
a failure of the workload.
"""

from __future__ import annotations

from typing import Any

from .model import HEALTH, HEALTH_LABELS

# Finding levels, worst first. "notice" and "ok" don't change a workload's health.
LEVELS = ("failing", "late", "attention", "unknown", "notice", "ok")
_GOOD_OBS = ("live", "cached")


def _where(dep: dict[str, Any]) -> str:
    return dep.get("env_label") or dep["location"]["label"]


def findings(w: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(level: str, kind: str, title: str, text: str = "", evidence: dict[str, Any] | None = None,
            next_actions: list[dict[str, Any]] | None = None, env: str | None = None) -> None:
        out.append({"level": level, "kind": kind, "title": title, "text": text, "evidence": evidence or {},
                    "next": next_actions or [], "env": env})

    if w.get("errors"):
        add("attention", "definition", "The definition has problems", "; ".join(w["errors"][:3]),
            {"source": "project validation"}, [{"action": "open_definition", "label": "Open"}])
    for dep in w.get("deployments") or []:
        where, env = _where(dep), dep.get("env")
        obs = dep.get("observation") or {}
        run = dep.get("latest_run")
        if obs.get("state") not in _GOOD_OBS and not run:
            add("unknown", "telemetry", f"{where} not observed",
                (obs.get("detail") or obs.get("label") or "") + " This says nothing about the workload itself.",
                {"source": obs.get("source"), "state": obs.get("state"), "collected_at": obs.get("collected_at")},
                [{"action": "sign_in", "label": "Sign in"}] if obs.get("state") == "signed_out" else [], env)
        elif obs.get("state") == "stale":
            add("unknown", "telemetry", f"{where}: information is out of date",
                f"Last read {obs.get('collected_at')}. Refresh to read it again.",
                {"source": obs.get("source"), "state": "stale", "collected_at": obs.get("collected_at")},
                [{"action": "refresh", "label": "Refresh"}], env)
        if run:
            ev = {"source": run.get("source"), "run_id": run.get("id"), "started_at": run.get("started_at"),
                  "ran_on": run.get("ran_on"), "message": run.get("message"), "open": run.get("open")}
            retry = [{"action": a, "label": "Retry"} for a in dep.get("actions") or []
                     if a in ("run", "run_item", "run_in_fabric", "run_schedule")][:1]
            if run["state"] == "failed":
                add("failing", "run", f"Last run in {where} failed", run.get("message") or "", ev,
                    [{"action": "open_run", "label": "Open run"}] + retry, env)
            elif run["state"] == "needs_decision":
                add("attention", "run", f"Last run in {where} needs a decision",
                    run.get("message") or "Schema drift or quarantined rows are waiting for review.", ev,
                    [{"action": "open_run", "label": "Open run"}], env)
            elif run["state"] == "cancelled":
                add("attention", "run", f"Last run in {where} was cancelled", run.get("message") or "", ev, [], env)
            elif run["state"] == "running":
                add("notice", "run", f"Running in {where}", "", ev, [], env)
            elif run["state"] == "unknown":
                add("unknown", "run", f"Last run in {where} has a status Fabric Desktop by KratosBI doesn't know",
                    str(run.get("native_status") or ""), ev, [], env)
        f = dep.get("freshness")
        if f and f.get("status") == "late":
            add("late", "freshness", f"Late in {where}", "; ".join(f.get("reasons") or []),
                {"source": "freshness promise", "expected": f.get("expected"), "last_ok": f.get("last_ok")}, [], env)
        elif f and f.get("status") == "never" and obs.get("state") in _GOOD_OBS:
            add("attention", "freshness", f"Never loaded in {where}", f"Promised: {f.get('expected')}",
                {"source": "freshness promise"}, [], env)
        v = dep.get("version") or {}
        if v.get("behind"):
            add("notice", "release", f"{where} runs an older version",
                f"{where} has version {str(v.get('deployed'))[:8]} (promoted {str(v.get('at') or 'earlier')[:10]}); "
                f"the project is at {str(v.get('current'))[:8]}.",
                {"source": "meta.deployments", "deployed": v.get("deployed"), "current": v.get("current")},
                [{"action": "promote", "label": "Compare and promote"}], env)
        link = dep.get("link")
        if link and link.get("how") == "name match":
            add("notice", "identity", "Linked to its Fabric item by name only",
                "Publish or run it in Fabric from this app once, or link it by hand, so a rename can't break the link.",
                {"source": "name match", "item_id": link.get("item_id")}, [{"action": "link", "label": "Link"}])
    owners = w.get("owners") or {}
    if not any(o.get("value") and not o.get("placeholder") for o in owners.values()):
        placeholder = next((o["value"] for o in owners.values() if o.get("placeholder")), "")
        add("notice", "ownership", "No owner" if not placeholder else f"Owner is a placeholder ({placeholder})",
            "Nobody is accountable for it, so nobody is told when it fails.", {},
            [{"action": "assign", "label": "Assign"}])
    order = {lvl: i for i, lvl in enumerate(LEVELS)}
    out.sort(key=lambda f: order[f["level"]])
    return out


def assess(w: dict[str, Any]) -> dict[str, Any]:
    """Health, readiness and findings for one workload record."""
    fs = findings(w)
    levels = {f["level"] for f in fs}
    deps = w.get("deployments") or []
    observed = any((d.get("observation") or {}).get("state") in _GOOD_OBS or d.get("latest_run") for d in deps)
    ran = any(d.get("latest_run") for d in deps)
    if "failing" in levels:
        health = "failing"
    elif "late" in levels:
        health = "late"
    elif "attention" in levels and any(f["kind"] in ("run", "freshness") for f in fs if f["level"] == "attention"):
        health = "attention"
    elif not observed:
        health = "unknown"
    elif not ran:
        health = "idle"
    else:
        health = "healthy"
    if w.get("errors"):
        readiness = {"state": "blocked", "label": "Can't run until the definition is fixed"}
    elif not deps:
        readiness = {"state": "unknown", "label": "Not deployed anywhere"}
    else:
        readiness = {"state": "ready", "label": "Ready"}
    gaps = [f for f in fs if f["kind"] == "ownership"]
    return {"health": health, "health_label": HEALTH_LABELS[health], "health_rank": HEALTH.index(health),
            "readiness": readiness, "findings": fs, "unowned": bool(gaps),
            "partial": observed and any((d.get("observation") or {}).get("state") not in _GOOD_OBS
                                        and not d.get("latest_run") for d in deps)}
