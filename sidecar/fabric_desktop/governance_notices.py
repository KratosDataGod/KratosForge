"""Platform notices from governance: the events requests, shortcut checks and deprecations send to Teams.

Builds ``platform_notify`` events (workloads/platform_notify.py has the shape) for a request that
starts waiting, is decided or is withdrawn, for a shortcut a health check finds broken or
fixed, for a data product published as deprecated, to the projects that read it, and for a policy exception
about to end, to whoever asked and the place it covers. Only names and where to open it in the app go into an event: a request's reason and
detail stay in the app, and people are named by UPN or by role.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any

from .redact import secrets

_UPN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ROLE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_KIND = {"key_table": "New key table", "source": "New source", "access": "Access to", "shortcut": "New shortcut",
         "workspace": "New workspace", "workspace_access": "Workspace access to", "domain": "New domain",
         "exception": "Exception to", "data": "Data request to"}
_DECIDED = {"approved": "approved", "declined": "declined"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _upns(values: Any) -> list[str]:
    return sorted({v.strip().lower() for v in re.split(r"[,;]", str(values or "")) if _UPN.match(v.strip())})


def _title(req: dict[str, Any]) -> str:
    return secrets(f"{_KIND.get(req.get('kind'), 'Request')} {req.get('target') or ''}".strip())


def _product(req: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any]:
    """The data product a request is about: access to it, or a shortcut pointing at it."""
    d = req.get("detail") or {}
    named = req.get("kind") == "access" or (req.get("kind") == "data" and d.get("what") == "change")
    pid = req.get("target") if named else d.get("product_id")
    return next((p for p in products if pid and p.get("id") == pid), {})


def _deciders(req: dict[str, Any], product: dict[str, Any]) -> list[str]:
    """Who decides it besides platform admins: the product's owners, or for a new data product the
    domain's owners as the request recorded them."""
    if req.get("kind") == "data" and (req.get("detail") or {}).get("what") == "new":
        return _upns((req.get("detail") or {}).get("owners"))
    return _upns(product.get("owner"))


def _domain(req: dict[str, Any], product: dict[str, Any]) -> str:
    """The domain a request is in: its product's, the new workspace's, the new subdomain's parent, or
    the domain an exception is for."""
    d = req.get("detail") or {}
    kind = req.get("kind")
    if kind == "workspace":
        return str(d.get("domain_id") or "")
    if kind == "domain":
        return str(d.get("parent_id") or req.get("target") or "")
    if kind == "exception" and d.get("scope") == "domain":
        return str(d.get("scope_target") or "")
    if kind == "data":
        return str(d.get("domain_id") or product.get("domain_id") or "")
    return product.get("domain_id") or ""


def request_waiting(req: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any]:
    """A new request, for the people who can decide it: the product's owners and platform admins."""
    product = _product(req, products)
    return {"kind": "request.waiting", "id": req["id"], "title": _title(req), "at": req.get("requested_at") or _now(),
            "domain": _domain(req, product),
            "to": {"people": _deciders(req, product), "roles": ["platform_admin"]},
            "open": {"view": "platform", "tab": "requests", "target": req["id"]}}


def _left(req: dict[str, Any]) -> str:
    """What a declined or withdrawn request's approval already made and left in Fabric, by kind only, so
    whoever can remove it hears of it (#1261). The app's decline says it in full (left_in_fabric in
    central/requests_service.py); a notice carries no names beyond the request's own target."""
    if req.get("status") not in ("declined", "withdrawn"):
        return ""
    made, kind = (req.get("detail") or {}).get("made") or {}, req.get("kind")
    if kind == "workspace" and made.get("workspace_id"):
        return "; the workspace it made is still in Fabric"
    if kind == "workspace_access" and made.get("assigned") and made.get("change") != "kept":
        if made.get("change") in ("gave", "raised"):
            return f"; the role it {made['change']} stays in the workspace"
        return "; the role may be from it: check before removing"
    if kind == "source" and made.get("copy_job_id"):
        return "; its Copy job is still in the landing workspace" + (", on its schedule" if made.get("schedule_id") else "")
    if kind == "domain" and made.get("domain_id"):
        return "; the domain may be from it: check before deleting" if made.get("found") \
            else "; the domain it made is still there"
    if kind == "shortcut" and made.get("shortcut_id"):
        return "; the shortcut may be from it: check before removing" if made.get("found") \
            else "; the shortcut it made is still in OneLake"
    return ""


TITLE_MAX = 200  # platform_notify.validate cuts a title to this


def _fit(head: str, tail: str) -> str:
    """``head`` then ``tail`` in TITLE_MAX characters: a long target is cut, never what follows it (#1269)."""
    if len(head) + len(tail) <= TITLE_MAX:
        return head + tail
    return head[:max(TITLE_MAX - len(tail) - 1, 0)] + "…" + tail


def request_closed(req: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A decided or withdrawn request, for whoever asked (decided) or decides (withdrawn). One whose
    approval failed partway says, in its title, that what it made is still there."""
    status = req.get("status")
    product = _product(req, products)
    base = {"id": req["id"], "at": req.get("decided_at") or _now(), "domain": _domain(req, product),
            "open": {"view": "platform", "tab": "requests", "target": req["id"]}}
    if status in _DECIDED:
        asker = _upns(req.get("requested_by"))
        return base | {"kind": "request.decided", "title": _fit(_title(req), f" ({_DECIDED[status]}){_left(req)}"),
                       "to": {"people": asker, "roles": [] if asker else ["requester"]}}
    if status == "withdrawn":
        return base | {"kind": "request.withdrawn", "title": _fit(_title(req), _left(req)),
                       "to": {"people": _deciders(req, product), "roles": ["platform_admin"]}}
    return None


def product_changed(product: dict[str, Any], changes: list[str], followers: list[str], env: str) -> dict[str, Any] | None:
    """A publish that changed a product, for its followers and the projects that link to it. Its
    changes are named (versions and column names), as they are what readers act on."""
    people = _upns(";".join(followers or []))
    if not changes or not people:
        return None
    at = _now()
    return {"kind": "product.changed", "id": product["id"], "title": secrets(str(product.get("name") or product["id"])),
            "at": at, "detail": secrets("; ".join(changes)), "domain": str(product.get("domain_id") or ""), "env": env,
            "dedupe": f"product.changed:{product['id']}:{env}:{at}",
            "to": {"people": people, "roles": []}, "open": {"view": "onelake", "tab": "products", "target": product["id"]}}


def shortcut_events(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A health check's changes ("broke", "fixed") as events for platform admins. The dedupe key is
    the shortcut, so one that stays broken is posted once and its fix closes that break."""
    out = []
    for c in changes:
        kind = {"broke": "shortcut.broken", "fixed": "shortcut.fixed"}.get(c.get("kind"))
        if not kind or not c.get("shortcut_id"):
            continue
        where = "/".join(x for x in (c.get("workspace_name"), c.get("lakehouse_name")) if x)
        out.append({"kind": kind, "id": c["shortcut_id"], "title": secrets(f"{c.get('name') or 'shortcut'}" + (f" in {where}" if where else "")),
                    "at": c.get("at") or _now(), "detail": secrets(c.get("note") or ""),
                    "severity": "warn" if kind == "shortcut.broken" else "info",
                    "to": {"people": [], "roles": ["platform_admin"]},
                    "open": {"view": "shortcuts", "tab": "health", "target": c["shortcut_id"]}})
    return out


def product_deprecated(product: dict[str, Any], consumers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A product published as deprecated, for the projects that link to it: the people who saved each
    link, by UPN, and each project's team by role. None when nobody else reads it. The dedupe key
    carries the date, so a changed date is news again and the same one is posted once."""
    pid, stops = str(product.get("id") or ""), str(product.get("deprecated_on") or "").strip()
    readers = [c for c in consumers if c.get("consumer_project") and c["consumer_project"] != product.get("project")]
    if not pid or not readers:
        return None
    projects = sorted({c["consumer_project"] for c in readers})
    links = sorted(f"{c['consumer_project']} ({c.get('alias') or '?'})" for c in readers)
    return {"kind": "product.deprecated", "id": pid, "title": secrets(f"{pid} stops on {stops}" if stops else pid),
            "at": _now(), "domain": product.get("domain_id") or product.get("domain") or "", "severity": "warn",
            "detail": secrets(f"Read by {', '.join(links)}. Move these links to what replaces it before then; "
                              "after that date their promotes stop."),
            "to": {"people": sorted({u for c in readers for u in _upns(c.get("updated_by"))}),
                   "roles": [f"project:{p}" for p in projects if _ROLE_NAME.match(p)] or ["platform_admin"]},
            "open": {"view": "metadata", "tab": "links", "target": pid},
            "dedupe": f"product.deprecated:{pid}:{stops}"}


def incident_impact(inc: dict[str, Any], found: dict[str, Any]) -> dict[str, Any]:
    """An incident's reach (workloads/impact.py) for the people downstream of it: product owners,
    the people who linked a project to one, and followers. Posted once per incident and set of
    people, so telling again only posts when someone new is reached."""
    upns = sorted(p["upn"] for p in found["people"])
    products = ", ".join(p["name"] for p in found["products"])
    domains = {p["domain_id"] for p in found["products"] if p.get("domain_id")}
    return {"kind": "incident.impact", "id": inc["id"], "title": secrets(str(inc.get("title") or inc.get("name") or "Incident")),
            "at": _now(), "env": str(inc.get("env") or ""), "severity": "warn",
            "domain": next(iter(domains)) if len(domains) == 1 else "",
            "detail": secrets(f"Data products affected: {products}." if products else ""),
            "to": {"people": upns, "roles": []},
            "open": {"view": "incident", "tab": "detail", "target": inc["id"]},
            "dedupe": f"incident.impact:{inc['id']}:{hashlib.sha1('|'.join(upns).encode()).hexdigest()[:12]}"}


def exception_ending(exc: dict[str, Any], days_left: int) -> dict[str, Any] | None:
    """A policy exception about to end, for whoever asked for it and the place it covers: the project's
    team, or the domain's owners. Like a request, its reason stays in the app. The dedupe key carries
    the end date, so it's posted once per exception and end date."""
    eid, pid, until = str(exc.get("id") or ""), str(exc.get("policy_id") or ""), str(exc.get("until") or "")
    if not eid or not pid or not until:
        return None
    scope, target = str(exc.get("scope") or ""), str(exc.get("target") or "")
    roles = []
    if scope == "project" and _ROLE_NAME.match(target):
        roles.append(f"project:{target}")
    elif scope == "domain" and _ROLE_NAME.match(target):
        roles.append(f"domain_owner:{target}")
    asker = _upns(exc.get("requested_by"))
    when = "today" if days_left <= 0 else "tomorrow" if days_left == 1 else f"in {days_left} days"
    return {"kind": "exception.ending", "id": eid, "title": secrets(f"Exception to {pid} for {target or scope} ends {when}"),
            "at": _now(), "domain": target if scope == "domain" else "", "severity": "warn",
            "detail": secrets(f"It ends on {until}. After that the policy applies again: meet it, or ask for a new exception."),
            "to": {"people": asker, "roles": roles or ([] if asker else ["requester"])},
            "open": {"view": "governance", "tab": "exceptions", "target": pid},
            "dedupe": f"exception.ending:{eid}:{until}"}
