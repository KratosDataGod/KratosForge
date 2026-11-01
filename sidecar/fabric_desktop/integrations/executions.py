"""Running SQL, Python and notebooks for an external tool, through the engine's own paths.

    SQL        sql.prepare_sandboxed and execute_sandboxed: one read-only statement (SELECT, WITH ...
               SELECT, UNION), DuckDB with external access off and its configuration locked, so
               only the lakehouse tables the query names can be read: no local files, no
               extensions, no network. The SQL page's memory share and threads per run apply
               (Settings > SQL). The tables are registered under the attach lock and the query
               runs after it is released, one external query at a time (waiting counts towards the
               timeout); a timeout or cancel interrupts DuckDB until it stops
    Python     a ProcessKernel (kernel_proc.py) of its own, started for the execution and ended
               after it: the same kernel the notebook screen uses, never the app's own kernel
    Notebook   the same, cell by cell in order as Run all does, stopping at the first error

Every execution has an id (``ex_...``), a state (queued, running, succeeded, failed, cancelled,
timed_out), timings, logs and a capped result, kept in ``<data>/integrations/executions/`` so
the Integrations page can list them. A call waits up to ``wait_seconds`` and otherwise returns
the id; ``fabric_desktop_get_execution_status`` follows it. Past ``timeout_seconds`` an execution
is interrupted, then its process is ended.

What code an external tool runs can do is limited on purpose:

- The kernel gets a storage token for reading the attached lakehouse and no other token
  (``notebookutils.credentials.getToken`` for Fabric, Graph or anything else is refused).
- Writing lakehouse tables through the engine (``saveAsTable``, ``lakehouse.write_table``) is
  refused: OneLake writes aren't offered to external tools in this release.
- Other notebooks can't be run from it (``%run``, ``notebookutils.notebook.run``): their code
  wasn't shown to the person. The app's own kernels still run them.
- Output is capped, images and HTML are left out, and every string has secrets removed.
- The kernel's environment has only the variables Python needs to start (kernel_proc.EXTERNAL_ENV_ALLOW:
  PATH, the Windows system folders, temp, home, locale, proxy and certificate settings), so a secret in an environment variable
  isn't visible to the code. It isn't a sandbox: the code runs as the person and can read their
  files, including the token cache, until that cache is encrypted (HD-013).

The person approves every Python and notebook run in the app, with the code shown; a notebook run
is bound to the SHA-256 of the cells shown and runs exactly those (services.bind_notebook).
Approved code runs as the person on this PC (see docs/product/ai-integration-security.md).
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .core import CallContext, IntegrationError, redact_result, redact_secrets
from .. import jsonfile

STATES = ("queued", "running", "succeeded", "failed", "cancelled", "timed_out")
FINISHED = ("succeeded", "failed", "cancelled", "timed_out")
MAX_OUTPUT_CHARS = 100_000    # all text output of one execution
MAX_TABLE_ROWS = 100          # rows of a displayed table kept in a result
MAX_LOG_LINES = 2_000
KEEP_RECORDS = 200            # finished executions kept on disk
STOP_GRACE_SECONDS = 5.0      # after an interrupt, before the process is ended
WRITE_REFUSED = ("External AI tools can't write lakehouse tables in this release. Run this notebook in "
                 "KratosForge to write.")
TOKEN_REFUSED = "Code an external tool runs gets no {audience} token; only the attached lakehouse can be read."
OTHER_NOTEBOOKS_REFUSED = ("Code an external tool runs can't run other notebooks (%run, notebookutils.notebook.run): "
                           "only the code the person approved runs. Run this notebook in KratosForge instead.")


def new_execution_id() -> str:
    return "ex_" + time.strftime("%Y%m%d%H%M%S") + secrets.token_hex(3)


def shape_outputs(outputs: list[dict[str, Any]], budget: list[int]) -> list[dict[str, Any]]:
    """Kernel outputs as an external tool gets them: text, errors and small tables; no images or
    HTML; at most MAX_OUTPUT_CHARS in all (``budget`` is shared by every cell of an execution)."""
    out: list[dict[str, Any]] = []
    for o in outputs or []:
        kind = o.get("type")
        if kind in ("stream", "text"):
            item: dict[str, Any] = {"type": kind, "text": o.get("text") or ""}
        elif kind == "error":
            item = {"type": "error", "ename": o.get("ename"), "evalue": o.get("evalue"), "traceback": o.get("traceback") or ""}
        elif kind == "table":
            rows = (o.get("rows") or [])[:MAX_TABLE_ROWS]
            item = {"type": "table", "columns": o.get("columns"), "rows": rows, "total_rows": o.get("total_rows")}
        elif kind == "image":
            item = {"type": "omitted", "text": f"[{o.get('mime') or 'image'} not sent to external tools]"}
        elif kind == "html":
            item = {"type": "omitted", "text": "[HTML output not sent to external tools]"}
        else:
            item = {"type": "omitted", "text": f"[{kind} output not sent to external tools]"}
        item = redact_result(item)
        size = len(json.dumps(item, default=str))
        if size > budget[0]:
            out.append({"type": "omitted", "text": f"[output cut: more than {MAX_OUTPUT_CHARS:,} characters]"})
            budget[0] = 0
            break
        budget[0] -= size
        out.append(item)
    return out


def interrupt_until_stopped(con: Any, stopped: threading.Event, seconds: float = 30.0,
                            lock: threading.Lock | None = None) -> None:
    """Interrupt a DuckDB query until it has stopped: DuckDB drops an interrupt that lands just
    before it starts a statement (localdata/engine.py's _interrupt_until_stopped does the same).
    ``lock`` is held by whoever closes ``con`` too, so an interrupt never overlaps the close."""
    lock = lock or threading.Lock()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with lock:
            if stopped.is_set():
                return
            try:
                con.interrupt()
            except Exception:  # silent-ok: the connection closed as the query ended
                return
        stopped.wait(0.05)


class Execution:
    def __init__(self, kind: str, client_id: str, title: str, target: dict[str, Any], timeout: float):
        self.id = new_execution_id()
        self.kind, self.client_id, self.title, self.target, self.timeout = kind, client_id, title, target, timeout
        self.state = "queued"
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.result: Any = None
        self.error: dict[str, Any] | None = None
        self.logs: list[str] = []
        self.cancel_event = threading.Event()
        self.done = threading.Event()
        self.on_cancel: Callable[[], None] | None = None

    def log(self, text: str) -> None:
        if len(self.logs) < MAX_LOG_LINES:
            self.logs.append(f"{time.strftime('%H:%M:%S')} {redact_secrets(text)}")

    def view(self, include_result: bool = True) -> dict[str, Any]:
        now = time.time()
        out: dict[str, Any] = {
            "id": self.id, "kind": self.kind, "state": self.state, "title": self.title, "target": self.target,
            "client_id": self.client_id, "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "timeout_seconds": self.timeout,
            "seconds": round(((self.finished_at or now) - self.started_at), 3) if self.started_at else 0.0}
        if self.error:
            out["error"] = self.error
        if include_result and self.result is not None:
            out["result"] = self.result
        return out


class ExecutionManager:
    """Executions of one engine process (an MCP server or the local API), recorded on disk."""

    def __init__(self, service: Any, folder: Path | None = None, kernel_factory: Callable[[], Any] | None = None,
                 kernel_command: list[str] | None = None):
        self.service = service
        self.kernel_command = kernel_command  # tests start the kernel with a stand-in for OneLake
        self.folder = Path(folder) if folder else Path(service.root) / "integrations" / "executions"
        self._kernel_factory = kernel_factory or self._new_kernel
        self._runs: dict[str, Execution] = {}
        self._lock = threading.Lock()
        # The engine has one attached lakehouse at a time: share the lock the desktop's cells take.
        self._attach_lock = getattr(service, "attach_lock", None) or threading.RLock()
        # Held only while an external SQL statement executes (not while its tables are registered):
        # one at a time, so concurrent queries can't each take the SQL page's memory share.
        self._sql_slot = threading.Lock()

    # ---- the kernel external code runs in

    def _token(self, audience: str) -> str:
        if audience != "storage":
            raise PermissionError(TOKEN_REFUSED.format(audience=audience))
        return self.service.auth.storage_token()

    @staticmethod
    def _other_notebook(name: str) -> list[dict[str, Any]]:
        raise PermissionError(OTHER_NOTEBOOKS_REFUSED)

    def _new_kernel(self) -> Any:
        from .. import lakehouse
        from ..kernel_proc import ProcessKernel, external_environment

        class ExternalKernel(ProcessKernel):
            def _sync_lakehouse(self) -> None:
                # Tell the kernel every table write is checked (the check below refuses them all).
                saved = lakehouse.write_guard
                lakehouse.write_guard = saved or (lambda name, schema, table: WRITE_REFUSED)
                try:
                    super()._sync_lakehouse()
                finally:
                    lakehouse.write_guard = saved

        kernel = ExternalKernel(token=self._token, load_notebook=self._other_notebook, command=self.kernel_command,
                                env=external_environment)
        kernel.calls["write_guard"] = lambda name, schema, table: WRITE_REFUSED
        return kernel

    # ---- records

    def get(self, execution_id: str, client_id: str) -> Execution | dict[str, Any]:
        """A running execution, or a finished one's record. Only the tool that started it can see it."""
        with self._lock:
            run = self._runs.get(execution_id)
        if run is not None:
            if run.client_id != client_id:
                raise LookupError(f"No execution {execution_id}")
            return run
        path = self.folder / f"{execution_id}.json"
        if not execution_id.replace("_", "").isalnum() or not path.exists():
            raise LookupError(f"No execution {execution_id}")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("client_id") != client_id:
            raise LookupError(f"No execution {execution_id}")
        return record

    def list(self, client_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        if self.folder.exists():
            for path in sorted(self.folder.glob("ex_*.json"), reverse=True)[:KEEP_RECORDS]:
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                record.pop("result", None)
                record.pop("logs", None)
                rows[record["id"]] = record
        with self._lock:
            for run in self._runs.values():
                rows[run.id] = run.view(include_result=False)
        out = [r for r in rows.values() if client_id is None or r.get("client_id") == client_id]
        return sorted(out, key=lambda r: r.get("created_at") or 0, reverse=True)[:limit]

    def _save(self, run: Execution) -> None:
        body = run.view() | {"logs": run.logs}
        jsonfile.write_text(self.folder / f"{run.id}.json", json.dumps(body, default=str))
        old = sorted(self.folder.glob("ex_*.json"))[:-KEEP_RECORDS]
        for path in old:
            path.unlink(missing_ok=True)

    # ---- running

    def start(self, run: Execution, work: Callable[[Execution], Any], ctx: CallContext, wait: float) -> dict[str, Any]:
        """Run ``work`` on its own thread; wait up to ``wait`` seconds for it, then hand back what
        is known. The call being cancelled (the client's cancel) cancels the execution too."""
        with self._lock:
            self._runs[run.id] = run

        def body() -> None:
            run.state, run.started_at = "running", time.time()
            run.log(f"started {run.kind}: {run.title}")
            try:
                run.result = work(run)
                if run.state == "running":
                    run.state = "succeeded"
            except IntegrationError as err:
                run.state = "cancelled" if run.cancel_event.is_set() and run.state == "running" else (
                    run.state if run.state in ("timed_out", "cancelled") else "failed")
                run.error = err.to_dict()
            except Exception as exc:  # noqa: BLE001 - recorded on the execution
                from .core import error_from
                if run.state == "running":
                    run.state = "cancelled" if run.cancel_event.is_set() else "failed"
                run.error = error_from(exc).to_dict()
            finally:
                run.finished_at = time.time()
                run.log(f"{run.state} after {run.view()['seconds']} s")
                try:
                    self._save(run)
                finally:
                    with self._lock:
                        self._runs.pop(run.id, None)
                    run.done.set()

        threading.Thread(target=body, daemon=True, name=f"execution-{run.id}").start()
        threading.Thread(target=self._watch, args=(run,), daemon=True, name=f"execution-timer-{run.id}").start()
        deadline = time.monotonic() + max(0.0, min(wait, ctx.remaining() - 1 if ctx.timeout else wait))
        while not run.done.wait(0.05):
            if ctx.cancelled:
                self.cancel(run.id, run.client_id, "The client cancelled the call.")
            if time.monotonic() >= deadline:
                break
        return run.view()

    def _watch(self, run: Execution) -> None:
        if not run.done.wait(run.timeout):
            run.state = "timed_out"
            run.log(f"timed out after {run.timeout:g} s")
            self._stop(run)

    def _stop(self, run: Execution) -> None:
        run.cancel_event.set()
        if run.on_cancel is not None:
            try:
                run.on_cancel()
            except Exception as exc:  # noqa: BLE001 - logged; the timer below still ends it
                run.log(f"stopping: {exc}")

    def cancel(self, execution_id: str, client_id: str, why: str = "Cancelled by the tool.") -> dict[str, Any]:
        found = self.get(execution_id, client_id)
        if not isinstance(found, Execution):
            return found | {"cancelled": False}
        if found.state not in FINISHED:
            found.state = "cancelled"
            found.log(why)
            self._stop(found)
            found.done.wait(STOP_GRACE_SECONDS * 2)
        return found.view() | {"cancelled": found.state == "cancelled"}

    # ---- the three kinds

    def run_sql(self, run: Execution, query: str, lakehouse_ref: dict[str, str], max_rows: int) -> dict[str, Any]:
        """The query's tables are registered under the attach lock; the query itself runs after the
        lock is released, so a long query never holds up the next run. A timeout or cancel
        interrupts DuckDB (con.interrupt) until the query has stopped."""
        import duckdb

        from .. import app_settings, sql, sql_exec
        from ..service import sql_result
        limits = app_settings.sql_limits()  # the SQL page's memory share and threads per run (SQL-003)
        with self._attach_lock:
            if run.cancel_event.is_set():
                raise self._stopped(run)
            self.service._attach(lakehouse_ref)
            con, statement = sql.prepare_sandboxed(
                query, memory_mb=sql_exec.memory_mb(int(limits["sql_memory_limit_percent"])),
                threads=sql_exec.run_threads(limits))
        stopped = threading.Event()
        con_lock = threading.Lock()  # an interrupt and the close never overlap
        have_slot = False
        try:
            # One external query runs at a time, each with the SQL page's memory share. Waiting for
            # the slot counts towards the timeout: a stop while waiting ends the run here.
            while not (have_slot := self._sql_slot.acquire(timeout=0.05)):
                if run.cancel_event.is_set():
                    raise self._stopped(run)
            run.on_cancel = lambda: threading.Thread(target=interrupt_until_stopped, args=(con, stopped, 30.0, con_lock), daemon=True,
                                                     name=f"execution-stop-{run.id}").start()
            started = time.monotonic()
            # _stop sets cancel_event before it reads on_cancel: a stop that came before on_cancel was
            # set is seen here, and one after it interrupts the query.
            if run.cancel_event.is_set():
                raise self._stopped(run)
            result = sql.execute_sandboxed(con, statement)
        except duckdb.Error:
            if run.cancel_event.is_set():
                raise self._stopped(run) from None
            raise
        finally:
            run.on_cancel = None
            with con_lock:
                stopped.set()
                con.close()
            if have_slot:
                self._sql_slot.release()
        out = sql_result(result, max_rows, started)
        run.log(f"{out['row_count']} rows in {out['seconds']} s")
        return out | {"truncated": out["row_count"] > len(out["rows"])}

    @staticmethod
    def _stopped(run: Execution) -> IntegrationError:
        if run.state == "timed_out":
            return IntegrationError("timeout", f"The query was stopped after its time limit ({run.timeout:g} s).",
                                    hint="Narrow the query (a filter, fewer rows or columns) or give it a longer "
                                         "timeout_seconds.", retryable=True)
        return IntegrationError("cancelled", "The query was cancelled.")

    def run_cells(self, run: Execution, cells: list[str], lakehouse_ref: dict[str, str] | None,
                  notebook: str = "", skipped: str = "") -> dict[str, Any]:
        """Run code cells in order in a fresh process kernel, then end it. ``skipped`` says what
        the run left out, in its log and result."""
        if skipped:
            run.log(skipped)
        kernel = self._kernel_factory()
        kernel.notebook_name = notebook
        where = getattr(self.service, "_resources_dir", None)  # builtin/ paths, as in the notebook
        kernel.resources_dir = where(notebook) if where else ""
        budget = [MAX_OUTPUT_CHARS]
        done: list[dict[str, Any]] = []

        def stop() -> None:
            kernel.interrupt()
            deadline = time.monotonic() + STOP_GRACE_SECONDS
            while time.monotonic() < deadline and kernel.poll().get("status") == "running":
                time.sleep(0.1)
            kernel.stop()
        run.on_cancel = stop
        try:
            for n, code in enumerate(cells, 1):
                if run.cancel_event.is_set():
                    break
                run.log(f"cell {n} of {len(cells)}")
                with self._attach_lock:
                    self.service._attach(lakehouse_ref)
                    snap = kernel.start(code)
                version, outputs = -1, snap.get("outputs") or []
                while snap["status"] == "running":
                    snap = kernel.poll(version, 0.5)
                    version = snap.get("version", version)
                    if snap.get("outputs") is not None:
                        outputs = snap["outputs"]
                done.append({"cell": n, "status": snap["status"], "seconds": snap.get("seconds"),
                             "outputs": shape_outputs(outputs, budget)})
                if snap["status"] != "ok":
                    run.log(f"cell {n} ended: {snap['status']}")
                    break
        finally:
            run.on_cancel = None
            kernel.close()
        if run.state in ("cancelled", "timed_out"):
            raise IntegrationError("timeout" if run.state == "timed_out" else "cancelled",
                                   f"The execution was {run.state.replace('_', ' ')}.", details={"cells": done})
        failed = next((c for c in done if c["status"] != "ok"), None)
        if failed is not None:
            run.state = "failed"
            run.error = {"code": "failed", "message": f"Cell {failed['cell']} failed.", "retryable": False}
        return {"cells": done, "cells_total": len(cells), "cells_run": len(done)} | ({"skipped": skipped} if skipped else {})
