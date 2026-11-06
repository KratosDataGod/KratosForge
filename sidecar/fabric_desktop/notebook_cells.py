"""Which of a notebook's cells a run runs, in one place (KF-DE-001, first slice).

Every path that runs a notebook's cells (Validate, Prove, side by side, integrations executions,
scheduled runs, pipeline Notebook steps and notebookutils.notebook.run) asks ``runs`` instead of
writing its own test, so they agree: code that isn't empty, and never a frozen cell, as Fabric
skips frozen cells in every run.

The runs that start a fresh kernel for the notebook (Validate, pipeline Notebook steps and
integrations executions) also wait for each cell the same way, with ``wait``."""

from __future__ import annotations

from typing import Any, Callable


def frozen(cell: dict[str, Any]) -> bool:
    return bool((cell.get("meta") or {}).get("frozen"))


def runs(cell: dict[str, Any], skip_fabric_step: bool = False) -> bool:
    """Whether a run runs this cell. ``skip_fabric_step`` for runs that aren't started from the
    notebook itself, and for Validate and integrations runs: the step runs on Fabric Spark only
    (fabric_step.py). Prove and side by side refuse a notebook that has one instead."""
    if cell.get("type", "code") == "markdown" or not (cell.get("source") or "").strip() or frozen(cell):
        return False
    return not (skip_fabric_step and (cell.get("meta") or {}).get("fabric_step"))


def step_cell(cells: list[dict[str, Any]]) -> int | None:
    """The number (from 1, as the notebook shows) of the cell a run with ``skip_fabric_step`` skips."""
    return next((n for n, c in enumerate(cells, 1) if runs(c) and not runs(c, skip_fabric_step=True)), None)


def skipped_step(cells: list[dict[str, Any]]) -> str:
    """What to tell the person when a run with ``skip_fabric_step`` leaves a cell out, or ""."""
    n = step_cell(cells)
    return f"Cell {n} is marked Run on Fabric, which runs only on Fabric Spark, so this run on this PC skipped it." if n else ""


def wait(kernel: Any, snap: dict[str, Any], stop: Callable[[], bool] = lambda: False,
         progress: Callable[[dict[str, Any]], None] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Waits for the cell ``kernel.start`` just started (``snap`` is what it returned): the cell's
    last snapshot and its outputs. When ``stop()`` turns true while it runs, the kernel's process
    is stopped (if it has one yet: it may still be starting) and the snapshot says "cancelled"; the
    caller says why. ``progress`` sees each snapshot as it comes."""
    version, outputs = -1, snap.get("outputs") or []
    while snap["status"] == "running":
        if stop():
            if kernel.alive():
                kernel.stop()
            return {"status": "cancelled", "seconds": snap.get("seconds")}, outputs
        snap = kernel.poll(version, 0.5)
        version = snap.get("version", version)
        if progress is not None:
            progress(snap)
        if snap.get("outputs") is not None:
            outputs = snap["outputs"]
    return snap, snap.get("outputs") or outputs
