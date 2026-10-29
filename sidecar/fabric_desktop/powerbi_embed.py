"""What Embed Studio (Preview) needs to embed Power BI content with Microsoft's powerbi-client SDK.

Reads only, through the Power BI REST API, as the signed-in person: a report's or dashboard's embed URL,
semantic model, pages and tiles. The design is in docs/architecture/power-bi-embedding.md (PBE-04).

Embed URLs are accepted only on ``app.powerbi.com`` over https: the app's embed host frame may only
frame that host, and the SDK hands the person's token to whatever page the URL loads.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

from .fabric_api import FabricClient, FabricError

POWERBI_API = "https://api.powerbi.com/v1.0/myorg"
EMBED_HOST = "app.powerbi.com"

_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# Fabric's item type -> the kind Embed Studio embeds. Q&A isn't one: Microsoft retires it in December 2026.
KINDS = {"Report": "report", "PaginatedReport": "paginated", "Dashboard": "dashboard"}

# What the SDK supports for each kind (docs/architecture/power-bi-embedding.md, Capability profiles).
CAPABILITIES: dict[str, dict[str, Any]] = {
    "report": {"embed_type": "report", "pages": True, "filters": True, "bookmarks": True, "slicers": True,
               "visuals": True, "events": ["loaded", "rendered", "error", "pageChanged", "dataSelected",
                                           "buttonClicked", "visualRendered", "saved"],
               "limits": ["Export data follows the tenant setting and your permission."]},
    "paginated": {"embed_type": "report", "pages": False, "filters": False, "bookmarks": False, "slicers": False,
                  "visuals": False, "parameters": True, "events": ["loaded", "error"],
                  "limits": ["No bootstrap.", "No multi-value parameters.", "No rdl: parameters.",
                             "Not on real-time (push) semantic models."]},
    "dashboard": {"embed_type": "dashboard", "pages": False, "filters": False, "bookmarks": False, "slicers": False,
                  "visuals": False, "tiles": True, "events": ["loaded", "error", "tileClicked"],
                  "limits": ["No filters or bookmarks."]},
}


def _id(value: str, what: str) -> str:
    """A workspace or item id, which goes into the request path: a GUID and nothing else."""
    value = (value or "").strip()
    if not _GUID.fullmatch(value):
        raise ValueError(f"That isn't a {what} id.")
    return value.lower()


def embed_url(url: Any) -> str:
    """The embed URL Power BI returned, when it is on the one host the embed frame may load."""
    text = str(url or "")
    parts = urlsplit(text)
    if (parts.scheme != "https" or (parts.hostname or "") != EMBED_HOST or parts.port is not None
            or parts.username or parts.password):
        raise ValueError("Power BI returned an embed address KratosForge doesn't open. "
                         f"Embed Studio only opens content on {EMBED_HOST}.")
    return text


def token_expiry(token: str) -> int | None:
    """When the token stops working (its ``exp`` claim, seconds since 1970), read without checking the
    signature: only to tell the screen when to ask for a new one. None when it can't be read."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = claims.get("exp")
        return int(exp) if isinstance(exp, (int, float)) else None
    except (IndexError, ValueError, TypeError, AttributeError):
        return None


def token_reply(token: str, now: float | None = None) -> dict[str, Any]:
    expires = token_expiry(token)
    now = time.time() if now is None else now
    return {"token": token, "token_type": "Aad", "expires_at": expires,
            "seconds_left": max(0, int(expires - now)) if expires else None}


def _not_found(exc: FabricError, what: str) -> Exception:
    if exc.status == 404:
        return LookupError(f"This {what} isn't there any more, or your account can't see it.")
    if exc.status in (401, 403):
        return PermissionError(f"Your account can't open this {what} in Power BI. Ask the workspace's admin for "
                               "access; viewers also need a Pro or Premium Per User licence unless the workspace is "
                               "on F64 or larger capacity.")
    return exc


class EmbedClient:
    """Power BI REST reads for embedding, with a token for the Power BI resource."""

    def __init__(self, client: FabricClient):
        self._client = client

    def _get(self, path: str, what: str) -> dict[str, Any]:
        try:
            body = self._client._request("GET", f"{POWERBI_API}{path}").json()
        except FabricError as exc:
            raise _not_found(exc, what) from exc
        return body if isinstance(body, dict) else {}

    def report(self, workspace_id: str, report_id: str) -> dict[str, Any]:
        ws, rid = _id(workspace_id, "workspace"), _id(report_id, "report")
        body = self._get(f"/groups/{ws}/reports/{rid}", "report")
        paginated = body.get("reportType") == "PaginatedReport"
        out = {"kind": "paginated" if paginated else "report", "id": str(body.get("id") or rid),
               "name": str(body.get("name") or ""), "workspace_id": ws, "embed_url": embed_url(body.get("embedUrl")),
               "dataset_id": str(body.get("datasetId") or ""), "pages": [], "tiles": []}
        if not paginated:
            pages = self._get(f"/groups/{ws}/reports/{rid}/pages", "report").get("value") or []
            out["pages"] = [{"name": str(p.get("name") or ""), "display_name": str(p.get("displayName") or ""),
                             "order": p.get("order")} for p in pages if isinstance(p, dict)]
        return out

    def dashboard(self, workspace_id: str, dashboard_id: str) -> dict[str, Any]:
        ws, did = _id(workspace_id, "workspace"), _id(dashboard_id, "dashboard")
        body = self._get(f"/groups/{ws}/dashboards/{did}", "dashboard")
        tiles = self._get(f"/groups/{ws}/dashboards/{did}/tiles", "dashboard").get("value") or []
        rows = []
        for t in tiles:
            if not isinstance(t, dict):
                continue
            try:
                url = embed_url(t.get("embedUrl"))
            except ValueError:
                continue  # a tile Power BI can't embed (or one elsewhere) is left out, not opened
            rows.append({"id": str(t.get("id") or ""), "title": str(t.get("title") or ""), "embed_url": url,
                         "report_id": str(t.get("reportId") or ""), "dataset_id": str(t.get("datasetId") or "")})
        return {"kind": "dashboard", "id": str(body.get("id") or did), "name": str(body.get("displayName") or ""),
                "workspace_id": ws, "embed_url": embed_url(body.get("embedUrl")), "dataset_id": "",
                "pages": [], "tiles": rows}

    def content(self, workspace_id: str, item_id: str, item_type: str) -> dict[str, Any]:
        kind = KINDS.get(item_type)
        if kind is None:
            raise ValueError(f"Embed Studio opens reports, paginated reports and dashboards, not {item_type or 'this item'}.")
        out = self.dashboard(workspace_id, item_id) if kind == "dashboard" else self.report(workspace_id, item_id)
        return out | {"capabilities": CAPABILITIES[out["kind"]]}
