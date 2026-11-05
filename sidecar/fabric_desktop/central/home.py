"""Data mesh > Home: the mesh as each of its three people sees it.

- **My team**: a team lead's products and what each needs, requests waiting on them, their own asks,
  and the data their project depends on.
- **On call**: incidents that reach data products and who to wake, late landing sources, broken
  shortcuts and owner gaps.
- **Organization**: the CDO's scorecard, per domain.

Pure: the service reads the catalog, the incident lists and the landing zone, and these turn them
into what each view shows. Nothing here reads or writes anything itself.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from ..workloads import impact

VIEWS = ("team", "oncall", "org")
STALE_DAYS = 5  # a request waiting longer than this is a decision a leader should see


def _owners(value: Any) -> list[str]:
    return [x.strip().lower() for x in re.split(r"[,;]", str(value or "")) if x.strip()]


def _days(since: Any, now: dt.datetime) -> int | None:
    try:
        return (now - dt.datetime.fromisoformat(str(since))).days
    except (TypeError, ValueError):
        return None


def _published(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in products if p.get("status") != "draft"]


def incident_reach(products: list[dict[str, Any]], incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Active incidents about a metadata table, each with the products it reaches and their owners
    (workloads/impact.py). Incidents that reach no product are left out."""
    out = []
    for inc in incidents:
        where = impact.workload_entity(inc.get("workload"))
        if where is None:
            continue
        found = impact.blast_radius(products, *where, str(inc.get("env") or ""))
        if not found["products"]:
            continue
        readers = sorted({c for p in found["products"] for c in p["consumers"]})
        out.append({"id": inc["id"], "env": inc.get("env"), "title": inc.get("title") or inc.get("name"),
                    "state": inc.get("state"), "priority": inc.get("priority") or "", "owner": inc.get("owner") or "",
                    "opened_at": inc.get("opened_at") or "", "number": inc.get("number") or "",
                    "products": [{"id": p["id"], "name": p["name"], "owners": _owners(p["owner"])} for p in found["products"]],
                    "readers": readers, "people": len(found["people"])})
    out.sort(key=lambda i: (i["state"] != "open", i["priority"] or "P9", i["opened_at"]))
    return out


def team(me: str, project: dict[str, Any] | None, products: list[dict[str, Any]], requests: list[dict[str, Any]],
         follows: list[str] | None, reviews: dict[str, Any] | None, reach: list[dict[str, Any]]) -> dict[str, Any]:
    """My team: the products I own or my project publishes, what each needs, requests waiting on me,
    my own requests and the products my project links to or I follow."""
    me = (me or "").lower()
    mine_project = (project or {}).get("name") or ""
    due = {g["product_id"] for g in (reviews or {}).get("grants") or [] if g.get("due")}
    hit = {p["id"]: i for i in reach for p in i["products"]}
    own = []
    for p in _published(products):
        owners = _owners(p.get("owner"))
        if not ((me and me in owners) or (mine_project and p.get("project") == mine_project)):
            continue
        needs = []
        if p["id"] in hit:
            needs.append("Incident upstream")
        if not owners:
            needs.append("No owner")
        elif len(owners) == 1:
            needs.append("No second owner")
        if p["id"] in due:
            needs.append("Access review due")
        if p.get("status") == "deprecated":
            needs.append(f"Deprecated, stops {p.get('deprecated_on') or 'soon'}")
        own.append({"id": p["id"], "name": p.get("name") or p["id"], "version": p.get("version") or "",
                    "status": p.get("status"), "owners": owners,
                    "readers": sorted({c["consumer_project"] for c in p.get("consumers") or []}), "needs": needs,
                    "incident": hit[p["id"]]["id"] if p["id"] in hit else None,
                    "incident_env": hit[p["id"]]["env"] if p["id"] in hit else None})
    linked = {c["product_id"] for p in products for c in p.get("consumers") or []
              if mine_project and c.get("consumer_project") == mine_project}
    followed = set(follows or [])
    by_id = {p["id"]: p for p in products}
    depends = []
    for pid in sorted(linked | followed):
        p = by_id.get(pid)
        if not p or (mine_project and p.get("project") == mine_project):
            continue
        state = (f"Deprecated, stops {p.get('deprecated_on') or 'soon'}" if p.get("status") == "deprecated"
                 else f"Version {p.get('version') or '1'}")
        depends.append({"id": pid, "name": p.get("name") or pid, "project": p.get("project"), "state": state,
                        "how": "linked" if pid in linked else "following",
                        "incident": hit[pid]["id"] if pid in hit else None})
    waiting = [r for r in requests if r.get("status") == "waiting" and r.get("can_decide")]
    asks = sorted([r for r in requests if r.get("mine")], key=lambda r: str(r.get("requested_at") or ""), reverse=True)
    return {"project": project, "products": own, "waiting": waiting, "asks": asks[:10],
            "depends": depends, "follows_known": follows is not None,
            "reviews_due": len([g for g in (reviews or {}).get("grants") or [] if g.get("due")]),
            "reviews_known": reviews is not None}


def oncall(reach: list[dict[str, Any]], landing: dict[str, Any] | None, shortcuts: list[dict[str, Any]] | None,
           coverage: dict[str, Any] | None) -> dict[str, Any]:
    """On call: incidents that reach data products and who owns each product, late landing sources,
    broken shortcuts and owner gaps. A source that couldn't be read is None, and the view says so."""
    wake: dict[str, list[str]] = {}
    for i in reach:
        if i["state"] == "resolved":
            continue
        for p in i["products"]:
            wake.setdefault(p["name"], p["owners"])
    late = [s for s in (landing or {}).get("sources") or [] if s.get("late")]
    broken = [s for s in shortcuts or [] if s.get("health") == "broken"]
    gaps = (coverage or {}).get("gaps") or []
    return {"incidents": reach, "wake": [{"product": k, "owners": v} for k, v in sorted(wake.items())],
            "late": late if landing is not None else None, "landing_problem": (landing or {}).get("problem"),
            "broken": broken if shortcuts is not None else None,
            "owner_gaps": {"no_owner": sum("No owner" in g["problems"] for g in gaps), "total": len(gaps)}
            if coverage is not None and coverage.get("available", True) else None}


def org(domains: list[dict[str, Any]], products: list[dict[str, Any]], requests: list[dict[str, Any]],
        reviews: dict[str, Any] | None, reach: list[dict[str, Any]], now: dt.datetime) -> dict[str, Any]:
    """Organization: totals and one row per top-level domain. Every number counts something in the
    catalog or an incident list; nothing is estimated. Subdomains count toward their parent."""
    parent = {d["id"]: d.get("parent_id") or d["id"] for d in domains}
    published = _published(products)
    due = [g for g in (reviews or {}).get("grants") or [] if g.get("due")]
    product_domain = {p["id"]: parent.get(p.get("domain_id") or "", p.get("domain_id") or "") for p in products}
    incident_products = {p["id"] for i in reach if i["state"] != "resolved" for p in i["products"]}
    waiting = [r for r in requests if r.get("status") == "waiting"]
    stale = sorted([dict(r, days=_days(r.get("requested_at"), now)) for r in waiting
                    if (_days(r.get("requested_at"), now) or 0) > STALE_DAYS], key=lambda r: -(r["days"] or 0))
    rows = []
    for d in sorted((d for d in domains if not d.get("parent_id")), key=lambda d: str(d.get("name") or d["id"]).lower()):
        mine = [p for p in published if product_domain.get(p["id"]) == d["id"]]
        two = sum(len(_owners(p.get("owner"))) >= 2 for p in mine)
        reviews_due = sum(1 for g in due if product_domain.get(g["product_id"]) == d["id"])
        incidents = sum(1 for p in mine if p["id"] in incident_products)
        readers = {c["consumer_project"] for p in mine for c in p.get("consumers") or []}
        needs = []
        if incidents:
            needs.append(f"{incidents} product{'s' if incidents != 1 else ''} with an incident upstream")
        if len(mine) - two:
            needs.append(f"{len(mine) - two} without a second owner")
        if reviews_due:
            needs.append(f"{reviews_due} access review{'s' if reviews_due != 1 else ''} due")
        rows.append({"id": d["id"], "name": d.get("name") or d["id"], "products": len(mine), "readers": len(readers),
                     "two_owners": two, "reviews_due": reviews_due if reviews is not None else None,
                     "incidents": incidents, "needs": needs})
    return {"totals": {"domains": len(rows), "products": len(published),
                       "two_owners": sum(len(_owners(p.get("owner"))) >= 2 for p in published),
                       "reviews_due": len(due) if reviews is not None else None,
                       "grants": len((reviews or {}).get("grants") or []) if reviews is not None else None,
                       "waiting": len(waiting), "stale": len(stale)},
            "domains": rows, "stale": stale[:5], "stale_days": STALE_DAYS}
