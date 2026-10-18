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

Each finished run adds one ``sql_execution`` line to the audit trail: its id, dialect, lakehouse,
state, permission class, rows and seconds. Never the SQL, the error text, the token or any data.
"""

from __future__ import annotations

import bisect
import itertools
import json
import os
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

from . import app_settings, sql_policy

STATES = ("queued", "running", "cancelling", "completed", "failed", "cancelled", "timed_out")
FINISHED = ("completed", "failed", "cancelled", "timed_out")
MAX_KEPT = 20  # finished runs whose snapshots are kept, newest first
EXPORT_FORMATS = ("csv", "parquet")
EXPORT_BATCH_ROWS = 65_536


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

    @property
    def snapshot(self) -> Path:
        return self.folder / "result.arrow"

    def status(self) -> dict[str, Any]:
        """What the page shows: never the SQL or the token."""
        end = self.ended or time.time()
        return {"id": self.id, "state": self.state, "target": "lakehouse", "dialect": self.dialect,
                "lakehouse": self.lakehouse.get("name", ""),
                "created": self.created, "started": self.started, "ended": self.ended,
                "seconds": round(end - self.started, 2) if self.started else 0.0,
                "rows": self.rows, "columns": self.columns, "permission": self.permission,
                "error": self.error, "error_kind": self.error_kind, "refused": self.refused,
                "time_limit_minutes": round(self.time_limit_s / 60, 2),
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
                 schemas_enabled: Callable[[dict[str, Any]], bool] = lambda _ref: True, test_lake: Path | None = None):
        self.root = Path(root)
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

    def start(self, query: str, lakehouse_ref: dict[str, Any], dialect: str = "spark",
              allow: list[str] | None = None) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("The query is empty.")
        if not isinstance(lakehouse_ref, dict) or not all(isinstance(lakehouse_ref.get(k), str) and lakehouse_ref[k]
                                                          for k in ("workspace_id", "lakehouse_id", "name")):
            raise ValueError("Pick a lakehouse to run the query against.")
        allowed = tuple(dict.fromkeys(["read_only", *(allow or [])]))
        if any(a not in ("read_only", "local_mutation") for a in allowed):
            # OneLake writes and Fabric changes are never allowed from the SQL page, whoever asks.
            raise PermissionError("The SQL page can only read tables and change a run's own session on this PC.")
        if not self._cleaned_at_start:
            self._cleaned_at_start = True
            self.cleanup(earlier_sessions=True)
        run_id = uuid.uuid4().hex[:12]
        folder = self.base() / run_id
        run = Execution(id=run_id, query=query, dialect=dialect, lakehouse=dict(lakehouse_ref), allow=allowed, folder=folder)
        with self._lock:
            self._runs[run_id] = run
        self._pump()
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
            waiting = sorted((r for r in self._runs.values() if r.state == "queued"), key=lambda r: r.created)
            for run in waiting[:max(0, int(limits["sql_max_concurrent"]) - active)]:
                self._launch(run, limits)

    def _launch(self, run: Execution, limits: dict[str, Any]) -> None:
        run.folder.mkdir(parents=True, exist_ok=True)
        lh = run.lakehouse
        job = {"query": run.query, "dialect": run.dialect, "allow": list(run.allow),
               "lakehouse": {k: lh[k] for k in ("workspace_id", "lakehouse_id", "name", "workspace_name") if k in lh},
               "work_dir": str(self.root), "out": str(run.snapshot), "temp_dir": str(run.folder / "spill"),
               "memory_mb": self._memory_mb(int(limits["sql_memory_limit_percent"])),
               "threads": run_threads(limits)}
        if self.test_lake:  # tests only: local Delta folders instead of OneLake
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
        minutes = float(limits["sql_time_limit_minutes"] or 0)
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
            elif name == "schemas_enabled":
                reply = {"value": self._schemas_enabled(run.lakehouse)}
            else:
                reply = {"error": f"Unknown question {name!r}"}
        except Exception as exc:  # noqa: BLE001 - signed out or sign-in cancelled: the run fails with why
            reply = {"error": f"Sign in to read lakehouse tables ({type(exc).__name__}: {exc})"}
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
        """End this run's own process: the Popen this manager started for it, nothing else."""
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

    def _finished(self, run: Execution) -> None:
        if self._audit is not None:
            lh = run.lakehouse
            try:
                self._audit("sql_execution", "ok" if run.state == "completed" else "error",
                            (run.ended or time.time()) - (run.started or run.created),
                            {"execution": run.id, "state": run.state, "dialect": run.dialect,
                             "permission": run.permission or "", "rows": run.rows,
                             "workspace_id": lh.get("workspace_id", ""), "lakehouse_id": lh.get("lakehouse_id", ""),
                             "lakehouse": lh.get("name", "")})
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
        stem = "".join(ch for ch in (name or run.lakehouse.get("name") or "SQL") if ch.isalnum() or ch in " -_").strip()[:60]
        base = f"{stem or 'SQL'} query {time.strftime('%Y-%m-%d %H%M%S')}"
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
            os.replace(partial, export.path)  # over the empty file that reserved the name
            export.state = "completed"
        except _Stopped:
            export.state = "cancelled"
            _discard(partial, export.path)
        except Exception as exc:  # noqa: BLE001 - shown on the page
            export.state, export.error = "failed", str(exc) or type(exc).__name__
            _discard(partial, export.path)

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
