"""Platform notices from governance: the events requests and shortcut checks send to Teams.

Builds ``platform_notify`` events (workloads/platform_notify.py has the shape) for a request that
starts waiting, is decided or is withdrawn, and for a shortcut a health check finds broken or
fixed. Only names and where to open it in the app go into an event: a request's reason and
detail stay in the app, and people are named by UPN or by role.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .redact import secrets

_UPN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
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


def request_closed(req: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A decided or withdrawn request, for whoever asked (decided) or decides (withdrawn)."""
    status = req.get("status")
    product = _product(req, products)
    base = {"id": req["id"], "at": req.get("decided_at") or _now(), "domain": _domain(req, product),
            "open": {"view": "platform", "tab": "requests", "target": req["id"]}}
    if status in _DECIDED:
        asker = _upns(req.get("requested_by"))
        return base | {"kind": "request.decided", "title": f"{_title(req)} ({_DECIDED[status]})",
                       "to": {"people": asker, "roles": [] if asker else ["requester"]}}
    if status == "withdrawn":
        return base | {"kind": "request.withdrawn", "title": _title(req),
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
