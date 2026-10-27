"""Views, search, grouping and paging over assessed workload records."""

from __future__ import annotations

from typing import Any, Callable

from .model import HEALTH

VIEWS = {
    "attention": "Needs attention",
    "mine": "Mine",
    "unowned": "Unowned",
    "this_pc": "On this PC",
    "fabric": "In Fabric",
    "production": "Production",
    "all": "All",
}
GROUPS = ("project", "owner", "environment", "kind", "none")
_ENV_ORDER = {"prod": 0, "test": 1, "dev": 2}


def is_mine(w: dict[str, Any], user: str) -> bool:
    """Owned by this person, declared or assigned. Never from who ran it last."""
    if not user:
        return False
    me = {user.lower(), user.split("@")[0].lower()}
    return any(str(o.get("value", "")).strip().lower() in me for o in (w.get("owners") or {}).values())


def _view_test(view: str, user: str) -> Callable[[dict[str, Any]], bool]:
    if view == "attention":
        return lambda w: w["health"] in ("failing", "late", "attention")
    if view == "mine":
        return lambda w: is_mine(w, user)
    if view == "unowned":
        return lambda w: w["unowned"]
    if view == "this_pc":
        return lambda w: any(d["location"]["kind"] == "this_pc" for d in w["deployments"])
    if view == "fabric":
        return lambda w: any(d["location"]["kind"] == "fabric" for d in w["deployments"])
    if view == "production":
        return lambda w: any(d.get("env") == "prod" for d in w["deployments"])
    if view == "all":
        return lambda w: True
    raise ValueError(f"view is one of {', '.join(VIEWS)}")


def _haystack(w: dict[str, Any]) -> str:
    owners = " ".join(str(o.get("value", "")) for o in (w.get("owners") or {}).values())
    envs = " ".join(f"{d.get('env') or ''} {d.get('env_label') or ''} {d['location']['label']}" for d in w["deployments"])
    crit = (w.get("criticality") or {}).get("value", "")
    return " ".join([w.get("search", ""), w["kind_label"], w["health_label"], owners, envs, crit,
                     " ".join(w.get("tags") or [])]).lower()


def _group(w: dict[str, Any], by: str) -> tuple[str, str]:
    if by == "project":
        return w["group"]["id"], w["group"]["label"]
    if by == "owner":
        o = w.get("owners") or {}
        for role in ("team", "technical_owner"):
            if o.get(role, {}).get("value") and not o[role].get("placeholder"):
                return f"owner:{o[role]['value'].lower()}", o[role]["value"]
        return "owner:", "No owner"
    if by == "environment":
        envs = sorted((d["env"] for d in w["deployments"] if d.get("env")), key=lambda e: _ENV_ORDER.get(e, 9))
        if envs:
            return f"env:{envs[0]}", {"prod": "Reaches Prod", "test": "Reaches Test", "dev": "Dev only"}[envs[0]]
        kinds = {d["location"]["kind"] for d in w["deployments"]}
        return ("env:fabric", "Fabric only") if kinds == {"fabric"} else ("env:pc", "This PC")
    if by == "kind":
        return f"kind:{w['kind_label']}", w["kind_label"]
    return "all", "All workloads"


def row(w: dict[str, Any]) -> dict[str, Any]:
    """What the list shows for one workload: no run history, no findings text."""
    last = max((d["latest_run"] for d in w["deployments"] if d.get("latest_run")),
               key=lambda r: str(r.get("started_at") or ""), default=None)
    return {
        "id": w["id"], "kind": w["kind"], "kind_label": w["kind_label"], "name": w["name"],
        "preview": bool(w.get("preview")),
        "description": w.get("description", ""), "group": w["group"], "owners": w.get("owners") or {},
        "criticality": w.get("criticality"), "tags": w.get("tags") or [], "health": w["health"],
        "health_label": w["health_label"], "readiness": w["readiness"], "unowned": w["unowned"],
        "partial": w["partial"], "capabilities": w["capabilities"],
        "headline": next((f for f in w["findings"] if f["level"] in ("failing", "late", "attention", "unknown")), None),
        "last_run": last,
        "deployments": [{"env": d.get("env"), "env_label": d.get("env_label"), "location": d["location"],
                         "latest_run": d.get("latest_run"), "observation": d.get("observation"),
                         "actions": d.get("actions") or []} for d in w["deployments"]],
    }


def select(workloads: list[dict[str, Any]], *, view: str = "all", query: str = "", kind: str = "",
           env: str = "", location: str = "", group: str = "project", offset: int = 0, limit: int = 100,
           user: str = "") -> dict[str, Any]:
    if group not in GROUPS:
        raise ValueError(f"group is one of {', '.join(GROUPS)}")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    counts = {v: sum(1 for w in workloads if _view_test(v, user)(w)) for v in VIEWS}
    test = _view_test(view, user)
    terms = [t for t in query.lower().split() if t]
    picked = []
    for w in workloads:
        if not test(w):
            continue
        if kind and w["kind"] != kind:
            continue
        if env and not any(d.get("env") == env for d in w["deployments"]):
            continue
        if location and not any(d["location"]["kind"] == location for d in w["deployments"]):
            continue
        if terms:
            hay = _haystack(w)
            if not all(t in hay for t in terms):
                continue
        picked.append(w)
    keyed = [(_group(w, group), w) for w in picked]
    keyed.sort(key=lambda kw: (kw[0][1].lower() if kw[0][1] != "No owner" else "~", kw[1]["health_rank"],
                               kw[1]["name"].lower()))
    groups: dict[str, dict[str, Any]] = {}
    for (gid, label), w in keyed:
        g = groups.setdefault(gid, {"id": gid, "label": label, "count": 0, "health": {h: 0 for h in HEALTH}})
        g["count"] += 1
        g["health"][w["health"]] += 1
    page = keyed[offset:offset + limit]
    return {"total": len(picked), "offset": offset, "limit": limit, "view": view, "group": group,
            "views": [{"id": v, "label": label, "count": counts[v]} for v, label in VIEWS.items()],
            "groups": list(groups.values()),
            "rows": [row(w) | {"group_key": gid} for (gid, _label), w in page]}
