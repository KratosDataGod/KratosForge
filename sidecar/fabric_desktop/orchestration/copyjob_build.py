"""Build the Copy job that lands a source in the landing zone (scale plan stage 4.1).

A team asks for a source with ``detail.copy``: the Fabric connection to read (by id, so its
credentials stay in Fabric), the tables, how each run copies each one (the whole table, or the rows
changed since the last run by a column), and how often. A platform admin approves, and the app makes
one Copy job ``CJ_Landing_<source>`` in the landing workspace, as that admin, writing each table to
``LH_Landing/Files/<source>/<table>/`` (central/landing_copy_service.py).

``check`` normalizes and checks what was asked; ``content`` builds ``copyjob-content.json`` in the
shape DF-6's reader (copyjob.describe) takes, so the Data Factory page shows the job as made. Those
field names follow Microsoft's Copy job definition article and no real export has been checked yet
(Data Factory plan decision 4): until a tenant run confirms them, this stays Preview. Nothing here
talks to Fabric.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .copyjob import CONTENT

PREFIX = "CJ_Landing_"
MAX_TABLES = 50
MODES = ("full", "incremental")
_TABLE = re.compile(r"^([A-Za-z_][\w$#@]{0,127})\.([A-Za-z_][\w$#@]{0,127})$")
_COLUMN = re.compile(r"^[A-Za-z_][\w$#@ ]{0,127}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class CopyRequestError(ValueError):
    """What was asked can't become a Copy job; the message says what to change."""


def job_name(source: str) -> str:
    return f"{PREFIX}{source}"


def folder(table: str) -> str:
    """The folder a table lands in under its source: its name without the schema, in lower case."""
    return table.split(".", 1)[1].lower()


def check(copy: Any) -> dict[str, Any]:
    """``copy`` as sent by a team, normalized: {connection_id, connection_name, tables, times}. Raises
    CopyRequestError naming the first thing to fix."""
    if not isinstance(copy, dict):
        raise CopyRequestError("Say which connection and which tables to copy")
    conn = str(copy.get("connection_id") or "").strip().lower()
    if not _ID.match(conn):
        raise CopyRequestError("Pick the Fabric connection to read from")
    rows = copy.get("tables")
    if not isinstance(rows, list) or not rows:
        raise CopyRequestError("Name at least one table, like dbo.Customer")
    if len(rows) > MAX_TABLES:
        raise CopyRequestError(f"A request copies {MAX_TABLES} tables at most: send the rest as another source")
    tables, folders = [], {}
    for row in rows:
        row = row if isinstance(row, dict) else {"table": row}
        name = str(row.get("table") or "").strip()
        if not _TABLE.match(name):
            raise CopyRequestError(f"{name or '(blank)'}: name a table as schema.table, like dbo.Customer")
        mode = str(row.get("mode") or "full").strip()
        if mode not in MODES:
            raise CopyRequestError(f"{name}: each run copies the whole table (full) or new and changed rows (incremental)")
        column = str(row.get("column") or "").strip()
        if mode == "incremental" and not _COLUMN.match(column):
            raise CopyRequestError(f"{name}: say which column tells new and changed rows, like ModifiedAt")
        where = folder(name)
        if where in folders:
            raise CopyRequestError(f"{name} and {folders[where]} would land in the same folder {where}/: "
                                   "ask for them as two sources")
        folders[where] = name
        tables.append({"table": name, "mode": mode, "column": column if mode == "incremental" else "", "folder": where})
    times = copy.get("times") or []
    if not isinstance(times, list) or not all(isinstance(t, str) and _TIME.match(t.strip()) for t in times):
        raise CopyRequestError("Times of day are HH:MM in UTC, like 02:00")
    return {"connection_id": conn, "connection_name": str(copy.get("connection_name") or "").strip()[:200],
            "tables": tables, "times": sorted({t.strip() for t in times})}


def content(spec: dict[str, Any], source: str, workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
    """The Copy job's copyjob-content.json: Batch mode, the connection as the source, the landing
    lakehouse's Files as the destination, one activity per table writing Parquet to its folder."""
    activities = []
    for t in spec["tables"]:
        schema, table = t["table"].split(".", 1)
        src: dict[str, Any] = {"datasetSettings": {"schema": schema, "table": table}}
        if t["mode"] == "incremental":
            src["changeDataSettings"] = {"readMethod": "Incremental", "columns": [{"name": t["column"]}]}
        activities.append({"id": f"{schema}.{table}", "properties": {
            "source": src,
            "destination": {"datasetSettings": {"location": {"folderPath": f"{source}/{t['folder']}"},
                                                "format": "Parquet"},
                            "writeBehavior": "Append"}}})
    return {"properties": {
        "jobMode": "Batch",
        "source": {"type": "SqlServer", "connectionSettings": {
            "type": "SqlServer", "externalReferences": {"connection": spec["connection_id"]}}},
        "destination": {"type": "LakehouseFiles", "connectionSettings": {
            "type": "Lakehouse", "typeProperties": {"workspaceId": workspace_id, "artifactId": lakehouse_id,
                                                    "rootFolder": "Files"}}},
        "policy": {"timeout": "0.12:00:00", "retry": 0}},
        "activities": activities}


def parts(job: dict[str, Any]) -> dict[str, str]:
    """The definition parts save_item takes."""
    return {CONTENT: json.dumps(job, indent=2)}
