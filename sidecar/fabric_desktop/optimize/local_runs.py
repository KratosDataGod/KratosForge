"""This PC's notebook work as fdopt_local_execution rows, for Optimize to count what ran here.

Two sources, both already on the PC: ``history.jsonl`` (each notebook write to a OneLake table,
lakehouse.py) and ``schedule_runs.jsonl`` (each scheduled notebook run, local_schedules.py).
A write is named by the lakehouse it wrote to (its ids); a scheduled run by its notebook's name,
since a notebook on this PC has no Fabric id. Code and table contents are never included; a
write's table name is, as the rest of Optimize's rows name tables. Pure: it reads and writes
nothing.
"""

from __future__ import annotations

import re
from typing import Any

from .shared import artifact_key, record_id, timestamp

ENGINE = "local"
_TABLE_URI = re.compile(r"^abfss://([^@/]+)@[^/]+/([^/]+)/Tables/(.+)$")
_RESULT = {"ok": "succeeded", "failed": "failed", "skipped": "skipped"}


def _seconds(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _at(value: Any) -> str | None:
    try:
        return timestamp(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def from_history(rows: list[dict[str, Any]], installation_id: str) -> list[dict[str, Any]]:
    """Notebook writes to OneLake tables; other history rows (publishes) are left out."""
    out = []
    for r in rows:
        match = _TABLE_URI.match(str(r.get("table_uri") or ""))
        finished = _at(r.get("finished_at"))
        if not str(r.get("job") or "").startswith("Notebook write") or not match or not finished:
            continue
        ws, lh, table = match.groups()
        out.append({"execution_id": record_id("notebook-write", installation_id, r.get("table_uri"), finished,
                                              r.get("delta_version")),
                    "project": table.replace("/", "."), "artifact_key": artifact_key(ws, lh),
                    "workload_type": "notebook_write", "engine": ENGINE, "started_at": None, "finished_at": finished,
                    "seconds": _seconds(r.get("seconds")), "rows": r.get("rows"), "bytes": None, "cpu_seconds": None,
                    "peak_memory_bytes": None, "result": "succeeded", "fabric_workload_type": "Notebook",
                    "counts_for": None})
    return out


def from_schedule_runs(rows: list[dict[str, Any]], notebooks: set[str], installation_id: str) -> list[dict[str, Any]]:
    """Scheduled runs of a notebook on this PC (``notebooks`` are the PC's notebook names, so a
    metadata schedule isn't taken for one); a run still going has no end yet and waits."""
    out = []
    for r in rows:
        started, finished = _at(r.get("started")), _at(r.get("ended"))
        if r.get("label") not in notebooks or not started or not finished:
            continue
        out.append({"execution_id": record_id("notebook-run", installation_id, r.get("schedule_id"), started),
                    "project": r["label"], "artifact_key": None, "workload_type": "notebook_run", "engine": ENGINE,
                    "started_at": started, "finished_at": finished, "seconds": _seconds(r.get("seconds")), "rows": None,
                    "bytes": None, "cpu_seconds": None, "peak_memory_bytes": None,
                    "result": _RESULT.get(r.get("status"), "failed"), "fabric_workload_type": "Notebook",
                    "counts_for": None})
    return out
