"""Which of a notebook's cells a run runs, in one place (KF-DE-001, first slice).

Every path that runs a notebook's cells (Validate, Prove, side by side, integrations executions,
scheduled runs, pipeline Notebook steps and notebookutils.notebook.run) asks ``runs`` instead of
writing its own test, so they agree: code that isn't empty, and never a frozen cell, as Fabric
skips frozen cells in every run."""

from __future__ import annotations

from typing import Any


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
