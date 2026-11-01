"""Managed SQL query page runs (SQL-003): a queue, Stop, time limits, paging and export.

Each run gets an id and moves through these states:

    queued -> running -> completed | failed | timed_out
                      -> cancelling -> cancelled        (Stop)
    queued -> cancelled                                 (Stop before it started)

A run is a Python process of its own (sql_worker.py) with a DuckDB memory limit, a temp folder and
external access locked off. Stop and the time limit end that process and nothing else: no
notebook kernel, no other run. At most ``sql_max_concurrent`` run at once; the rest wait queued.

The worker streams the last statement's result into an Arrow IPC file, the run's snapshot. Pages
are read from it by batch (memory mapped), so a 10 million row result is never held in memory and
never counted with COUNT(*): the row count is what the worker wrote. Exports stream the snapshot
to CSV or Parquet in the Downloads folder: written to a ``.partial`` file, never over an existing
file, and removed again when stopped or failed. A snapshot that was cleaned up can't be exported;
the page asks before running the query again.

Snapshots live under ``sql_temp_dir`` (default ``<data>/sql-runs``) and are deleted after
``sql_result_retention_hours``, when more than MAX_KEPT runs are kept, and at start-up for runs
from an earlier session that are past retention.

A run's target is a lakehouse (above) or, with ``target="local"``, a local data project
(KF-DE-027). A local run doesn't get a process of its own: it runs in the project's own DuckDB
database through LocalEngine.execute_reader, on a thread here. The project's database is a file
only one process can open for writing, and the engine already holds it, so a worker process
couldn't open it; the engine also already stops a query cleanly with con.interrupt() for Stop and
for the time limit. The run otherwise has the same states, queue, snapshot, paging and export.
Its statements are classified for the local target first (sql_policy.py): writes to the project's
own tables run, DROP, TRUNCATE and DELETE or UPDATE without WHERE run only when started again with
``confirm``, and COPY, ATTACH, INSTALL and the like never run.

A run with ``target="warehouse"`` (SQL-004, Preview) runs one T-SQL query that only reads on a Fabric
Data warehouse or a lakehouse's SQL analytics endpoint, in Fabric, so it uses capacity. It has a
process of its own like a lakehouse run (Stop and the time limit end it, which closes the
connection), and asks the engine for an Azure SQL token. warehouse.py says what it checks first.

A warehouse **change** (WH-10) is a warehouse run with ``allow=["warehouse_write"]``, ``confirm``
and ``confirm_name``: the page showed what the script changes and the person confirmed it, naming
the warehouse. The engine checks that name against the one Fabric gives for the item before it
connects, so a change never reaches a warehouse the person didn't name, and refuses every change
while the ``sql_warehouse_writes`` setting is off (Settings › SQL, or the organization's policy).
Its batches run in one transaction (warehouse.py). Its status adds ``affected`` (rows changed) and
``committed``.

``dry_run`` classifies the statements and says what would run, and what needs confirming, without
running anything, for every target.

Each finished run adds one ``sql_execution`` line to the audit trail: its id, dialect, lakehouse,
state, permission class, rows and seconds (and target and project for a local run, or the
warehouse for a warehouse run). Never the SQL, the error text, the token or any data.
"""

from __future__ import annotations

import bisect
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
from sqlglot import exp

from . import app_settings, jsonfile, sql, sql_policy, warehouse
from .filenames import file_stem

STATES = ("queued", "running", "cancelling", "completed", "failed", "cancelled", "timed_out")
FINISHED = ("completed", "failed", "cancelled", "timed_out")
MAX_KEPT = 20  # finished runs whose snapshots are kept, newest first
EXPORT_FORMATS = ("csv", "parquet")
EXPORT_BATCH_ROWS = 65_536
LOCAL_BATCH_ROWS = 65_536
# What each target may ever allow. A local run allows all of its classes unless the caller narrows it.
TARGET_ALLOWS = {"lakehouse": ("read_only", "local_mutation"),
                 "local": ("read_only", "local_mutation", "local_project_write"),
                 "warehouse": ("read_only", "warehouse_write")}
LOCAL_DIALECTS = ("duckdb", "spark", "tsql")


def memory_mb(percent: int) -> int:
    """``percent`` of this PC's memory in MB (8 GB assumed when it can't be read), at least 256."""
    from .side_by_side import _memory_gb
    total = _memory_gb()
    return max(256, int((total or 8) * 1024 * percent / 100))


def run_threads(limits: dict[str, Any]) -> int:
    """DuckDB threads for one run: this PC's cores shared by the runs allowed at once."""
    return max(1, (os.cpu_count() or 2) // max(1, int(limits["sql_max_concurrent"])))


class RerunNeeded(Exception):
    """The run's snapshot is gone; the query has to run again."""


@dataclass
class Execution:
    id: str
    query: str = field(repr=False)
    dialect: str
    lakehouse: dict[str, Any]
    allow: tuple[str, ...]
    folder: Path
    created: float = field(default_factory=time.time)
    started: float | None = None
    ended: float | None = None
    state: str = "queued"
    rows: int = 0
    batches: list[int] = field(default_factory=list)
    offsets: list[int] = field(default_factory=list)  # first row of each batch
    columns: list[dict[str, str]] = field(default_factory=list)
    permission: str = ""
    error: str = ""
    error_kind: str = ""
    refused: dict[str, Any] | None = None
    proc: subprocess.Popen[str] | None = field(default=None, repr=False)
    timer: threading.Timer | None = field(default=None, repr=False)
    stop_reason: str = ""  # "cancel" or "timeout" once the engine ended the process
    time_limit_s: float = 0.0
    time_limit_from: str = ""  # local target: "sql" (Settings › SQL), "caller" or "project" (its own setting)
    target: str = "lakehouse"  # or "local": a local data project's database
    project: str = ""  # local target: the project's id
    project_name: str = ""
    confirm: bool = False  # local target: DROP, TRUNCATE, DELETE/UPDATE without WHERE may run
    timeout_s: float | None = None  # local target: a caller's own time limit, in seconds
    warehouse: dict[str, Any] = field(default_factory=dict)  # warehouse target: the item, its server and database
    confirm_name: str = ""  # warehouse change: the warehouse the person confirmed, checked against Fabric's name
    affected: int = 0  # warehouse change: rows its statements changed
    plan: bool = False  # warehouse target: the estimated plan, not the rows (WH-09)
    precondition: dict[str, Any] | None = None  # a deploy change: the object as its preview saw it (#947)
    committed: bool = False

    @property
    def snapshot(self) -> Path:
        return self.folder / "result.arrow"

    def status(self) -> dict[str, Any]:
        """What the page shows: never the SQL or the token."""
        end = self.ended or time.time()
        return {"id": self.id, "state": self.state, "target": self.target, "dialect": self.dialect,
                "lakehouse": self.lakehouse.get("name", ""), "project": self.project_name,
                "warehouse": self.warehouse.get("database", ""),
                "created": self.created, "started": self.started, "ended": self.ended,
                "seconds": round(end - self.started, 2) if self.started else 0.0,
                "rows": self.rows, "columns": self.columns, "permission": self.permission,
                "error": self.error, "error_kind": self.error_kind, "refused": self.refused,
                "time_limit_minutes": round(self.time_limit_s / 60, 2),
                **({"time_limit_from": self.time_limit_from} if self.target == "local" else {}),
                **({"change": True, "affected": self.affected, "committed": self.committed}
                   if "warehouse_write" in self.allow else {}),
                **({"plan": True} if self.plan else {}),
                "snapshot": self.state == "completed" and self.snapshot.exists()}


@dataclass
class Export:
    id: str
    execution: str
    format: str
    path: Path
    state: str = "running"  # running, completed, failed, cancelled
    rows: int = 0
    total: int = 0
    error: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)

    def status(self) -> dict[str, Any]:
        return {"id": self.id, "execution": self.execution, "format": self.format, "path": str(self.path),
                "state": self.state, "rows": self.rows, "total": self.total, "error": self.error}


class SqlExecutions:
    """The SQL page's runs on this PC. One per engine (Service.sql_runs)."""

    def __init__(self, root: Path, token: Callable[[], str], audit: Callable[..., None] | None = None,
                 limits: Callable[[], dict[str, Any]] = app_settings.sql_limits,
                 command: list[str] | None = None, downloads: Callable[[], Path] | None = None,
                 schemas_enabled: Callable[[dict[str, Any]], bool] = lambda _ref: True, test_lake: Path | None = None,
                 local_engine: Callable[[str], Any] | None = None,
                 warehouse_token: Callable[[], str] | None = None,
                 resolve_warehouse: Callable[[dict[str, Any]], warehouse.Endpoint] | None = None):
        self.root = Path(root)
        self._warehouse_token = warehouse_token
        self._resolve_warehouse = resolve_warehouse  # a warehouse ref -> its server and database, from Fabric
        self._local_engine = local_engine  # project id or name -> its LocalEngine (localdata/)
        self._token = token
        self._schemas_enabled = schemas_enabled
        self._audit = audit
        self._limits = limits
        self.command = command or [sys.executable, "-m", "fabric_desktop.sql_worker"]
        self._downloads = downloads or (lambda: Path.home() / "Downloads")
        self._lock = threading.RLock()
        self._runs: dict[str, Execution] = {}
        self._exports: dict[str, Export] = {}
        self._order = itertools.count()
        self._cleaned_at_start = False
        # Tests only, never set by a call: read the lakehouse's tables from local Delta folders here.
        self.test_lake = test_lake

    # ---- folders and limits

    def base(self) -> Path:
        configured = str(self._limits().get("sql_temp_dir") or "")
        return Path(configured) if configured else self.root / "sql-runs"

    def _memory_mb(self, percent: int) -> int:
        return memory_mb(percent)

    # ---- starting and stopping

    def start(self, query: str, lakehouse_ref: dict[str, Any] | None = None, dialect: str = "spark",
              allow: list[str] | None = None, target: str = "lakehouse", project: str = "",
              confirm: bool = False, dry_run: bool = False, timeout: float | None = None,
              warehouse_ref: dict[str, Any] | None = None, confirm_name: str = "",
              estimated_plan: bool = False, precondition: dict[str, Any] | None = None) -> dict[str, Any]:
        """Queue a run. ``target="local"`` runs it in local data project ``project`` instead of
        against ``lakehouse_ref``; ``confirm`` lets its DROP, TRUNCATE and DELETE or UPDATE without
        WHERE run; ``timeout`` (seconds, local only) replaces the SQL page's time limit.

        ``target="warehouse"`` runs one T-SQL query that reads on ``warehouse_ref``
        (``workspace_id``, ``item_id``, ``kind`` "warehouse" or "endpoint"), in Fabric. With
        ``allow=["warehouse_write"]`` it is a change: it runs only with ``confirm`` and
        ``confirm_name``, the warehouse's name as the person confirmed it. With ``estimated_plan``
        (WH-09) a query that reads isn't run: Fabric returns how it would run it, as showplan XML. A change
        with ``precondition`` (warehouse.precondition_problem) runs only if that object is still as a
        deploy's preview saw it; otherwise nothing is sent (#947)."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("The query is empty.")
        if target not in TARGET_ALLOWS:
            raise ValueError(f"target must be one of {tuple(TARGET_ALLOWS)}, got {target!r}")
        engine = None
        if target == "warehouse":
            if dialect != "tsql":
                raise ValueError("A warehouse runs T-SQL: pick T-SQL as the dialect.")
            if not isinstance(warehouse_ref, dict) or warehouse_ref.get("kind") not in warehouse.KINDS or not all(
                    isinstance(warehouse_ref.get(k), str) and warehouse_ref[k] for k in ("workspace_id", "item_id")):
                raise ValueError("Pick a warehouse or SQL analytics endpoint to run the query on.")
        elif target == "lakehouse":
            if not isinstance(lakehouse_ref, dict) or not all(isinstance(lakehouse_ref.get(k), str) and lakehouse_ref[k]
                                                              for k in ("workspace_id", "lakehouse_id", "name")):
                raise ValueError("Pick a lakehouse to run the query against.")
        else:
            if not isinstance(project, str) or not project:
                raise ValueError("Pick a local data project to run the query in.")
            if dialect not in LOCAL_DIALECTS:
                raise ValueError(f"A local data project runs {' or '.join(LOCAL_DIALECTS)} SQL, not {dialect!r}.")
            if self._local_engine is None:
                raise ValueError("Local data projects aren't available here.")
            engine = self._local_engine(project)  # KeyError for a project that doesn't exist
        permitted = TARGET_ALLOWS[target]
        allowed = tuple(dict.fromkeys(["read_only", *(permitted[1:] if allow is None and target == "local" else allow or [])]))
        if any(a not in permitted for a in allowed):
            # OneLake writes and Fabric changes are never allowed from the SQL page, whoever asks.
            if target == "local":
                raise PermissionError("A local data project run can read and change the project's own tables, "
                                      "never files elsewhere on this PC, extensions or Fabric.")
            if target == "warehouse":
                raise PermissionError("A warehouse run reads, or changes the warehouse after you confirm it; "
                                      "nothing else is allowed.")
            raise PermissionError("The SQL page can only read tables and change a run's own session on this PC.")
        change = target == "warehouse" and "warehouse_write" in allowed
        if estimated_plan and (target != "warehouse" or change):
            raise ValueError("An estimated plan is for a warehouse query that reads.")
        if precondition is not None:
            if not change:
                raise ValueError("A precondition is for a warehouse change.")
            problem = warehouse.precondition_problem(precondition)
            if problem:
                raise ValueError(f"The change wasn't sent: {problem}.")
        writes_setting = app_settings.load()["sql_warehouse_writes"] if change else {}
        if change and not writes_setting["value"]:
            raise PermissionError("Warehouse changes are turned off in Settings › SQL"
                                  + (" by your organization." if writes_setting["source"] == "policy" else "."))
        if change and not dry_run and not (confirm and isinstance(confirm_name, str) and confirm_name.strip()):
            raise PermissionError("A warehouse change runs only after you confirm it, naming the warehouse.")
        if dry_run:
            return self._plan(query, dialect, target, allowed, confirm)
        if not self._cleaned_at_start:
            self._cleaned_at_start = True
            self.cleanup(earlier_sessions=True)
        run_id = uuid.uuid4().hex[:12]
        folder = self.base() / run_id
        run = Execution(id=run_id, query=query, dialect=dialect, lakehouse=dict(lakehouse_ref or {}), allow=allowed,
                        folder=folder, target=target, confirm=bool(confirm),
                        timeout_s=None if timeout is None else float(timeout))
        if target == "warehouse":
            assert warehouse_ref is not None
            run.warehouse = {k: warehouse_ref[k] for k in ("workspace_id", "item_id", "kind")}
            run.confirm_name = confirm_name.strip() if change else ""
            run.plan = bool(estimated_plan)
            run.precondition = ({k: precondition[k] for k in warehouse.PRECONDITION_KEYS if k in precondition}
                                if precondition is not None else None)
            stop = sql_policy.refused(warehouse.verdicts(query, writes=change), allowed)  # before Fabric is asked anything
            if stop is not None:
                return self._refuse(run, stop)
            if self._resolve_warehouse is None:
                raise ValueError("Warehouse queries aren't available here.")
        if engine is not None:
            run.project, run.project_name = engine.project.id, engine.project.name
        with self._lock:
            self._runs[run_id] = run
        if target == "warehouse":
            # Finding the server asks Fabric, so it happens off this call (sql_start answers at once);
            # the run waits queued until it's known.
            threading.Thread(target=self._find_warehouse, args=(run,), daemon=True, name=f"sql-find-{run_id}").start()
            return run.status()
        self._pump()
        return run.status()

    def _find_warehouse(self, run: Execution) -> None:
        assert self._resolve_warehouse is not None
        try:
            endpoint = self._resolve_warehouse(run.warehouse)
        except Exception as exc:  # noqa: BLE001 - signed out, no access or not found: the run fails with why
            with self._lock:
                if run.state == "queued":
                    run.state, run.ended, run.error_kind = "failed", time.time(), "fabric"
                    run.error = f"Couldn't find the warehouse's SQL connection in Fabric: {exc}"
                    self._finished(run)
            return
        with self._lock:
            if run.confirm_name and run.confirm_name != endpoint.database:
                if run.state == "queued":
                    run.state, run.ended, run.error_kind = "failed", time.time(), "permission"
                    run.permission = "warehouse_write"
                    run.error = (f"You confirmed a change to {run.confirm_name}, but Fabric names this warehouse "
                                 f"{endpoint.database}, so nothing was sent. Check which warehouse the page is on.")
                    self._finished(run)
                return
            run.warehouse.update(server=endpoint.server, database=endpoint.database)
        self._pump()

    def _plan(self, query: str, dialect: str, target: str, allowed: tuple[str, ...], confirm: bool) -> dict[str, Any]:
        """A dry run: what each statement is and whether the run would be refused or need
        confirming. Nothing runs and no run is kept."""
        verdicts = (warehouse.verdicts(query, writes="warehouse_write" in allowed) if target == "warehouse"
                    else sql_policy.classify(_policy_statements(query, dialect), target))
        stop = sql_policy.refused(verdicts, allowed, bool(confirm))
        return {"dry_run": True, "target": target, "dialect": dialect,
                "permission": sql_policy.strongest(verdicts),
                "statements": [{"statement": v.statement, "permission": v.kind, "reason": v.reason,
                                "confirm": v.confirm} for v in verdicts],
                "confirm_required": bool(stop is not None and stop.kind in allowed and stop.confirm),
                "refused": None if stop is None else _refusal(stop)}

    def _refuse(self, run: Execution, stop: sql_policy.Verdict) -> dict[str, Any]:
        """A run refused before anything was sent: kept as failed, with why, like a worker's refusal."""
        run.state, run.error_kind, run.permission = "failed", "permission", stop.kind
        run.started = run.ended = time.time()
        run.error = (f"Statement {stop.statement} was refused ({stop.reason}), so nothing was sent. "
                     + ("A warehouse change can't do this." if "warehouse_write" in run.allow
                        else "A warehouse run only reads."))
        run.refused = {"permission": stop.kind, "statement": stop.statement, "label": sql_policy.LABELS.get(stop.kind, "")}
        with self._lock:
            self._runs[run.id] = run
            self._finished(run)
        return run.status()

    def cancel(self, run_id: str) -> dict[str, Any]:
        """Stop: a queued run never starts; a running one has its own process ended."""
        run = self._get(run_id)
        with self._lock:
            if run.state == "queued":
                run.state, run.ended = "cancelled", time.time()
                self._finished(run)
            elif run.state == "running":
                run.state, run.stop_reason = "cancelling", "cancel"
                self._end_process(run)
        return run.status()

    def status(self, run_id: str) -> dict[str, Any]:
        return self._get(run_id).status()

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda r: r.created, reverse=True)
        return [r.status() for r in runs]

    def shutdown(self) -> None:
        """The app is closing: end every run's process."""
        with self._lock:
            procs = []
            for run in self._runs.values():
                if run.state in ("running", "cancelling"):
                    run.stop_reason = run.stop_reason or "cancel"
                    self._end_process(run)
                    if run.proc is not None:
                        procs.append(run.proc)
            for export in self._exports.values():
                export.cancel.set()
        for proc in procs:
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                pass

    def _get(self, run_id: str) -> Execution:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"No SQL run {run_id!r} on this PC. It may have been cleaned up; run the query again.")
        return run

    def _pump(self) -> None:
        """Start queued runs while fewer than sql_max_concurrent are running."""
        limits = self._limits()
        with self._lock:
            active = sum(r.state in ("running", "cancelling") for r in self._runs.values())
            waiting = sorted((r for r in self._runs.values() if r.state == "queued"
                              and (r.target != "warehouse" or "server" in r.warehouse)), key=lambda r: r.created)
            for run in waiting[:max(0, int(limits["sql_max_concurrent"]) - active)]:
                self._launch(run, limits)

    def _launch(self, run: Execution, limits: dict[str, Any]) -> None:
        run.folder.mkdir(parents=True, exist_ok=True)
        if run.target == "local":
            self._launch_local(run, limits)
            return
        lh = run.lakehouse
        minutes = float(limits["sql_time_limit_minutes"] or 0)
        if run.target == "warehouse":
            job: dict[str, Any] = {"target": "warehouse", "query": run.query, "out": str(run.snapshot),
                                   "warehouse": {k: run.warehouse[k] for k in ("server", "database")},
                                   "time_limit_s": minutes * 60, "writes": "warehouse_write" in run.allow,
                                   "plan": run.plan}
            if run.precondition:
                job["precondition"] = run.precondition
        else:
            job = {"query": run.query, "dialect": run.dialect, "allow": list(run.allow),
                   "lakehouse": {k: lh[k] for k in ("workspace_id", "lakehouse_id", "name", "workspace_name") if k in lh},
                   "work_dir": str(self.root), "out": str(run.snapshot), "temp_dir": str(run.folder / "spill"),
                   "memory_mb": self._memory_mb(int(limits["sql_memory_limit_percent"])),
                   "threads": run_threads(limits)}
        if self.test_lake and run.target == "lakehouse":  # tests only: local Delta folders instead of OneLake
            job["lakehouse"]["schemas_enabled"] = False
            job["local_root"] = str(self.test_lake)
        run.state, run.started = "running", time.time()
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", bufsize=1, creationflags=flags,
                                    env=dict(os.environ, PYTHONUNBUFFERED="1"))
        except Exception as exc:  # noqa: BLE001 - signed out, or Python couldn't start
            run.state, run.ended, run.error_kind = "failed", time.time(), "engine"
            run.error = str(exc) or type(exc).__name__
            self._finished(run)
            return
        run.proc = proc
        assert proc.stdin is not None
        try:
            # stdin stays open: the worker ends itself when it closes, so a run never outlives the engine.
            proc.stdin.write(json.dumps(job) + "\n")
            proc.stdin.flush()
        except OSError:
            pass  # the reader sees the process end and reports it
        run.time_limit_s = minutes * 60
        if minutes > 0:
            run.timer = threading.Timer(minutes * 60, self._time_up, args=(run,))
            run.timer.daemon = True
            run.timer.start()
        threading.Thread(target=self._read, args=(run, proc), daemon=True, name=f"sql-run-{run.id}").start()

    def _answer(self, run: Execution, proc: subprocess.Popen[str], name: str) -> None:
        """The worker is about to read a lakehouse table and needs a storage token, or the
        lakehouse's layout: answer over its stdin."""
        try:
            if name == "token":
                reply: dict[str, Any] = {"value": self._token()}
            elif name == "warehouse_token" and run.target == "warehouse" and self._warehouse_token is not None:
                reply = {"value": self._warehouse_token()}
            elif name == "schemas_enabled":
                reply = {"value": self._schemas_enabled(run.lakehouse)}
            else:
                reply = {"error": f"Unknown question {name!r}"}
        except Exception as exc:  # noqa: BLE001 - signed out or sign-in cancelled: the run fails with why
            what = "query the warehouse" if run.target == "warehouse" else "read lakehouse tables"
            reply = {"error": f"Sign in to {what} ({type(exc).__name__}: {exc})"}
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(reply) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            pass  # the run already ended

    def _time_up(self, run: Execution) -> None:
        with self._lock:
            if run.state == "running":
                run.stop_reason = "timeout"
                self._end_process(run)

    def _end_process(self, run: Execution) -> None:
        """End this run's own process: the Popen this manager started for it, nothing else. A local
        run has no process: its query is interrupted in the project's database (con.interrupt())."""
        if run.target == "local":
            if self._local_engine is not None:
                # not_started: the run's thread may not have reached the engine yet; it stops as it starts.
                self._local_engine(run.project).cancel(run.id, not_started=True)
            return
        proc = run.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def _read(self, run: Execution, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        outcome: dict[str, Any] = {}
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("event")
            if kind == "call":
                threading.Thread(target=self._answer, args=(run, proc, str(event.get("name"))), daemon=True,
                                 name="sql-call").start()
                continue
            with self._lock:
                if kind == "started":
                    run.permission = str(event.get("permission") or "")
                elif kind == "progress":
                    run.rows = int(event.get("rows") or 0)
                elif kind in ("done", "error"):
                    outcome = event
        code = proc.wait()
        try:
            assert proc.stdin is not None
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        with self._lock:
            if run.timer is not None:
                run.timer.cancel()
            run.ended = time.time()
            run.proc = None
            if run.stop_reason == "timeout":
                run.state, run.error_kind = "timed_out", "timeout"
                run.error = f"Stopped after the time limit ({run.time_limit_s / 60:g} minutes, Settings › SQL)."
            elif run.stop_reason == "cancel":
                run.state, run.error = "cancelled", "Stopped."
            elif outcome.get("event") == "done":
                run.state = "completed"
                run.rows = int(outcome.get("rows") or 0)
                run.batches = [int(n) for n in outcome.get("batches") or []]
                run.offsets = list(itertools.accumulate([0, *run.batches[:-1]])) if run.batches else []
                run.columns = list(outcome.get("columns") or [])
                run.affected, run.committed = int(outcome.get("affected") or 0), bool(outcome.get("committed"))
            elif outcome.get("event") == "error":
                run.state, run.error_kind = "failed", str(outcome.get("kind") or "sql")
                run.error = str(outcome.get("message") or "The query failed.")
                if run.error_kind == "permission":
                    run.permission = str(outcome.get("permission") or "")
                    run.refused = {"permission": run.permission, "statement": outcome.get("statement"),
                                   "label": sql_policy.LABELS.get(run.permission, "")}
            else:
                run.state, run.error_kind = "failed", "engine"
                run.error = (f"The query's process stopped unexpectedly (exit code {code}). It may have run out of "
                             "memory: lower the result size, or raise the memory share in Settings › SQL.")
            if run.state != "completed":
                shutil.rmtree(run.folder, ignore_errors=True)
            self._finished(run)
        self._pump()

    # ---- local data project runs (target="local")

    def _launch_local(self, run: Execution, limits: dict[str, Any]) -> None:
        minutes = float(limits["sql_time_limit_minutes"] or 0)
        if run.timeout_s is not None:
            run.time_limit_s, run.time_limit_from = run.timeout_s, "caller"
        elif minutes:
            run.time_limit_s, run.time_limit_from = minutes * 60, "sql"
        else:  # the engine applies the project's own limit; say so in the status
            try:
                seconds = float(self._local_engine(run.project).project.settings.timeout_seconds or 0)  # type: ignore[misc]
            except KeyError:  # the project went away; _run_local reports it
                seconds = 0.0
            run.time_limit_s, run.time_limit_from = seconds, "project" if seconds else ""
        run.state, run.started = "running", time.time()
        threading.Thread(target=self._run_local, args=(run,), daemon=True, name=f"sql-run-{run.id}").start()

    def _run_local(self, run: Execution) -> None:
        """Run in the project's own database, streaming the last statement's result to the
        snapshot as the worker does. The engine's own timer and cancel stop it with con.interrupt()."""
        from .localdata import EngineBusy, QueryCancelled
        state, kind, error, refused = "completed", "", "", None
        rows, batches, columns = 0, [], []
        try:
            verdicts = sql_policy.classify(_policy_statements(run.query, run.dialect), "local")
            stop = sql_policy.refused(verdicts, run.allow, run.confirm)
            if stop is not None:
                refused = _refusal(stop)
                state, kind = "failed", "permission"
                error = local_refusal_message(stop, run.allow)
            else:
                with self._lock:
                    run.permission = sql_policy.strongest(verdicts)
                engine = self._local_engine(run.project)  # type: ignore[misc]
                with engine.execute_reader(run.query, dialect=run.dialect, timeout=run.time_limit_s or None,
                                           query_id=run.id, batch_size=LOCAL_BATCH_ROWS) as stream:
                    with pa.ipc.new_file(str(run.snapshot), stream.schema) as writer:
                        for batch in stream:
                            if not batch.num_rows:
                                continue
                            writer.write_batch(batch)
                            rows += batch.num_rows
                            batches.append(batch.num_rows)
                            run.rows = rows
                    columns = [{"name": f.name, "type": str(f.type)} for f in stream.schema]
        except QueryCancelled:
            state, error = "cancelled", "Stopped."
        except TimeoutError as exc:
            state, kind = "timed_out", "timeout"
            error = {"sql": f"Stopped after the time limit ({run.time_limit_s / 60:g} minutes, Settings › SQL).",
                     "project": f"Stopped after the project's time limit ({run.time_limit_s / 60:g} minutes, "
                                "in the project's settings)."}.get(run.time_limit_from, str(exc))
        except EngineBusy as exc:
            state, kind, error = "failed", "engine", str(exc)
        except Exception as exc:  # noqa: BLE001 - the SQL's own error, shown in place of the rows
            state, kind, error = "failed", "sql", (str(exc).strip() or type(exc).__name__)[:4000]
        with self._lock:
            run.ended = time.time()
            run.state, run.error_kind, run.error, run.refused = state, kind, error, refused
            if refused is not None:
                run.permission = refused["permission"]
            if state == "completed":
                run.rows, run.batches, run.columns = rows, batches, columns
                run.offsets = list(itertools.accumulate([0, *batches[:-1]])) if batches else []
            else:
                shutil.rmtree(run.folder, ignore_errors=True)
            self._finished(run)
        self._pump()

    def _finished(self, run: Execution) -> None:
        if self._audit is not None:
            lh = run.lakehouse
            try:
                self._audit("sql_execution", "ok" if run.state == "completed" else "error",
                            (run.ended or time.time()) - (run.started or run.created),
                            {"execution": run.id, "state": run.state, "dialect": run.dialect,
                             "permission": run.permission or "", "rows": run.rows, "target": run.target,
                             "workspace_id": lh.get("workspace_id", ""), "lakehouse_id": lh.get("lakehouse_id", ""),
                             "lakehouse": lh.get("name", ""), "project": run.project_name,
                             **({"workspace_id": run.warehouse.get("workspace_id", ""),
                                 "warehouse_id": run.warehouse.get("item_id", ""),
                                 "warehouse": run.warehouse.get("database", ""),
                                 **({"plan": True} if run.plan else {}),
                                 **({"affected": run.affected, "committed": run.committed}
                                    if "warehouse_write" in run.allow else {})} if run.target == "warehouse" else {})})
            except Exception as exc:  # noqa: BLE001 - an audit problem never fails the run
                print(f"audit: could not record sql_execution: {type(exc).__name__}", file=sys.stderr)
        run.query = ""  # the SQL isn't kept once the run is over
        self.cleanup()

    # ---- paging

    def page(self, run_id: str, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        """Rows ``offset`` to ``offset + limit`` of a completed run's result, read from its snapshot."""
        run = self._get(run_id)
        if run.state != "completed":
            raise ValueError(f"The query is {run.state.replace('_', ' ')}, so there are no rows to show.")
        if not run.snapshot.exists():
            raise RerunNeeded("This result was cleaned up. Run the query again to see it.")
        limit = int(limit or self._limits()["sql_page_rows"])
        limit = max(1, min(limit, app_settings.SQL_RANGES["sql_page_rows"][1]))
        offset = max(0, min(int(offset), max(0, run.rows - 1)))
        rows: list[list[Any]] = []
        if run.rows:
            with pa.memory_map(str(run.snapshot)) as source:
                reader = pa.ipc.open_file(source)
                i = bisect.bisect_right(run.offsets, offset) - 1
                need, skip = limit, offset - run.offsets[i]
                while need > 0 and i < reader.num_record_batches:
                    batch = reader.get_batch(i).slice(skip, need)
                    columns = [c.to_pylist() for c in batch.columns]  # by position: names can repeat
                    rows.extend([list(r) for r in zip(*columns)])
                    need -= batch.num_rows
                    skip, i = 0, i + 1
        return {"id": run.id, "offset": offset, "limit": limit, "rows": [[_json_value(v) for v in r] for r in rows],
                "columns": run.columns, "total": run.rows, "has_previous": offset > 0,
                "has_next": offset + len(rows) < run.rows}

    def query_of(self, run_id: str) -> str:
        """The SQL a run ran, for the engine's own checks (GD-06); never sent to the page."""
        return self._get(run_id).query

    def table(self, run_id: str) -> tuple[dict[str, Any], pa.Table]:
        """A completed run's status and its whole result, from its snapshot (Compare, GD-02). Columns
        with the same name get the export's unique names."""
        run = self._get(run_id)
        if run.state != "completed":
            raise ValueError(f"The query is {run.state.replace('_', ' ')}, so there are no rows to compare.")
        if not run.snapshot.exists():
            raise RerunNeeded("This result was cleaned up. Run the query again to compare it.")
        with pa.memory_map(str(run.snapshot)) as source:
            table = pa.ipc.open_file(source).read_all()
        return run.status(), table.rename_columns(_unique_names(table.schema).names)

    # ---- export

    def export(self, run_id: str, fmt: str = "csv", name: str = "") -> dict[str, Any]:
        """Stream a completed run's snapshot to a new file in Downloads; never replaces a file."""
        if fmt not in EXPORT_FORMATS:
            raise ValueError(f"Export as {' or '.join(EXPORT_FORMATS)}.")
        run = self._get(run_id)
        if run.state != "completed":
            raise ValueError(f"The query is {run.state.replace('_', ' ')}, so there is no result to export.")
        if not run.snapshot.exists():
            return {"rerun_needed": True, "message": "This result was cleaned up. Run the query again to export it."}
        folder = self._downloads()
        folder.mkdir(parents=True, exist_ok=True)
        base = f"{file_stem(name or run.lakehouse.get('name') or run.project_name or '')} query {time.strftime('%Y-%m-%d %H%M%S')}"
        path = _reserve(folder, base, "." + fmt)
        export = Export(id=uuid.uuid4().hex[:12], execution=run.id, format=fmt, path=path, total=run.rows)
        with self._lock:
            self._exports[export.id] = export
        threading.Thread(target=self._write, args=(run, export), daemon=True, name=f"sql-export-{export.id}").start()
        return export.status()

    def export_status(self, export_id: str) -> dict[str, Any]:
        return self._get_export(export_id).status()

    def export_cancel(self, export_id: str) -> dict[str, Any]:
        export = self._get_export(export_id)
        export.cancel.set()
        return export.status()

    def _get_export(self, export_id: str) -> Export:
        with self._lock:
            export = self._exports.get(export_id)
        if export is None:
            raise KeyError(f"No export {export_id!r}.")
        return export

    def _write(self, run: Execution, export: Export) -> None:
        partial = export.path.with_name(export.path.name + ".partial")
        try:
            with pa.memory_map(str(run.snapshot)) as source:
                reader = pa.ipc.open_file(source)
                schema = reader.schema
                if export.format == "parquet":
                    schema = _unique_names(schema)
                with open(partial, "xb") as sink:
                    writer = _writer(export.format, sink, schema)
                    try:
                        for i in range(reader.num_record_batches):
                            if export.cancel.is_set():
                                raise _Stopped()
                            batch = reader.get_batch(i)
                            if export.format == "parquet":
                                batch = pa.RecordBatch.from_arrays(batch.columns, schema=schema)
                            writer.write_batch(batch)
                            export.rows += batch.num_rows
                    finally:
                        writer.close()
            if export.cancel.is_set():
                raise _Stopped()
            jsonfile.replace(partial, export.path)  # over the empty file that reserved the name
            export.state = "completed"
        except _Stopped:
            _discard(partial, export.path)  # before the state changes: a finished export leaves no files
            export.state = "cancelled"
        except Exception as exc:  # noqa: BLE001 - shown on the page
            _discard(partial, export.path)
            export.state, export.error = "failed", str(exc) or type(exc).__name__

    # ---- clean-up

    def cleanup(self, earlier_sessions: bool = False) -> None:
        """Delete snapshots past retention, beyond MAX_KEPT, and (at start) left by an earlier session."""
        hours = float(self._limits()["sql_result_retention_hours"])
        now = time.time()
        with self._lock:
            done = sorted((r for r in self._runs.values() if r.state in FINISHED), key=lambda r: r.ended or 0, reverse=True)
            busy = {e.execution for e in self._exports.values() if e.state == "running"}
            for n, run in enumerate(done):
                old = hours > 0 and now - (run.ended or now) > hours * 3600
                if (old or n >= MAX_KEPT) and run.id not in busy:
                    shutil.rmtree(run.folder, ignore_errors=True)
                    if n >= MAX_KEPT:
                        del self._runs[run.id]
            known = {r.folder.name for r in self._runs.values()}
        if earlier_sessions:
            base = self.base()
            for folder in base.glob("*") if base.is_dir() else []:
                if folder.is_dir() and folder.name not in known and len(folder.name) == 12:
                    age = now - folder.stat().st_mtime
                    if hours <= 0 or age > hours * 3600:
                        shutil.rmtree(folder, ignore_errors=True)


class _Stopped(Exception):
    pass


_OUTSIDE_COMMAND = re.compile(r"(EXPORT|IMPORT)\s+DATABASE\b", re.IGNORECASE)


def _outside_command(query: str) -> str:
    """EXPORT or IMPORT when a statement of ``query`` starts with EXPORT/IMPORT DATABASE (after any
    comments), else ''. One pass over the text that skips strings and comments, so it stays linear."""
    i, n, start = 0, len(query), True
    while i < n:
        if start:  # skip whitespace and comments before the statement's first word
            while i < n:
                if query[i].isspace():
                    i += 1
                elif query.startswith("--", i):
                    i = n if (j := query.find("\n", i)) < 0 else j + 1
                elif query.startswith("/*", i):
                    i = n if (j := query.find("*/", i + 2)) < 0 else j + 2
                else:
                    break
            found = _OUTSIDE_COMMAND.match(query, i)
            if found:
                return found.group(1).upper()
            start = False
            continue
        ch = query[i]
        if ch in "'\"":
            i = n if (j := query.find(ch, i + 1)) < 0 else j + 1  # '' inside a string is two strings back to back
        elif query.startswith("--", i):
            i = n if (j := query.find("\n", i)) < 0 else j + 1
        elif query.startswith("/*", i):
            i = n if (j := query.find("*/", i + 2)) < 0 else j + 2
        else:
            start = ch == ";"
            i += 1
    return ""


def _policy_statements(query: str, dialect: str) -> list[Any]:
    """The statements the policy classes. T-SQL (local target only) is classed as the DuckDB SQL it
    is translated to, which is what runs."""
    if dialect != "tsql":
        try:
            return sql.parse(query, dialect)
        except ValueError:
            # sqlglot can't read DuckDB's EXPORT/IMPORT DATABASE; class it as the command it is,
            # so it is refused as a write outside the project, not reported as a SQL error.
            word = _outside_command(query)
            if word:
                return [exp.Command(this=word, expression=exp.Literal.string(""))]
            raise
    from .localdata import tsql
    texts, _notes = tsql.translate(query)
    return [st for text in texts for st in sql.parse(text, "duckdb")]


def check_local(query: str, dialect: str, confirm: bool = False) -> tuple[list[sql_policy.Verdict], sql_policy.Verdict | None]:
    """A local data project query's statements, classed, and the first that may not run, or None.
    The same check a managed local run makes (``target="local"``), for callers that run a query
    in the project's engine themselves (``local_query``)."""
    verdicts = sql_policy.classify(_policy_statements(query, dialect), "local")
    return verdicts, sql_policy.refused(verdicts, TARGET_ALLOWS["local"], confirm)


def local_refusal_message(stop: sql_policy.Verdict, allow: tuple[str, ...] = TARGET_ALLOWS["local"]) -> str:
    if stop.kind in allow and stop.confirm:
        return (f"Statement {stop.statement} needs confirming ({stop.reason}), so nothing ran. "
                "Run it again and confirm to let it change the project.")
    return (f"Statement {stop.statement} {sql_policy.LABELS[stop.kind]} ({stop.reason}), so nothing "
            "ran. A local data project run reads and changes the project's own tables only.")


def _refusal(stop: sql_policy.Verdict) -> dict[str, Any]:
    return {"permission": stop.kind, "statement": stop.statement, "label": sql_policy.LABELS.get(stop.kind, ""),
            "reason": stop.reason, "confirm_required": stop.confirm}


def _writer(fmt: str, sink: Any, schema: pa.Schema) -> Any:
    if fmt == "csv":
        import pyarrow.csv as pa_csv
        return pa_csv.CSVWriter(sink, schema)
    import pyarrow.parquet as pq
    return pq.ParquetWriter(sink, schema)


def _unique_names(schema: pa.Schema) -> pa.Schema:
    """Parquet files can't be read back with two columns of one name: the second a becomes a_2."""
    seen: dict[str, int] = {}
    taken = {f.name.lower() for f in schema}
    fields = []
    for f in schema:
        key = f.name.lower()
        seen[key] = seen.get(key, 0) + 1
        name = f.name
        if seen[key] > 1:
            n = seen[key]
            while f"{f.name}_{n}".lower() in taken:
                n += 1
            name = f"{f.name}_{n}"
            taken.add(name.lower())
        fields.append(f.with_name(name))
    return pa.schema(fields, metadata=schema.metadata)


def _reserve(folder: Path, base: str, suffix: str) -> Path:
    """A new file name in ``folder``, reserved by creating it empty, so nothing is overwritten."""
    for n in range(1, 1000):
        path = folder / (f"{base}{suffix}" if n == 1 else f"{base} ({n}){suffix}")
        try:
            with open(path, "x"):
                pass
            return path
        except FileExistsError:
            continue
    raise ValueError("Couldn't find a free file name in Downloads for the export.")


def _discard(partial: Path, reserved: Path) -> None:
    """Remove a stopped or failed export: the partial file, and the name reserved for it if still empty."""
    try:
        partial.unlink()
    except OSError:
        pass
    try:
        if reserved.exists() and reserved.stat().st_size == 0:
            reserved.unlink()
    except OSError:
        pass


def _json_value(value: Any) -> Any:
    from .service import _json_value as convert
    return convert(value)
