"""Databricks DBUs billed, from ``system.billing.usage`` (Preview; tested against fakes, not a live workspace).

Billing is a Unity Catalog system table, so reading it runs one SQL query on a SQL warehouse the
person picks. That warehouse may start and bill a few DBUs itself, so the read needs a confirmed
``RemoteAuthorization`` for that warehouse (engines/authorization.py), like any remote cell. The
query is fixed text: only the number of days goes in, as a whole number.

DBUs are reported as DBUs: never added to Fabric capacity units or Snowflake credits, and never
turned into money here.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from .. import redact
from ..jsonfile import read_json, write_json
from .contract import CellRequest, SessionConfig
from .databricks_discovery import _text

SCHEMA = "databricks-billing@1"
ROW_LIMIT = 500
DAYS = (1, 7, 30)

# One row per job, pipeline, warehouse or cluster and SKU: what used the DBUs. usage_metadata names it.
SQL = """SELECT
  usage_metadata.job_id AS job_id,
  usage_metadata.dlt_pipeline_id AS pipeline_id,
  usage_metadata.warehouse_id AS warehouse_id,
  usage_metadata.cluster_id AS cluster_id,
  sku_name,
  CAST(SUM(usage_quantity) AS DOUBLE) AS dbu
FROM system.billing.usage
WHERE usage_unit = 'DBU' AND usage_date >= date_sub(current_date(), {days})
GROUP BY ALL
ORDER BY dbu DESC
LIMIT {limit}"""


def query(days: int) -> str:
    if days not in DAYS:
        raise ValueError(f"Read billing for {', '.join(map(str, DAYS))} days.")
    return SQL.format(days=int(days), limit=ROW_LIMIT)


def _what(row: dict[str, Any]) -> tuple[str, str]:
    for key, kind in (("job_id", "job"), ("pipeline_id", "pipeline"), ("warehouse_id", "warehouse"), ("cluster_id", "cluster")):
        if row.get(key):
            return kind, _text(row[key])
    return "other", ""


def rows_from(table: Any) -> list[dict[str, Any]]:
    """The query's rows as {kind, id, sku, dbu}; a DBU that isn't a number is dropped, not guessed."""
    out = []
    for row in table.to_pylist() if table is not None else []:
        try:
            dbu = float(row.get("dbu"))
        except (TypeError, ValueError):
            continue
        if dbu != dbu or dbu < 0:   # NaN or negative: not a usage figure
            continue
        kind, ident = _what(row)
        out.append({"kind": kind, "id": ident, "sku": _text(row.get("sku_name")), "dbu": round(dbu, 3)})
    return out


def read(adapter: Any, connection: dict[str, Any], warehouse_id: str, days: int, authorization: Any) -> dict[str, Any]:
    """Run the billing query on ``warehouse_id`` and close the session. ``authorization`` is required:
    the adapter refuses without one for that warehouse."""
    sql = query(days)
    session = adapter.open_session(SessionConfig("databricks-billing", "", {"connection": connection, "warehouse_id": warehouse_id}),
                                   authorization)
    try:
        result = session.execute(CellRequest(sql, "sql", "spark", preview_rows=0, result_rows=ROW_LIMIT))
    finally:
        session.close()
    if result.status != "ok":
        errors = [o.get("evalue") or o.get("ename") or "" for o in result.outputs if o.get("type") == "error"]
        return {"ok": False, "error": redact.secrets(_text(" ".join(e for e in errors if e) or "The billing query didn't finish.", 600))}
    rows = rows_from(result.table)
    return {"ok": True, "schema": SCHEMA, "days": days, "warehouse_id": warehouse_id, "seconds": round(result.seconds, 1),
            "read_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_dbu": round(sum(r["dbu"] for r in rows), 3), "rows": rows}


def _file(root: Path, host: str) -> Path:
    name = "".join(c if c.isalnum() else "_" for c in host.removeprefix("https://"))[:120] or "workspace"
    return root / f"{name}.json"


def save(root: Path, host: str, result: dict[str, Any]) -> None:
    """Keep the last read on this PC, per workspace, so the Cost tab shows it without reading again."""
    root.mkdir(parents=True, exist_ok=True)
    write_json(_file(root, host), result | {"host": host})


def last(root: Path, host: str) -> dict[str, Any] | None:
    data = read_json(_file(root, host), None)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return json.loads(json.dumps(redact.walk(data)))
