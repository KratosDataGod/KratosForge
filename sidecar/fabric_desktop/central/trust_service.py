"""Trust badges on a data product (Preview): its endorsement in Fabric, and its business terms.

Endorsement (Certified, Promoted, Master data) is set in Fabric, and Fabric shows it through an API
only to Fabric administrators: Power BI's admin tenant scan. Fabric's Get Item doesn't return it. A
platform admin who is also a Fabric administrator chooses Scan now; the scan reads the workspaces the
platform's published products live in, and what it read is kept in the catalog (catalog.endorsement)
so everyone sees the last scan's badge. KratosForge never sets endorsement, and an item no scan has
read shows as unknown.

Business terms are a name and a link to the term in Microsoft Purview, typed by the product's owner
(catalog.product_term). KratosForge's sign-in can't get a Purview token, so nothing here calls Purview.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..auth import POWERBI_TENANT_READ_SCOPES
from ..fabric_api import FabricClient, FabricError
from ..redact import describe
from . import core
from .requests_service import call_or_repair

_log = logging.getLogger(__name__)

# Power BI item lists the tenant scan documents endorsementDetails on, and leaves it out of when the
# item isn't endorsed. On any other list (Fabric items), no endorsementDetails says nothing.
SCAN_ENDORSED_KINDS = ("datasets", "reports", "dataflows", "datamarts")
NOT_ADMIN = ("Only a Fabric administrator can read endorsement: Fabric shows it only through its admin tenant "
             "scan. Ask one who is also a platform admin to choose Scan now; after that, everyone sees the badge here.")


def endorsements_from_scan(workspaces: list[dict[str, Any]], wanted: set[str]) -> dict[str, dict[str, str]]:
    """What a tenant scan says about each wanted item id: {item_id: {endorsement, certified_by}}. Only
    what the scan read: an item it didn't report, or a Fabric item without endorsementDetails, is left out."""
    found: dict[str, dict[str, str]] = {}
    for ws in workspaces or []:
        for kind, items in (ws or {}).items():
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                iid = str(it.get("id") or it.get("objectId") or "").strip().lower()
                if iid not in wanted:
                    continue
                details = it.get("endorsementDetails")
                if isinstance(details, dict) and str(details.get("endorsement") or "").strip():
                    found[iid] = {"endorsement": core.endorsement_label(details["endorsement"]),
                                  "certified_by": str(details.get("certifiedBy") or "").strip()}
                elif kind in SCAN_ENDORSED_KINDS:
                    found[iid] = {"endorsement": "None", "certified_by": ""}
    return found


class TrustMixin:

    def _scanner(self) -> FabricClient:
        """Power BI's admin API, as the signed-in user. Tenant.Read.All is asked for only here."""
        return FabricClient(lambda: self.auth._token(POWERBI_TENANT_READ_SCOPES))  # type: ignore[attr-defined]

    def platform_product_trust(self, product_id: str) -> dict[str, Any]:
        """A product's endorsement as the last tenant scan read it, and its business terms. Only reads."""
        return call_or_repair(self._catalog(), "product_trust",  # type: ignore[attr-defined]
                              "show endorsement and business terms", product_id=product_id)

    def platform_scan_endorsement(self, product_id: str = "") -> dict[str, Any]:
        """Read endorsement with Fabric's admin tenant scan for every published product's items, and keep
        it in the catalog for everyone. Reads the tenant (only those workspaces, at most 100 a call);
        writes catalog.endorsement. A platform admin who is also a Fabric administrator."""
        catalog = self._catalog()  # type: ignore[attr-defined]
        if not catalog.call("whoami").get("admin"):
            raise PermissionError("Only platform admins scan endorsement for everyone. A platform admin who is also "
                                  "a Fabric administrator scans; everyone then sees the last scan's badge.")
        products = [p for p in catalog.call("list_products") if p.get("status") != "draft"]
        items = {i["item_id"]: i for p in products for i in core.product_items(p)}
        workspaces = sorted({i["workspace_id"] for i in items.values() if i["workspace_id"]})
        if not workspaces:
            return {"workspaces": 0, "items": 0, "recorded": 0, "not_reported": [],
                    "message": "No published data product has a port in Fabric yet, so there was nothing to scan."}
        try:
            scanned = self._scanner().admin_scan_workspaces(workspaces)
        except FabricError as exc:
            if getattr(exc, "status", None) in (401, 403) or re.search(r"failed with 40[13]\b", str(exc)):
                _log.info("Tenant scan refused: %s", describe(exc))
                raise PermissionError(NOT_ADMIN) from exc
            raise
        found = endorsements_from_scan(scanned, set(items))
        rows = [{k: items[iid][k] for k in ("item_id", "workspace_id", "item_type", "item_name")} | found[iid]
                for iid in sorted(found)]
        recorded = call_or_repair(catalog, "record_endorsements", "keep endorsement", items=rows) if rows else {"recorded": 0}
        missing = sorted(i["item_name"] or i["item_id"] for iid, i in items.items() if iid not in found)
        out = {"workspaces": len(workspaces), "items": len(items), "recorded": int(recorded.get("recorded") or 0),
               "not_reported": missing}
        if product_id:
            out["trust"] = self.platform_product_trust(product_id)
        return out

    def platform_add_product_term(self, product_id: str, term: str, url: str) -> dict[str, Any]:
        """Put a business term, with its link in Purview, on a product. Writes catalog.product_term."""
        core.purview_url(url)  # refused here too, before anything is sent
        call_or_repair(self._catalog(), "add_product_term", "keep business terms",  # type: ignore[attr-defined]
                       product_id=product_id, term=term, url=url)
        return self.platform_product_trust(product_id)

    def platform_remove_product_term(self, product_id: str, term: str) -> dict[str, Any]:
        """Take a business term off a product (it stays in Purview). Writes catalog.product_term."""
        call_or_repair(self._catalog(), "remove_product_term", "keep business terms",  # type: ignore[attr-defined]
                       product_id=product_id, term=term)
        return self.platform_product_trust(product_id)
