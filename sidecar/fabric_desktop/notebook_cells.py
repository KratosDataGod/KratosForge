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
    notebook itself: its Fabric step runs on Fabric Spark only then (fabric_step.py)."""
    if cell.get("type", "code") == "markdown" or not (cell.get("source") or "").strip() or frozen(cell):
        return False
    return not (skip_fabric_step and (cell.get("meta") or {}).get("fabric_step"))
