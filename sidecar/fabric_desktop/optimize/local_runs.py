"""This PC's notebook work as fdopt_local_execution rows, for Optimize to count what ran here.

Four sources, all already on the PC: ``history.jsonl`` (each notebook write to a OneLake table,
lakehouse.py), ``schedule_runs.jsonl`` (each scheduled notebook run, local_schedules.py), the
open metadata project's ``run_log`` in its environments kept on this PC (seconds, rows read and
written), and Prove's ``proofs.jsonl`` (the PC side of each proof, measured by ``RunMeter``).
A write is named by the lakehouse it wrote to (its ids); a scheduled run or a proof by its
notebook's name, since a notebook on this PC has no Fabric id; a metadata run by its project and
table. CPU seconds and peak memory are filled only where they were measured (a proof's run);
everywhere else they are empty, never 0. Code and table contents are never included; table names
are, as the rest of Optimize's rows name tables. Pure: it reads and writes nothing.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .shared import artifact_key, record_id, timestamp

ENGINE = "local"
_TABLE_URI = re.compile(r"^abfss://([^@/]+)@[^/]+/([^/]+)/Tables/(.+)$")
_RESULT = {"ok": "succeeded", "failed": "failed", "skipped": "skipped", "cancelled": "cancelled"}
# A proof that ended; one still going waits, as a scheduled run still going does.
_PROOF_ENDED = ("done", "failed", "cancelled")


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


def _ran_here(run: dict[str, Any]) -> bool:
    """A run_log row run on this PC: not in Fabric (its batch starts "fabric-") and not by an agent."""
    return not str(run.get("batch_id") or "").startswith("fabric-") and not run.get("worker")


def from_run_log(rows: list[dict[str, Any]], project: str, installation_id: str) -> list[dict[str, Any]]:
    """Metadata runs on this PC from a project's run_log (one environment's, or several): seconds and
    rows written. A run that waits on a decision didn't run, so it's left out."""
    out = []
    for r in rows:
        started, seconds, status = _at(r.get("started_at")), _seconds(r.get("seconds")), str(r.get("status") or "")
        if not _ran_here(r) or status not in _RESULT or not started or seconds is None or not r.get("entity"):
            continue
        finished = timestamp(dt.datetime.fromisoformat(started) + dt.timedelta(seconds=seconds))
        out.append({"execution_id": record_id("metadata-run", installation_id, project, r.get("env"), r.get("run_id"),
                                              r.get("entity")),
                    "project": f"{project}.{r['entity']}", "artifact_key": None, "workload_type": "metadata_run",
                    "engine": ENGINE, "started_at": started, "finished_at": finished, "seconds": seconds,
                    "rows": r.get("rows_written"), "bytes": None, "cpu_seconds": None, "peak_memory_bytes": None,
                    "result": _RESULT[status], "fabric_workload_type": "Notebook", "counts_for": None})
    return out


def from_proofs(records: list[dict[str, Any]], installation_id: str) -> list[dict[str, Any]]:
    """The PC side of each finished proof, with the CPU seconds and peak memory RunMeter measured.
    A proof times the PC run but not when it began and ended inside the proof, so only its end is
    given (the proof's), and its start is left empty."""
    out = []
    for r in records:
        pc = r.get("pc") or {}
        finished, seconds = _at(r.get("finished_at")), _seconds(pc.get("seconds"))
        if r.get("state") not in _PROOF_ENDED or pc.get("status") not in _RESULT or not finished or seconds is None:
            continue
        measured = bool(pc.get("measured"))
        peak = _seconds(pc.get("peak_memory_mb"))  # a non-negative number, as seconds are
        out.append({"execution_id": record_id("proof-run", installation_id, r.get("id")),
                    "project": r.get("name"), "artifact_key": None, "workload_type": "proof_run", "engine": ENGINE,
                    "started_at": None, "finished_at": finished, "seconds": seconds, "rows": None, "bytes": None,
                    "cpu_seconds": _seconds(pc.get("cpu_seconds")) if measured else None,
                    "peak_memory_bytes": int(peak * 1024 ** 2) if measured and peak is not None else None,
                    "result": _RESULT[pc["status"]], "fabric_workload_type": "Notebook", "counts_for": None})
    return out
