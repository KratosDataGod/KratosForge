"""Warehouse and SQL analytics endpoint controls (FEA-303), and the evidence they read.

Both decide from item properties only, read with the signed-in person's own access:

- ``sqlendpoint.properties`` (verified configuration): a lakehouse's SQL analytics endpoint and its
  provisioning status, from ``GET /workspaces/{id}/lakehouses/{id}`` (``sqlEndpointProperties``). The
  assessment already makes that read for the lakehouse controls, so this adds no Fabric call. The
  record's subject is the SQLEndpoint item the workspace lists with the endpoint's id; an endpoint
  that isn't a lakehouse's (a mirrored database's) has no record and is not evaluated.
- ``warehouse.properties`` (verified configuration): a warehouse's collation, from
  ``GET /workspaces/{id}/warehouses/{id}``, the read the SQL page already makes to find a warehouse's
  connection (warehouse.resolve). The assessment makes it only with a token the app already holds
  (excellence_service), never with a sign-in prompt.
- ``workspace.warehouses``: the collation of every warehouse read in the workspace, and how many there
  are, so one warehouse can be compared with the others.

Evidence keeps item names (cut at MAX_NAME characters), a status, a collation name and counts. No
connection string, SQL text, view or procedure body, or data. Lists are capped at MAX_LIST.

Not checked yet, because no read the assessment can make sees them: the views and stored procedures
inside a warehouse (the SQL best-practice rules need their text, read over the warehouse's SQL
connection), query insights (queryinsights views, also over that connection), and table maintenance
(statistics, compaction). Those controls need a read the assessment doesn't make.

References are to be re-checked against Microsoft Learn under FEA-106 before these controls leave
Preview.
"""

from __future__ import annotations

from typing import Any

from . import controls as ct
from . import evidence as ev

GET_LAKEHOUSE = "GET /v1/workspaces/{id}/lakehouses/{lakehouseId} (sqlEndpointProperties)"
GET_WAREHOUSE = "GET /v1/workspaces/{id}/warehouses/{warehouseId}"
ENDPOINT = "sqlendpoint.properties"
WAREHOUSE = "warehouse.properties"
WAREHOUSES = "workspace.warehouses"
MAX_NAME = 256  # characters kept of any one name
MAX_LIST = 50  # warehouses listed in one workspace.warehouses record
PROVISIONED = "success"


def _cut(name: Any) -> str:
    text = str(name or "")
    return text if len(text) <= MAX_NAME else text[:MAX_NAME - 1] + "…"


def _collation(got: dict[str, Any]) -> str | None:
    value = (got.get("properties") or {}).get("collationType")
    return _cut(value) if isinstance(value, str) and value.strip() else None


# ---- evidence


def endpoint_properties(ws_id: str, items: list[dict[str, Any]], lakehouses_read: dict[str, dict[str, Any] | None], *,
                        collected_at: str) -> list[dict[str, Any]]:
    """One ``sqlendpoint.properties`` record per SQLEndpoint item whose lakehouse was read and names it."""
    endpoints = {str(i["id"]).lower(): i for i in items if i.get("type") == "SQLEndpoint" and i.get("id")}
    out = []
    for lh in (i for i in items if i.get("type") == "Lakehouse" and i.get("id")):
        got = lakehouses_read.get(str(lh["id"]))
        if not got:
            continue
        props = (got.get("properties") or {}).get("sqlEndpointProperties") or {}
        ep = endpoints.get(str(props.get("id") or "").lower())
        if ep is None:
            continue
        status = props.get("provisioningStatus")
        subj = ev.subject("item", str(ep["id"]), _cut(ep.get("displayName")), workspace_id=ws_id,
                          item_type="SQLEndpoint")
        out.append(ev.record(ENDPOINT, subj, source=GET_LAKEHOUSE, confidence="verified_configuration",
                             collected_at=collected_at,
                             payload={"lakehouse_id": str(lh["id"]), "lakehouse": _cut(lh.get("displayName")),
                                      "provisioning_status": _cut(status) if isinstance(status, str) and status else None}))
    return out


def warehouse_properties(ws: dict[str, Any], items: list[dict[str, Any]],
                         warehouses_read: dict[str, dict[str, Any] | None], *,
                         collected_at: str) -> list[dict[str, Any]]:
    """One ``warehouse.properties`` record per warehouse in ``warehouses_read`` (absent or ``None``: it
    wasn't read, so no record), and one ``workspace.warehouses`` record when at least one was read."""
    ws_id = str(ws.get("id") or "")
    found = [i for i in items if i.get("type") == "Warehouse" and i.get("id")]
    out, listed = [], []
    for wh in found:
        got = warehouses_read.get(str(wh["id"]))
        if not got:
            continue
        name = _cut(wh.get("displayName") or got.get("displayName"))
        collation = _collation(got)
        subj = ev.subject("item", str(wh["id"]), name, workspace_id=ws_id, item_type="Warehouse")
        out.append(ev.record(WAREHOUSE, subj, source=GET_WAREHOUSE, confidence="verified_configuration",
                             collected_at=collected_at, payload={"collation": collation}))
        listed.append({"id": str(wh["id"]), "name": name, "collation": collation})
    if listed:
        listed.sort(key=lambda w: w["id"])
        out.append(ev.record(WAREHOUSES, ev.subject("workspace", ws_id, ws.get("displayName")), source=GET_WAREHOUSE,
                             confidence="verified_configuration", collected_at=collected_at,
                             payload={"count": len(found), "read": len(listed), "warehouses": listed[:MAX_LIST]}))
    return out


def evidence(ws: dict[str, Any], items: list[dict[str, Any]] | None, lakehouses_read: dict[str, dict[str, Any] | None],
             warehouses_read: dict[str, dict[str, Any] | None], *, collected_at: str) -> list[dict[str, Any]]:
    """All the warehouse and SQL endpoint evidence for one workspace (none when its items couldn't be listed)."""
    if items is None:
        return []
    return (endpoint_properties(str(ws.get("id") or ""), items, lakehouses_read, collected_at=collected_at)
            + warehouse_properties(ws, items, warehouses_read, collected_at=collected_at))


# ---- controls

_ENDPOINT_DOC = {"title": "What is the SQL analytics endpoint for a lakehouse?",
                 "url": "https://learn.microsoft.com/fabric/data-engineering/lakehouse-sql-analytics-endpoint"}
_GET_LAKEHOUSE_DOC = {"title": "Items - Get Lakehouse (Fabric REST API)",
                      "url": "https://learn.microsoft.com/rest/api/fabric/lakehouse/items/get-lakehouse"}
_COLLATION_DOC = {"title": "Data Warehouse collation", "url": "https://learn.microsoft.com/fabric/data-warehouse/collation"}
_GET_WAREHOUSE_DOC = {"title": "Items - Get Warehouse (Fabric REST API)",
                      "url": "https://learn.microsoft.com/rest/api/fabric/warehouse/items/get-warehouse"}
_COLLATE_DOC = {"title": "COLLATE (Transact-SQL)", "url": "https://learn.microsoft.com/sql/t-sql/statements/collations"}


def _names(rows: list[dict[str, Any]], limit: int = 5) -> str:
    names = [f"{x['name']} ({x['collation']})" for x in rows][:limit]
    more = len(rows) - len(names)
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


def _endpoint(ctx: ct.Context) -> dict[str, Any]:
    """FEA-WH-001. Severity Medium: a SQL analytics endpoint whose provisioning failed can't be queried, so
    T-SQL readers, reports on the lakehouse's SQL endpoint and anything joined to it through the
    endpoint stop working, while the lakehouse's Delta tables and Spark are unaffected and no data
    is lost. A real outage for SQL readers, bounded to one lakehouse, so medium rather than high.
    Verified from the lakehouse's own properties; any status other than Success or Failed (still
    provisioning, or none given) is not evaluated, never passed."""
    r = ctx.one(ENDPOINT)
    assert r is not None  # declared evidence is present before a control runs
    status = r["payload"].get("provisioning_status")
    lakehouse = r["payload"].get("lakehouse") or r["payload"].get("lakehouse_id")
    if status is None:
        return ct.not_evaluated(f"Fabric didn't report a provisioning status for the SQL endpoint of {lakehouse}.",
                                needs="the SQL endpoint's provisioning status")
    if status.lower() == PROVISIONED:
        return ct.passed([r["id"]], f"The SQL analytics endpoint of {lakehouse} is provisioned.")
    if status.lower() == "failed":
        return ct.failed([r["id"]], f"Provisioning of the SQL analytics endpoint of {lakehouse} failed, so it can't "
                                    "be queried with T-SQL.", detail={"status": status})
    return ct.not_evaluated(f"Fabric reports the SQL endpoint of {lakehouse} as {status}, so whether it works isn't "
                            "known yet.", needs="a finished provisioning status")


def _collations(ctx: ct.Context) -> dict[str, Any]:
    """FEA-WH-002. Severity Low: warehouses with different collations in one workspace compare text
    differently (case-sensitive in one, case-insensitive in the other), so a query or load that
    joins or unions their text columns fails with a collation conflict, or needs COLLATE on every
    such column. The failure is an error, not wrong data, and only hits work that combines those
    warehouses, so low. A collation can only be set when a warehouse is created, which is why it is
    advice to plan. Verified from the warehouses' own properties; it passes only when every
    warehouse in the workspace was read."""
    own = ctx.one(WAREHOUSE)
    ws = ctx.one(WAREHOUSES)
    assert own is not None and ws is not None
    mine = own["payload"].get("collation")
    if mine is None:
        return ct.not_evaluated("Fabric didn't report this warehouse's collation.", needs="the warehouse's collation")
    p = ws["payload"]
    others = [w for w in p.get("warehouses") or [] if w["id"] != own["subject"]["id"]]
    differ = [w for w in others if w.get("collation") and w["collation"].lower() != mine.lower()]
    ids = [own["id"], ws["id"]]
    if differ:
        return ct.failed(ids, f"This warehouse uses {mine}, and {len(differ)} other warehouse"
                              f"{'s' if len(differ) != 1 else ''} in the workspace use{'s' if len(differ) == 1 else ''} "
                              f"another collation: {_names(differ)}.",
                         detail={"collation": mine, "different": len(differ)})
    known = sum(1 for w in others if w.get("collation"))
    unknown = int(p.get("count") or 0) - 1 - known
    if unknown > 0:
        return ct.not_evaluated(f"{unknown} other warehouse{'s' if unknown != 1 else ''} in the workspace "
                                f"{'weren' if unknown != 1 else 'wasn'}'t read or didn't report a collation, so "
                                f"{'they' if unknown != 1 else 'it'} can't be compared.",
                                needs="every warehouse's collation")
    if not others:
        return ct.passed(ids, f"The only warehouse in this workspace ({mine}).")
    return ct.passed(ids, f"Same collation ({mine}) as the other {len(others)} warehouse"
                          f"{'s' if len(others) != 1 else ''} in the workspace.")


def _t(why: str, solution: str, steps: list[str], benefit: str, effort: str, risk: str, validation: str,
       rollback: str) -> dict[str, Any]:
    return {"why": why, "solution": solution, "steps": steps, "benefit": benefit, "effort": effort, "risk": risk,
            "validation": validation, "rollback": rollback}


CONTROLS = (
    ct.Control(
        id="FEA-WH-001", version="1.0.0", dimension="reliability", title="SQL analytics endpoint isn't provisioned",
        severity="medium", requirement="kratosforge_recommendation", scope="item", item_types=("SQLEndpoint",),
        evidence_kinds=(ENDPOINT,), evaluate=_endpoint, references=(_ENDPOINT_DOC, _GET_LAKEHOUSE_DOC),
        description="Fabric reports that provisioning of the lakehouse's SQL analytics endpoint failed. Not evaluated "
                    "while it is still provisioning, or for an endpoint that isn't a lakehouse's.",
        remediation=_t("Without a provisioned SQL analytics endpoint the lakehouse's tables can't be read with T-SQL, "
                       "so SQL queries, reports and semantic models that read through the endpoint fail.",
                       "Get the endpoint provisioned again, and if it keeps failing, raise it with Microsoft support.",
                       ["Open the lakehouse in Fabric and check the SQL analytics endpoint's status.",
                        "Check the capacity is running and the lakehouse's tables are Delta tables.",
                        "Retry provisioning from the lakehouse, or open a support request with the lakehouse id.",
                        "Assess again."],
                       "T-SQL readers and reports on the lakehouse work again.", "low", "low",
                       "Fabric reports the endpoint as provisioned, and the control passes.",
                       "Nothing to undo: provisioning changes no table.")),
    ct.Control(
        id="FEA-WH-002", version="1.0.0", dimension="architecture",
        title="Warehouses in one workspace use different collations", severity="low",
        requirement="kratosforge_recommendation", scope="item", item_types=("Warehouse",),
        evidence_kinds=(WAREHOUSE, WAREHOUSES), evaluate=_collations,
        references=(_COLLATION_DOC, _GET_WAREHOUSE_DOC, _COLLATE_DOC),
        description="The warehouse's collation differs from another warehouse's in the same workspace. Passes only "
                    "when every warehouse in the workspace was read.",
        remediation=_t("Text compares differently in warehouses with different collations, so a query or load that "
                       "joins or unions their text columns fails with a collation conflict or needs COLLATE on every "
                       "such column.",
                       "Agree one collation for the workspace's warehouses. A collation is set when a warehouse is "
                       "created, so plan the move for the odd one out, or keep it apart on purpose and turn this "
                       "control off in your profile.",
                       ["Open the evidence: each warehouse and its collation.",
                        "Decide which collation the workspace keeps.",
                        "Move the odd one's tables, views and procedures to a warehouse with that collation.",
                        "Point pipelines, reports and models at the new warehouse.",
                        "Retire the old warehouse once nothing reads it.", "Assess again."],
                       "Cross-warehouse queries and loads join text columns without collation errors.", "high", "medium",
                       "Queries that joined the warehouses run without COLLATE, and the control passes.",
                       "Point readers back at the old warehouse; keep it until the move is confirmed.")),
)
