"""Fabric pipeline runs for Monitor > Runs (Data Factory DF-1, engine half).

Two reads, no writes, no capacity: ``fabric_pipeline_runs`` lists the job history of every pipeline
in the chosen workspaces (a background scan, like Fabric schedules: ``{"pending": True}`` until it's
done), and ``fabric_pipeline_activity_runs`` reads one run's activities when its row is opened, never
in the background. Activity input, output and errors pass through redact.secrets and are capped, so a
large Lookup output can't flood the reply.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .fabric_api import FabricClient
from .redact import describe, secrets

_log = logging.getLogger(__name__)

RUNS_PER_PIPELINE = 20
MAX_RUNS = 300
TEXT_LIMIT = 16_000  # characters of one activity's input, output or error detail
WINDOW_SLACK = dt.timedelta(minutes=5)  # around the run's start and end for the activity query

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_FRACTION = re.compile(r"\.(\d+)")

# Fabric job instance status -> the states local pipeline runs use (orchestration/runs.py).
RUN_STATES = {"NotStarted": "pending", "InProgress": "running", "Completed": "succeeded", "Failed": "failed",
              "Cancelled": "cancelled", "Deduped": "cancelled"}
ACTIVITY_STATES = {"Queued": "pending", "InProgress": "running", "Succeeded": "succeeded", "Failed": "failed",
                   "Cancelled": "cancelled", "Skipped": "skipped"}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    # Fabric sends up to 7 fractional digits; fromisoformat takes 6. Only the digits right after the
    # dot are the fraction, so an offset after them (+05:00) stays as it is.
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    try:
        t = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _seconds(start: Any, end: Any) -> float | None:
    a, b = _parse(start), _parse(end)
    return round((b - a).total_seconds(), 3) if a and b else None


def run_row(job: dict[str, Any], pipeline: dict[str, Any]) -> dict[str, Any]:
    """One Fabric job instance of a pipeline, in the shape Monitor > Runs lists."""
    reason = job.get("failureReason") or {}
    status = job.get("status") or ""
    return {"where": "fabric", "workspace_id": pipeline["workspace_id"], "workspace_name": pipeline["workspace_name"],
            "item_id": pipeline["id"], "name": pipeline.get("displayName") or "", "job_id": job.get("id") or "",
            "state": RUN_STATES.get(status, "unknown"), "native_status": status,
            "started": job.get("startTimeUtc"), "ended": job.get("endTimeUtc"),
            "seconds": _seconds(job.get("startTimeUtc"), job.get("endTimeUtc")),
            "invoke": job.get("invokeType") or "",
            "message": secrets((reason.get("message", "") if isinstance(reason, dict) else str(reason)) or "")}


def _text(value: Any) -> dict[str, Any]:
    """An activity's input, output or error detail as capped, redacted text."""
    if value in (None, "", {}, []):
        return {"text": "", "truncated": False}
    raw = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, default=str)
    clean = secrets(raw)
    return {"text": clean[:TEXT_LIMIT], "truncated": len(clean) > TEXT_LIMIT}


def activity_row(a: dict[str, Any]) -> dict[str, Any]:
    err = a.get("error") if isinstance(a.get("error"), dict) else {}
    status = a.get("status") or ""
    message = secrets(str(err.get("message") or ""))
    return {"name": a.get("activityName") or "", "type": a.get("activityType") or "",
            "state": ACTIVITY_STATES.get(status, "unknown"), "native_status": status,
            "started": a.get("activityRunStart"), "ended": a.get("activityRunEnd"),
            "ms": a.get("durationInMs"), "retry": a.get("retryAttempt"), "run_id": a.get("activityRunId") or "",
            "iteration": a.get("iterationHash") or "",
            "input": _text(a.get("input")), "output": _text(a.get("output")),
            "error": {"code": str(err.get("errorCode") or ""), "message": message[:TEXT_LIMIT],
                      "failure_type": str(err.get("failureType") or "")} if message or err.get("errorCode") else None}


class FabricPipelineRunsMixin:
    _fabric_pipeline_runs: dict[str, Any] | None = None
    _fabric_pipeline_runs_lock = threading.Lock()

    def fabric_pipeline_runs(self, refresh: bool = False, all_workspaces: bool = False,
                             workspace_ids: list[str] | None = None) -> dict[str, Any]:
        """Pipeline runs in Fabric, newest first, from a background scan ({"pending": True} meanwhile).

        Workspaces: ``workspace_ids`` when given, else the ones Schedules lists (pinned, environments,
        notebooks), or every one with ``all_workspaces``. Reads only: no job starts."""
        scope = {"all_workspaces": bool(all_workspaces), "workspace_ids": sorted(set(workspace_ids or []))}
        with self._fabric_pipeline_runs_lock:
            cached = self._fabric_pipeline_runs
            same = cached is not None and cached.get("scope") == scope
            if not same or refresh:
                if cached and cached.get("pending") and same:
                    return dict(cached)  # a scan for this scope is already going
                try:
                    token = self.auth.fabric_token()  # type: ignore[attr-defined]  # here: MSAL isn't thread-safe
                except Exception as exc:  # noqa: BLE001
                    self._fabric_pipeline_runs = {"pending": False, "runs": [], "scope": scope, "error": describe(exc)}
                else:
                    self._fabric_pipeline_runs = {"pending": True, "runs": [], "scope": scope}
                    threading.Thread(target=self._scan_fabric_pipeline_runs, args=(token, scope), daemon=True).start()
            return dict(self._fabric_pipeline_runs or {})

    def _pipeline_run_workspaces(self, token: str, scope: dict[str, Any]) -> list[dict[str, str]]:
        if scope["workspace_ids"]:
            wanted = set(scope["workspace_ids"])
            return [{"id": w["id"], "name": w["displayName"]}
                    for w in FabricClient(lambda: token).list_workspaces() if w["id"] in wanted]
        return self._schedule_workspaces(scope["all_workspaces"], token)  # type: ignore[attr-defined]

    def _scan_fabric_pipeline_runs(self, token: str, scope: dict[str, Any]) -> None:
        client = FabricClient(lambda: token)
        try:
            workspaces = self._pipeline_run_workspaces(token, scope)
        except Exception as exc:  # noqa: BLE001
            self._set_pipeline_runs({"pending": False, "runs": [], "scope": scope, "error": describe(exc)})
            return
        missed: list[str] = []  # one log line per scan, not one per pipeline

        def pipelines(ws: dict[str, str]) -> list[dict[str, Any]]:
            try:
                return [{"workspace_id": ws["id"], "workspace_name": ws["name"], **it}
                        for it in client.list_items(ws["id"]) if it.get("type") == "DataPipeline"]
            except Exception as exc:  # noqa: BLE001
                missed.append(f"items in workspace {ws['id']}: {describe(exc)}")
                return []

        def runs(p: dict[str, Any]) -> list[dict[str, Any]]:
            try:
                jobs = client.list_job_instances(p["workspace_id"], p["id"])
            except Exception as exc:  # noqa: BLE001
                missed.append(f"runs of pipeline {p['id']}: {describe(exc)}")
                return []
            return [run_row(j, p) for j in jobs[:RUNS_PER_PIPELINE]]

        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [p for chunk in pool.map(pipelines, workspaces) for p in chunk]
            rows = [r for chunk in pool.map(runs, found) for r in chunk]
        if missed:
            _log.warning("Fabric pipeline runs: %d reads failed; the first was %s", len(missed), missed[0])
        rows.sort(key=lambda r: str(r.get("started") or ""), reverse=True)
        self._set_pipeline_runs({"pending": False, "runs": rows[:MAX_RUNS], "scope": scope,
                                 "workspaces": len(workspaces), "pipelines": len(found), "missed": len(missed),
                                 "scanned": _iso(_now())})

    def _set_pipeline_runs(self, value: dict[str, Any]) -> None:
        with self._fabric_pipeline_runs_lock:
            current = self._fabric_pipeline_runs
            if current is None or current.get("scope") == value.get("scope"):  # a newer scope's scan wins
                self._fabric_pipeline_runs = value

    def fabric_pipeline_activity_runs(self, workspace_id: str, job_id: str, started: str = "",
                                      ended: str = "") -> dict[str, Any]:
        """One Fabric pipeline run's activities, newest first. A read: no job starts.

        ``started`` and ``ended`` are the run's times from fabric_pipeline_runs; Fabric needs a time
        window, so the query covers them with a few minutes either side (up to now while it runs)."""
        if not _GUID.fullmatch(str(workspace_id or "")) or not _GUID.fullmatch(str(job_id or "")):
            raise ValueError("Pick a pipeline run: its workspace id and run id are needed (Fabric GUIDs).")
        now = _now()
        start = _parse(started) or now - dt.timedelta(days=1)
        end = _parse(ended) or now
        found = self.fabric.query_activity_runs(  # type: ignore[attr-defined]
            workspace_id, job_id, _iso(start - WINDOW_SLACK), _iso(end + WINDOW_SLACK))
        acts = [activity_row(a) for a in found]
        return {"workspace_id": workspace_id, "job_id": job_id, "activities": acts, "read": _iso(now)}
