"""Notebook steps of a local pipeline run (ORCH-004): the runner's NotebookRunner.

Each Notebook activity runs a notebook saved on this PC, picked by ``notebooks`` (``{activity
name or notebookId: notebook name}``). It runs the way Validate notebook's clean run does
(validate_service.py): every code cell from the top in a fresh kernel process of its own, so
the person's kernel is left alone, with the proof runtime bound (proof_runtime.py):

* Tables are read at the version they had when the run's first notebook step started in that
  lakehouse, and written to the run's own folder,
  ``Files/_fabric_desktop/proofs/pipeline-<run id>/pc/``, never to the lakehouse's tables. A later
  notebook step in the same run reads what an earlier one wrote there.
* The activity's parameters go in a cell after the notebook's parameter cell (at the top when it
  has none), and ``notebookutils.notebook.exit(value)`` ends the notebook and becomes the
  activity's ``output.result.exitValue``.
* What the clean run can't contain stops the run before it starts: a write the runtime can't
  redirect, and Spark cells. Network calls and files on this PC need ``allow_uncontained``.

The folder is deleted when the pipeline run ends unless the caller keeps it. No Fabric job is
started; reading and writing OneLake uses some capacity. Notebook steps run when the person starts
the run (the engine's pipeline_run_start, or ``fabric-desktop pipeline run --notebook``); runs
external AI tools start get no notebook runner, so their Notebook activities still need a mock.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from . import redact, validate
from .orchestration.adapters import ActivityFailed, notebook_key
from .orchestration.definition import Activity
from .proof_runtime import PROOF_ROOT

MAX_KERNELS = 2          # notebook steps running at once in one pipeline run
EXIT_TEXT = "Notebook exited with value:"
_EXIT_MARK = "\x1efd-exit:"
_EXIT_CELL = (f"print({_EXIT_MARK!r} + __import__('json').dumps("
              "{'exited': '__fd_exit__' in globals(), 'value': str(globals().get('__fd_exit__', ''))}))")


def code_cells(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The cells a run runs: code that isn't empty or frozen (Fabric skips frozen cells too)."""
    return [c for c in cells if c.get("type", "code") != "markdown" and (c.get("source") or "").strip()
            and not (c.get("meta") or {}).get("frozen")]


def _problem(code: str, message: str, a: Activity) -> dict[str, Any]:
    return {"code": code, "message": message, "activity": a.name, "path": a.path}


def parameters_cell(parameters: dict[str, Any]) -> str:
    return "# Parameters from the pipeline\n" + "\n".join(f"{k} = {v!r}" for k, v in parameters.items())


def with_parameters(code: list[dict[str, Any]], parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """The code cells with the pipeline's parameters in a cell of their own, after the parameter
    cell (Fabric's rule), or first when there is none."""
    if not parameters:
        return code
    injected = {"type": "code", "source": parameters_cell(parameters), "injected": True}
    at = next((i + 1 for i, c in enumerate(code) if "parameters" in ((c.get("meta") or {}).get("tags") or [])), 0)
    return code[:at] + [injected] + code[at:]


def _text(outputs: list[dict[str, Any]]) -> str:
    return "".join(o.get("text") or "" for o in outputs if o.get("type") == "stream")


class LocalNotebooks:
    """The NotebookRunner for one pipeline run. ``svc`` is the engine's Service (the clean kernel,
    the saved notebooks and the lakehouse helpers come from it)."""

    def __init__(self, svc: Any, run_id: str, notebooks: dict[str, str] | None, allow_uncontained: bool = False,
                 keep_outputs: bool = False):
        self.svc, self.run_id = svc, run_id
        self.map = {k: v for k, v in (notebooks or {}).items() if isinstance(k, str) and isinstance(v, str)}
        self.allow_uncontained, self.keep_outputs = allow_uncontained, keep_outputs
        self.folder = f"{PROOF_ROOT}/pipeline-{run_id}"
        self._lock = threading.Lock()
        self._kernels = threading.BoundedSemaphore(MAX_KERNELS)
        self._live: set[Any] = set()
        # Per lakehouse id: its ref, the versions pinned when it was first used, and what was written.
        self._lakes: dict[str, dict[str, Any]] = {}
        self._ran = 0
        self._after: list[str] = []

    # ---- before the run

    def notebook_of(self, a: Activity) -> str | None:
        return self.map.get(a.name) or self.map.get(notebook_key(a))

    def _load(self, name: str) -> dict[str, Any]:
        return self.svc.notebooks.load(name)

    def check(self, a: Activity) -> list[dict[str, Any]]:
        name = self.notebook_of(a)
        if not name:
            return [_problem("notebook_unmapped", f"{a.name} runs notebook {notebook_key(a) or '(no notebookId)'}; say "
                             "which notebook on this PC it is (notebooks: {activity name or notebookId: notebook "
                             "name}), or give a mock for it.", a)]
        try:
            nb = self._load(name)
        except (OSError, ValueError, LookupError) as exc:
            return [_problem("notebook_missing", f"{a.name}: notebook {name!r} can't be opened on this PC "
                             f"({type(exc).__name__}).", a)]
        plan = validate.run_plan(nb.get("cells") or [], nb.get("lakehouse"))
        if not plan["ok"]:
            b = plan["blockers"][0]
            if b["text"].startswith("uses Spark"):
                return [_problem("notebook_spark", f"{a.name}: notebook {name!r} uses Spark (cell {b['cell']}), which "
                                 "doesn't run in a pipeline on this PC yet. Give a mock for it.", a)]
            return [_problem("notebook_not_contained", f"{a.name}: notebook {name!r} cell {b['cell']} {b['text']}. "
                             "Change that cell to read and write through the lakehouse module, or give a mock.", a)]
        if plan["uncontained"] and not self.allow_uncontained:
            u = plan["uncontained"][0]
            return [_problem("notebook_uncontained", f"{a.name}: notebook {name!r} cell {u['cell']}: {u['text']}. "
                             "Confirm with allow_uncontained to run it anyway.", a)]
        return []

    def lakehouses(self, acts: list[Activity]) -> list[dict[str, str]]:
        """The lakehouses the mapped notebooks use, so the engine can sign in before the run."""
        refs = {}
        for a in acts:
            name = self.notebook_of(a)
            if name:
                try:
                    ref = self._load(name).get("lakehouse")
                except (OSError, ValueError, LookupError):
                    continue
                if ref and ref.get("lakehouse_id"):
                    refs[ref["lakehouse_id"]] = ref
        return list(refs.values())

    # ---- one notebook step

    def _lake(self, ref: dict[str, str]) -> dict[str, Any]:
        with self._lock:
            lake = self._lakes.get(ref["lakehouse_id"])
            if lake is None:
                schemas = self.svc._schemas_enabled(ref["workspace_id"], ref["lakehouse_id"])
                lake = {"ref": ref, "pins": self.svc._proof_tables(ref).versions(), "flat": not schemas,
                        "written": set()}
                self._lakes[ref["lakehouse_id"]] = lake
            return lake

    def run(self, a: Activity, parameters: dict[str, Any], cancelled: Callable[[], bool],
            deadline: float | None) -> dict[str, Any]:
        name = self.notebook_of(a)
        problems = self.check(a)
        if problems or not name:
            raise ActivityFailed(problems[0]["message"] if problems else f"{a.name} has no notebook.", "LocalNotebookRefused")
        nb = self._load(name)
        ref = nb.get("lakehouse") or None
        code = code_cells(nb.get("cells") or [])
        cells = with_parameters(code, parameters)
        while not self._kernels.acquire(timeout=0.2):
            if cancelled():
                raise ActivityFailed("Cancelled.", "LocalCancelled")
        kernel = None
        started = time.monotonic()
        out: dict[str, Any] = {"notebook": name, "cells_run": 0, "cells_total": len(code)}
        try:
            kernel = self.svc._validate_kernel(ref)
            with self._lock:
                self._live.add(kernel)
            kernel.notebook_name = name
            lake = None
            if ref:
                lake = self._lake(ref)
                root, options = self.svc._proof_location(ref)
                with self._lock:
                    seeded = sorted(lake["written"])
                kernel.bind_proof({"id": f"pipeline-{self.run_id}", "pins": lake["pins"], "flat": lake["flat"],
                                   "seed": 0, "seeded": seeded}, "pc", root, options)
            exit_value = None
            try:
                for n, c in enumerate(cells, 1):
                    outputs = self._cell(kernel, c["source"], cancelled, deadline, name, n)
                    if not c.get("injected"):
                        out["cells_run"] += 1
                    if EXIT_TEXT in _text(outputs):
                        ended = self._exit(kernel, cancelled, deadline, name, n)
                        if ended is not None:
                            exit_value = ended
                            break
            finally:
                if lake is not None and kernel.alive():
                    writes = (kernel.proof_log() or {}).get("writes") or {}
                    with self._lock:
                        lake["written"].update(writes)
                    out["tables_written"] = sorted(writes)
            out["seconds"] = round(time.monotonic() - started, 3)
            out["result"] = {"runStatus": "Succeeded", "exitValue": exit_value}
            if ref:
                out["sandbox"] = f"{self.folder}/pc"
            return out
        finally:
            if kernel is not None:
                kernel.close()
                with self._lock:
                    self._live.discard(kernel)
                    self._ran += 1
            self._kernels.release()

    def _cell(self, kernel: Any, source: str, cancelled: Callable[[], bool], deadline: float | None,
              name: str, n: int) -> list[dict[str, Any]]:
        snap = kernel.start(source)
        version, outputs = -1, snap.get("outputs") or []
        while snap["status"] == "running":
            if cancelled() or (deadline is not None and time.monotonic() > deadline):
                if kernel.alive():
                    kernel.stop()
                if cancelled():
                    raise ActivityFailed("Cancelled.", "LocalCancelled")
                raise ActivityFailed("The activity reached its policy timeout.", "LocalActivityTimeout")
            snap = kernel.poll(version, 0.5)
            version = snap.get("version", version)
            if snap.get("outputs") is not None:
                outputs = snap["outputs"]
        outputs = snap.get("outputs") or outputs
        if snap["status"] != "ok":
            err = next((o for o in reversed(outputs) if o.get("type") == "error"), None) or {}
            why = f"{err.get('ename') or snap['status']}: {err.get('evalue') or ''}".strip().rstrip(":")
            if getattr(kernel, "limit_hit", ""):
                why = f"stopped by the {kernel.limit_hit} limit"
            raise ActivityFailed(redact.secrets(f"Notebook {name!r} failed at cell {n}: {why}")[:2000],
                                 "LocalNotebookFailed", {"notebook": name, "failed_cell": n})
        return outputs

    def _exit(self, kernel: Any, cancelled: Callable[[], bool], deadline: float | None, name: str, n: int) -> str | None:
        """The value notebookutils.notebook.exit was given, or None when the notebook didn't exit."""
        text = _text(self._cell(kernel, _EXIT_CELL, cancelled, deadline, name, n))
        at = text.rfind(_EXIT_MARK)
        if at < 0:
            return None
        found = json.loads(text[at + len(_EXIT_MARK):].strip().splitlines()[0])
        return found["value"] if found.get("exited") else None

    # ---- after the run

    def stop(self) -> None:
        """Stop every notebook still running (the pipeline run is being cancelled or the engine stops)."""
        with self._lock:
            live = list(self._live)
        for kernel in live:
            if kernel.alive():
                kernel.stop()

    def finish(self) -> None:
        """Delete the run's folder in each lakehouse it wrote to, unless it is kept. What couldn't be
        deleted goes in the run's notes."""
        for lake in list(self._lakes.values()):
            if self.keep_outputs or not lake["written"]:
                continue
            try:
                self.svc._proof_delete_folder(lake["ref"], self.folder)
            except Exception as exc:  # noqa: BLE001 - the run's result stands; say what may be left behind
                self._after.append(f"The folder {self.folder} in {lake['ref'].get('name', 'the lakehouse')} couldn't "
                                   f"be deleted ({type(exc).__name__}); if it's there, delete it in Fabric.")

    def notes(self) -> list[str]:
        with self._lock:
            lakes = list(self._lakes.values())
            ran = self._ran
        if not ran and not lakes:
            return list(self._after)
        if not lakes:
            return [f"Ran on this PC; {ran} notebook step{'s' if ran != 1 else ''} ran in a fresh local kernel with no "
                    "lakehouse. No Fabric call was made."] + self._after
        names = ", ".join(sorted(lake["ref"].get("name") or lake["ref"]["lakehouse_id"] for lake in lakes))
        where = (f"kept in {self.folder}" if self.keep_outputs else f"in {self.folder}, deleted when the run ended")
        return [f"Ran on this PC; no Fabric job was started. Notebook steps read tables in {names} at the versions they "
                "had when the first step started, which uses some capacity, and wrote only to the run's own folder "
                f"({where}), never to the lakehouse's tables."] + self._after
