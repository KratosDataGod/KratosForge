"""Databricks DBUs billed, from ``system.billing.usage`` (Preview; tested against fakes, not a live workspace).

Billing is a Unity Catalog system table, so reading it runs one SQL query on a SQL warehouse the
person picks. That warehouse may start and bill a few DBUs itself, so the read needs a confirmed
``RemoteAuthorization`` for that warehouse (engines/authorization.py), like any remote cell. The
query is fixed text: only the number of days goes in, as a whole number.

DBUs are reported as DBUs: never added to Fabric capacity units or Snowflake credits, and never
turned into money here.

Per run (Compare platforms for one workload, Preview): when the read names jobs (``job_ids``, the
jobs a person linked to a workload), the same session runs a second fixed query that sums each of
those jobs' runs by ``usage_metadata.job_run_id`` and SKU. Only whole-number job ids go in. The
result keeps them under ``runs``, beside the rows it always had.
"""

from __future__ import annotations

import datetime as dt
import json
import re
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


# One row per run of a linked job and SKU. The job ids are whole numbers, checked before they go in.
RUNS_SQL = """SELECT
  usage_metadata.job_id AS job_id,
  usage_metadata.job_run_id AS job_run_id,
  sku_name,
  MIN(usage_start_time) AS started,
  CAST(SUM(usage_quantity) AS DOUBLE) AS dbu
FROM system.billing.usage
WHERE usage_unit = 'DBU' AND usage_date >= date_sub(current_date(), {days})
  AND usage_metadata.job_id IN ({jobs}) AND usage_metadata.job_run_id IS NOT NULL
GROUP BY ALL
ORDER BY started DESC
LIMIT {limit}"""
MAX_JOBS = 20
_JOB_ID = re.compile(r"^[0-9]{1,30}$")


def query(days: int) -> str:
    if days not in DAYS:
        raise ValueError(f"Read billing for {', '.join(map(str, DAYS))} days.")
    return SQL.format(days=int(days), limit=ROW_LIMIT)


def job_ids(values: Any) -> list[str]:
    """Job ids as Databricks gives them: whole numbers, at most MAX_JOBS, each once."""
    out = []
    for v in values or []:
        text = str(v).strip()
        if isinstance(v, bool) or not _JOB_ID.fullmatch(text):
            raise ValueError(f"{v!r} isn't a Databricks job id. A job id is a whole number.")
        if text not in out:
            out.append(text)
    if len(out) > MAX_JOBS:
        raise ValueError(f"Read the runs of at most {MAX_JOBS} jobs at a time.")
    return out


def runs_query(days: int, jobs: Any) -> str:
    """The per-run query for ``jobs``; refuses days it doesn't offer and anything but job ids."""
    query(days)
    ids = job_ids(jobs)
    if not ids:
        raise ValueError("Link a Databricks job to the workload first.")
    return RUNS_SQL.format(days=int(days), jobs=", ".join(f"'{i}'" for i in ids), limit=ROW_LIMIT)


def _when(value: Any) -> str | None:
    if isinstance(value, dt.datetime):
        t = value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
        return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _text(value) or None


def run_rows_from(table: Any) -> list[dict[str, Any]]:
    """The per-run query's rows as {job_id, job_run_id, sku, started, dbu}. A row with no run id or a
    DBU that isn't a number is dropped, not guessed."""
    out = []
    for row in table.to_pylist() if table is not None else []:
        try:
            dbu = float(row.get("dbu"))
        except (TypeError, ValueError):
            continue
        if dbu != dbu or dbu < 0 or not row.get("job_run_id") or not row.get("job_id"):
            continue
        out.append({"job_id": _text(row["job_id"]), "job_run_id": _text(row["job_run_id"]),
                    "sku": _text(row.get("sku_name")), "started": _when(row.get("started")), "dbu": round(dbu, 6)})
    return out


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


def _error(result: Any, fallback: str) -> str:
    errors = [o.get("evalue") or o.get("ename") or "" for o in result.outputs if o.get("type") == "error"]
    return redact.secrets(_text(" ".join(e for e in errors if e) or fallback, 600))


def read(adapter: Any, connection: dict[str, Any], warehouse_id: str, days: int, authorization: Any,
         jobs: Any = None) -> dict[str, Any]:
    """Run the billing query on ``warehouse_id`` and close the session. ``authorization`` is required:
    the adapter refuses without one for that warehouse. With ``jobs``, the per-run query for those
    jobs runs in the same session; its rows are ``runs``."""
    sql = query(days)
    runs_sql = runs_query(days, jobs) if jobs else ""
    session = adapter.open_session(SessionConfig("databricks-billing", "", {"connection": connection, "warehouse_id": warehouse_id}),
                                   authorization)
    per_run = None
    try:
        result = session.execute(CellRequest(sql, "sql", "spark", preview_rows=0, result_rows=ROW_LIMIT))
        if result.status == "ok" and runs_sql:
            per_run = session.execute(CellRequest(runs_sql, "sql", "spark", preview_rows=0, result_rows=ROW_LIMIT))
    finally:
        session.close()
    if result.status != "ok":
        return {"ok": False, "error": _error(result, "The billing query didn't finish.")}
    rows = rows_from(result.table)
    out: dict[str, Any] = {"ok": True, "schema": SCHEMA, "days": days, "warehouse_id": warehouse_id,
                           "seconds": round(result.seconds, 1),
                           "read_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "total_dbu": round(sum(r["dbu"] for r in rows), 3), "rows": rows}
    if per_run is not None and per_run.status != "ok":
        # The totals stand; the runs aren't known, which is not the same as none.
        out |= {"runs": None, "run_jobs": job_ids(jobs),
                "runs_error": "The query for the linked jobs' runs didn't finish: " + _error(per_run, "no reason was given.")}
    elif per_run is not None:
        out |= {"runs": run_rows_from(per_run.table), "run_jobs": job_ids(jobs)}
    return out


def _file(root: Path, host: str, suffix: str = "") -> Path:
    name = "".join(c if c.isalnum() else "_" for c in host.removeprefix("https://"))[:120] or "workspace"
    return root / f"{name}{suffix}.json"


def save(root: Path, host: str, result: dict[str, Any]) -> None:
    """Keep the last read on this PC, per workspace, so the Cost tab shows it without reading again.
    A read with per-run figures also keeps those apart (``last_runs``), so a later read of the totals
    alone doesn't drop them."""
    root.mkdir(parents=True, exist_ok=True)
    write_json(_file(root, host), {k: v for k, v in result.items() if k not in ("runs", "run_jobs", "runs_error")}
               | {"host": host})
    if "runs" in result:
        write_json(_file(root, host, ".runs"), {k: result.get(k) for k in (
            "schema", "days", "warehouse_id", "read_at", "runs", "run_jobs", "runs_error")} | {"host": host})


def _load(path: Path) -> dict[str, Any] | None:
    data = read_json(path, None)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return json.loads(json.dumps(redact.walk(data)))


def last(root: Path, host: str) -> dict[str, Any] | None:
    return _load(_file(root, host))


def last_runs(root: Path, host: str) -> dict[str, Any] | None:
    """The last per-run read of linked jobs for this workspace, or None when there's none."""
    return _load(_file(root, host, ".runs"))
