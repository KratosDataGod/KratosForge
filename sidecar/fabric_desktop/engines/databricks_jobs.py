"""Databricks jobs and Lakeflow pipeline runs for Monitor (Preview; tested against fakes, not a live workspace).

Read-only: every request is a GET on a documented REST API, so reading runs never starts a job, a
pipeline, a warehouse or a cluster and bills no Databricks units (DBU).

    Jobs API             GET /api/2.1/jobs/runs/list        recent job runs (one row per run)
                         GET /api/2.1/jobs/runs/get         one run with its tasks and their errors
    Pipelines API        GET /api/2.0/pipelines             Lakeflow Declarative Pipelines
                         GET /api/2.0/pipelines/{id}/updates  each pipeline's recent updates

Each list stands alone: one you can't read (a 403 on pipelines, say) says why and the other still
comes back. Names, states and error text from the workspace come back as one line of plain text
(databricks_discovery._text), and every error is redacted.

DBUs billed for a run come from system.billing.usage, which needs a SQL warehouse to read, so this
module never reports them: the Cost screen reads billing and says so first.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from .. import redact
from .databricks_auth import DatabricksAuthError
from .databricks_discovery import OddReply, _object, _text
from .databricks_sql import Client, DatabricksError

SCHEMA = "databricks-runs@1"
RUNS_LIMIT = 100           # job runs read per call (the Jobs API pages 25 at a time)
PIPELINES_LIMIT = 50       # pipelines whose updates are read
UPDATES_PER_PIPELINE = 5
_ERROR_MAX = 1000          # characters of one task's error
_RUN_ID = re.compile(r"^[0-9]{1,20}$")

# The Jobs API's life cycle and result states, in words.
RESULT_TEXT = {"SUCCESS": "Succeeded", "FAILED": "Failed", "TIMEDOUT": "Timed out", "CANCELED": "Cancelled",
               "SKIPPED": "Skipped", "INTERNAL_ERROR": "Failed (Databricks error)", "SUCCESS_WITH_FAILURES": "Succeeded with failures",
               "UPSTREAM_FAILED": "Not run: an upstream task failed", "UPSTREAM_CANCELED": "Not run: an upstream task was cancelled",
               "EXCLUDED": "Excluded", "MAXIMUM_CONCURRENT_RUNS_REACHED": "Not run: too many runs at once"}
RUNNING = {"PENDING": "Waiting", "QUEUED": "Queued", "RUNNING": "Running", "TERMINATING": "Finishing", "BLOCKED": "Blocked",
           "WAITING_FOR_RETRY": "Waiting to retry"}
# Lakeflow pipeline update states.
UPDATE_TEXT = {"COMPLETED": "Succeeded", "FAILED": "Failed", "CANCELED": "Cancelled", "QUEUED": "Queued", "CREATED": "Queued",
               "WAITING_FOR_RESOURCES": "Starting", "INITIALIZING": "Starting", "RESETTING": "Starting",
               "SETTING_UP_TABLES": "Starting", "RUNNING": "Running", "STOPPING": "Finishing"}


def _state(state: dict[str, Any]) -> tuple[str, str]:
    """(outcome, words) for a run's or task's state: outcome is ok, failed, running or other."""
    life = str(state.get("life_cycle_state") or "")
    result = str(state.get("result_state") or "")
    if result:
        outcome = "ok" if result == "SUCCESS" else ("failed" if result in ("FAILED", "TIMEDOUT", "INTERNAL_ERROR",
                                                                          "SUCCESS_WITH_FAILURES", "UPSTREAM_FAILED") else "other")
        return outcome, RESULT_TEXT.get(result, _text(result.title()))
    if life in RUNNING:
        return "running", RUNNING[life]
    if life == "INTERNAL_ERROR":
        return "failed", "Failed (Databricks error)"
    return "other", _text(life.title()) or "Unknown"


def _ms(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError, OverflowError):
        raise OddReply(f"a time of {_text(value)!r} isn't milliseconds") from None


def _took(run: dict[str, Any]) -> int:
    """Seconds a run took: run_duration when given, else end minus start (0 while running)."""
    if run.get("run_duration"):
        return _ms(run["run_duration"]) // 1000
    start, end = _ms(run.get("start_time")), _ms(run.get("end_time"))
    return (end - start) // 1000 if start and end >= start else 0


def _compute(task_or_run: dict[str, Any]) -> str:
    if task_or_run.get("existing_cluster_id"):
        return "All-purpose cluster"
    if task_or_run.get("new_cluster") or task_or_run.get("job_cluster_key"):
        return "Job cluster"
    if (task_or_run.get("sql_task") or {}).get("warehouse_id"):
        return "SQL warehouse"
    return "Serverless" if task_or_run.get("environment_key") or task_or_run.get("tasks") is None else ""


def run_row(run: dict[str, Any]) -> dict[str, Any]:
    run = _object(run, "a job run")
    outcome, words = _state(_object(run.get("state") or {}, "a run's state"))
    tasks = [_object(t, "a task") for t in run.get("tasks") or []]
    failed = [t.get("task_key") for t in tasks if _state(t.get("state") or {})[0] == "failed"]
    computes = sorted({c for c in (_compute(t) for t in tasks) if c}) or ([_compute(run)] if _compute(run) else [])
    return {"kind": "job", "id": _text(run.get("run_id")), "job_id": _text(run.get("job_id")),
            "name": _text(run.get("run_name")) or f"job {_text(run.get('job_id'))}",
            "started": _ms(run.get("start_time")), "took_s": _took(run), "outcome": outcome,
            "result": f"{words} at task {_text(failed[0])}" if failed and outcome == "failed" else words,
            "trigger": _text((run.get("trigger") or "").title()), "compute": ", ".join(computes),
            "creator": _text(run.get("creator_user_name")), "url": _text(run.get("run_page_url"), 500)}


def task_row(task: dict[str, Any]) -> dict[str, Any]:
    task = _object(task, "a task")
    outcome, words = _state(task.get("state") or {})
    error = ""
    if outcome == "failed":
        error = (task.get("state") or {}).get("state_message") or ""
    notebook = (task.get("notebook_task") or {}).get("notebook_path") or ""
    return {"key": _text(task.get("task_key")), "run_id": _text(task.get("run_id")), "outcome": outcome, "result": words,
            "took_s": _took(task), "compute": _compute(task), "notebook": _text(notebook, 500),
            "error": redact.secrets(_text(error, _ERROR_MAX))}


def update_row(pipeline: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    update = _object(update, "a pipeline update")
    state = str(update.get("state") or "")
    words = UPDATE_TEXT.get(state, _text(state.title()) or "Unknown")
    outcome = {"COMPLETED": "ok", "FAILED": "failed", "CANCELED": "other"}.get(state, "running")
    return {"kind": "pipeline", "id": _text(update.get("update_id")), "pipeline_id": _text(pipeline.get("pipeline_id")),
            "name": _text(pipeline.get("name")) or _text(pipeline.get("pipeline_id")), "started": _ms(update.get("creation_time")),
            "took_s": 0, "outcome": outcome, "result": words,
            "trigger": "Full refresh" if update.get("full_refresh") else _text(str(update.get("cause") or "").title()),
            "compute": "Serverless" if pipeline.get("serverless") else "Pipeline cluster",
            "creator": _text(pipeline.get("creator_user_name")), "url": ""}


class Runs:
    """``client`` is databricks_sql.Client (tests pass one over a fake HTTP session)."""

    def __init__(self, client: Client, clock: Callable[[], float] | None = None):
        self.client = client
        self.clock = clock or (lambda: time.time())

    def _guard(self, read: Callable[[], Any]) -> dict[str, Any]:
        try:
            return {"ok": True, "value": read()}
        except DatabricksAuthError:
            raise
        except DatabricksError as exc:
            return {"ok": False, "error": redact.secrets(str(exc))}
        except (OddReply, TypeError, AttributeError) as exc:
            why = str(exc) if isinstance(exc, OddReply) else "a value isn't the type the API documents"
            return {"ok": False, "error": f"Databricks answered in a shape this screen doesn't know ({why})."}

    def _jobs(self, since_ms: int, mine: str) -> list[dict[str, Any]]:
        params = {"start_time_from": str(since_ms), "expand_tasks": "true", "limit": "25"}
        rows = [self._own_url(run_row(r)) for r in self.client.list("/api/2.1/jobs/runs/list", "runs", params)[:RUNS_LIMIT]]
        return [r for r in rows if not mine or r["creator"].lower() == mine.lower()]

    def _own_url(self, row: dict[str, Any]) -> dict[str, Any]:
        """The run's page only when it is on this workspace: the app opens it in the browser."""
        if not row["url"].startswith(self.client.host.rstrip("/") + "/"):
            row["url"] = ""
        return row

    def _pipelines(self, since_ms: int, mine: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        found = [_object(p, "a pipeline") for p in self.client.list("/api/2.0/pipelines", "statuses", {})][:PIPELINES_LIMIT]
        for p in found:
            if mine and str(p.get("creator_user_name") or "").lower() != mine.lower():
                continue
            pid = str(p.get("pipeline_id") or "")
            if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", pid):
                raise OddReply("a pipeline id isn't one Databricks gives")
            updates = self.client.get(f"/api/2.0/pipelines/{pid}/updates", {"max_results": str(UPDATES_PER_PIPELINE)})
            out += [update_row(p, u) for u in _object(updates, "pipeline updates").get("updates") or []
                    if _ms(_object(u, "a pipeline update").get("creation_time")) >= since_ms]
        return out

    def list(self, days: int = 1, mine: str = "") -> dict[str, Any]:
        """Job runs and pipeline updates of the last ``days`` days, newest first; ``mine`` (a user
        name) keeps only that person's. A sign-in failure stops both lists with one message."""
        days = min(max(int(days), 1), 30)
        since = int((self.clock() - days * 86400) * 1000)
        try:
            jobs = self._guard(lambda: self._jobs(since, mine))
            pipelines = self._guard(lambda: self._pipelines(since, mine))
        except DatabricksAuthError as exc:
            message = redact.secrets(str(exc))
            return {"schema": SCHEMA, "host": self.client.host, "signed_in": False, "error": message,
                    "jobs": {"ok": False, "error": message}, "pipelines": {"ok": False, "error": message}, "rows": []}
        rows = (jobs.get("value") or []) + (pipelines.get("value") or [])
        rows.sort(key=lambda r: r["started"], reverse=True)
        return {"schema": SCHEMA, "host": self.client.host, "signed_in": True, "days": days,
                "jobs": {k: v for k, v in jobs.items() if k != "value"}, "pipelines": {k: v for k, v in pipelines.items() if k != "value"},
                "rows": rows}

    def run(self, run_id: str) -> dict[str, Any]:
        """One job run with its tasks, the failed ones' errors and notebooks."""
        if not _RUN_ID.fullmatch(str(run_id)):
            raise ValueError("A run id is a number.")
        run = self.client.get("/api/2.1/jobs/runs/get", {"run_id": str(run_id)})
        row = self._own_url(run_row(run))
        return row | {"tasks": [task_row(t) for t in run.get("tasks") or []],
                      "parameters": {_text(p.get("name")): _text(p.get("value"))
                                     for p in (run.get("job_parameters") or []) if isinstance(p, dict)}}
