"""Which organization (Microsoft Entra tenant) sign-in goes to: a guest's host company, or their own.

A guest (B2B) account signs in to its home organization through the ``organizations`` authority, so
Fabric shows the home organization's workspaces. To work in the company that invited them, the person
names it (Settings > Account > Switch organization, or "Guest of another company?" on the sign-in
prompt), and sign-in then goes to that tenant's own authority. The account stays the same.

``lookup`` checks a domain or tenant id before anyone signs in, with Microsoft's public sign-in
metadata for that tenant (``/.well-known/openid-configuration``): no token, no app registration, and
nothing about the person is sent. The organizations used recently are kept on this PC only
(``organizations.json`` in the app's data folder): their tenant id, the domain typed, and when.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import requests

from . import http_session, jsonfile
from .retrying import send_with_retry
from .redact import describe

_log = logging.getLogger(__name__)

LOGIN = "https://login.microsoftonline.com"
# Personal Microsoft accounts' tenant: not an organization, and Fabric doesn't sign them in.
CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
RECENT_FILE = "organizations.json"
RECENT_MAX = 5
LOOKUP_TIMEOUT = 15
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ISSUER = re.compile(r"https://[^/]+/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/")
_cache: dict[str, dict[str, str]] = {}
_cache_lock = threading.Lock()


def is_tenant_id(text: str) -> bool:
    return bool(_GUID.fullmatch(text))


def normalize(text: Any) -> str:
    """A domain or tenant id as typed, trimmed and lower-cased; ValueError when it is neither.
    Checked before it goes in any address, so nothing else can reach the sign-in URL."""
    from .app_settings import dns_name
    value = str(text or "").strip().lower()
    if value.startswith(("http://", "https://")) or "@" in value:
        # "chris@northwind.com" or a pasted address: the part that names the organization
        value = re.sub(r"^https?://", "", value).split("/", 1)[0].rsplit("@", 1)[-1]
    if not value:
        raise ValueError("Type the organization's domain, like northwind.com, or its tenant id.")
    if not (is_tenant_id(value) or dns_name(value)):
        raise ValueError(f"{value[:80]} isn't a domain like northwind.com or a tenant id like "
                         "11111111-2222-3333-4444-555555555555.")
    if value == CONSUMER_TENANT:
        raise ValueError("That is the tenant of personal Microsoft accounts, not an organization. "
                         "Ask the company that invited you for its domain or tenant id.")
    return value


def label(org: dict[str, Any] | None) -> str:
    """The name shown for an organization: Microsoft gives no display name without signing in, so its
    domain, else its tenant id."""
    if not org:
        return ""
    return str(org.get("name") or org.get("domain") or org.get("tenant_id") or "")


def lookup(text: Any, session: requests.Session | None = None) -> dict[str, str]:
    """The organization at a domain or tenant id: ``{"tenant_id", "domain", "name"}``. ValueError when
    Microsoft has none there or it can't be signed in to here; RuntimeError when Microsoft couldn't be
    reached or answered oddly (the server shows both as their plain message). Kept for this run of the engine."""
    value = normalize(text)
    with _cache_lock:
        if value in _cache:
            return dict(_cache[value])
    url = f"{LOGIN}/{value}/v2.0/.well-known/openid-configuration"
    try:
        http = session or http_session.session()
        # A read: throttling or a 5xx is tried again, briefly, as every other read is (retrying.py).
        response = send_with_retry(lambda: http.get(url, timeout=LOOKUP_TIMEOUT), "GET", attempts=3, wait_cap=5.0)
    except requests.RequestException as exc:
        _log.info("Organization lookup didn't reach Microsoft: %s", describe(exc))
        raise RuntimeError("KratosForge couldn't reach Microsoft to check that organization. "
                              "Check this PC's connection and try again.") from None
    if response.status_code in (400, 404):
        raise ValueError(f"Microsoft has no organization at {value}. Check the spelling, or ask the company "
                       "for its tenant id.")
    if response.status_code != 200:
        raise RuntimeError(f"Microsoft didn't answer the check for {value} (HTTP {response.status_code}). "
                              "Try again in a minute.")
    try:
        data = response.json()
    except ValueError:
        data = {}
    match = _ISSUER.match(str(data.get("issuer") or "") + "/") if isinstance(data, dict) else None
    if not match:
        raise RuntimeError(f"Microsoft's answer for {value} didn't name a tenant. Try again in a minute.")
    tenant_id = match.group(1)
    if tenant_id == CONSUMER_TENANT:
        raise ValueError(f"{value} is for personal Microsoft accounts, not an organization.")
    cloud = str(data.get("cloud_instance_name") or "microsoftonline.com")
    if cloud != "microsoftonline.com":
        raise ValueError(f"{value} is in a separate Microsoft cloud ({cloud}), which KratosForge doesn't sign in to yet.")
    domain = "" if is_tenant_id(value) else value
    org = {"tenant_id": tenant_id, "domain": domain, "name": domain or tenant_id}
    with _cache_lock:
        _cache[value] = dict(org)
        _cache.setdefault(tenant_id, dict(org))
    return org


def tenant_id_of(value: str) -> str | None:
    """The tenant id for the sign-in setting's value: itself when it is one, else the domain's (looked
    up once); None when it can't be looked up now (offline), so sign-in uses the domain as is."""
    if not value:
        return None
    if is_tenant_id(value.lower()):
        return value.lower()
    try:
        return lookup(value)["tenant_id"]
    except (ValueError, RuntimeError) as exc:
        _log.info("Couldn't look up the sign-in organization yet: %s", describe(exc))
        return None


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ---------- recently used, on this PC ----------

def _path(root: Path) -> Path:
    return root / RECENT_FILE


def recent(root: Path) -> list[dict[str, Any]]:
    """The organizations signed in to here, newest first (at most RECENT_MAX)."""
    data = jsonfile.read_json(_path(root), {})
    rows = data.get("recent") if isinstance(data, dict) else None
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not is_tenant_id(str(row.get("tenant_id") or "")):
            continue
        domain = str(row.get("domain") or "")
        try:
            domain = normalize(domain) if domain else ""
        except ValueError:
            domain = ""
        if is_tenant_id(domain):
            domain = ""
        used = row.get("used")
        out.append({"tenant_id": row["tenant_id"], "domain": domain, "name": domain or row["tenant_id"],
                    "used": used if isinstance(used, (int, float)) else 0})
    return out[:RECENT_MAX]


def remember(root: Path, org: dict[str, Any]) -> None:
    """Puts ``org`` first in the recent list. A failed write is logged: the switch itself worked."""
    tenant_id = str(org.get("tenant_id") or "")
    if not is_tenant_id(tenant_id):
        return
    rows = [r for r in recent(root) if r["tenant_id"] != tenant_id]
    domain = str(org.get("domain") or "") or next((r["domain"] for r in recent(root) if r["tenant_id"] == tenant_id), "")
    rows.insert(0, {"tenant_id": tenant_id, "domain": domain, "used": time.time()})
    try:
        jsonfile.write_json(_path(root), {"recent": [{k: r[k] for k in ("tenant_id", "domain", "used")}
                                                     for r in rows[:RECENT_MAX]]})
    except OSError as exc:
        _log.warning("Couldn't save the recent organizations: %s", describe(exc))

