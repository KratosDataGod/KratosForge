"""The workloads in a workspace, from what Fabric already says about them (OE-001).

Pure: the service reads Fabric (items, definitions, job history) and hands the results here, so
the same code runs on a live read and on the demonstration fixture. Nothing is executed: notebook
code is kept as text for the analyzers to parse, and a pipeline definition is read with the
pipeline reader the Pipelines page uses.

A workload::

    {"workspace_id", "workspace_name", "capacity_id", "item_id", "type", "name",
     "definition": "read" | "not_read" | "unsupported", "problem": str | None,
     "cells": [source, ...]            (notebooks)
     "default_lakehouse": id | None    (notebooks)
     "activities": [{...}]             (pipelines: name, type, inactive, type_properties)
     "runs": {...}                     (see :func:`run_summary`)}
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import statistics
from typing import Any

from ..orchestration.definition import DefinitionError, loads

ANALYZED = {"Notebook", "DataPipeline"}
_PY_CELL = re.compile(r"^# (?:CELL|MARKDOWN|PARAMETERS CELL) \*+\s*$", re.MULTILINE)
_PY_META = re.compile(r"^# META .*$\n?", re.MULTILINE)
_PY_DEFAULT_LH = re.compile(r'"default_lakehouse"\s*:\s*"([^"]+)"')


def _when(text: Any) -> dt.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def run_summary(jobs: list[dict[str, Any]] | None, now: dt.datetime, days: int) -> dict[str, Any]:
    """What the item's Fabric job history says about the last ``days`` days.

    ``seconds`` are measured from Fabric's start and end times of finished runs. ``scheduled`` counts
    runs Fabric started on a schedule. ``None`` history means it couldn't be read, which is not the
    same as no runs."""
    if jobs is None:
        return {"read": False, "days": days, "runs": None, "scheduled": None, "failed": None,
                "seconds_total": None, "seconds_median": None, "first": None, "last": None}
    since = now - dt.timedelta(days=days)
    kept: list[tuple[dict[str, Any], dt.datetime]] = []
    for j in jobs:
        t = _when(j.get("startTimeUtc"))
        if t is not None and since <= t <= now:
            kept.append((j, t))
    secs = []
    for j, start in kept:
        end = _when(j.get("endTimeUtc"))
        if end is not None and end >= start and j.get("status") in ("Completed", "Failed", "Cancelled"):
            secs.append((end - start).total_seconds())
    starts = sorted(t for _, t in kept)
    return {"read": True, "days": days, "runs": len(kept),
            "scheduled": sum(1 for j, _ in kept if str(j.get("invokeType") or "").lower() == "scheduled"),
            "failed": sum(1 for j, _ in kept if j.get("status") == "Failed"),
            "seconds_total": round(sum(secs), 3) if secs else (0.0 if not kept else None),
            "seconds_median": round(statistics.median(secs), 3) if secs else None,
            "timed_runs": len(secs),
            "first": starts[0].isoformat(timespec="seconds") if starts else None,
            "last": starts[-1].isoformat(timespec="seconds") if starts else None}


def _parts(definition: dict[str, Any]) -> list[dict[str, Any]]:
    parts = (definition.get("definition") or definition).get("parts")
    return [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []


def _decode(part: dict[str, Any]) -> str:
    payload = part.get("payload") or ""
    if part.get("payloadType", "InlineBase64") != "InlineBase64":
        return str(payload)
    return base64.b64decode(payload).decode("utf-8", "replace")


def notebook_content(definition: dict[str, Any]) -> tuple[list[str], str | None]:
    """A notebook definition's code cells (sources, in order) and its default lakehouse id.

    Reads the ipynb form (asked for with ?format=ipynb) and, failing that, Fabric's .py form.
    Markdown cells are left out: they don't run. Raises ValueError when there's no content."""
    part = next((p for p in _parts(definition) if str(p.get("path", "")).startswith("notebook-content")), None)
    if part is None:
        raise ValueError("the definition has no notebook-content part")
    text = _decode(part)
    if str(part.get("path")).endswith(".ipynb"):
        doc = json.loads(text)
        lh = (((doc.get("metadata") or {}).get("dependencies") or {}).get("lakehouse") or {}).get("default_lakehouse")
        cells = []
        for c in doc.get("cells") or []:
            if c.get("cell_type") != "code":
                continue
            src = c.get("source")
            cells.append("".join(src) if isinstance(src, list) else str(src or ""))
        return cells, lh or None
    found = _PY_DEFAULT_LH.search(text)
    chunks = _PY_CELL.split(text)
    markers = _PY_CELL.findall(text)
    cells = [_PY_META.sub("", chunk).strip("\n") for marker, chunk in zip(markers, chunks[1:], strict=False)
             if "MARKDOWN" not in marker]
    return cells, found.group(1) if found else None


def pipeline_activities(definition: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Every activity of a pipeline (nested ones too), with what the analyzers need."""
    imported = loads(json.dumps(definition), name_hint=name)
    return [{"name": a.name, "type": a.type, "inactive": a.inactive, "type_properties": a.type_properties,
             "path": a.path} for a in imported.pipeline.all_activities()]


def workloads(workspace: dict[str, Any], items: list[dict[str, Any]], definitions: dict[str, dict[str, Any]],
              jobs: dict[str, list[dict[str, Any]] | None], *, now: dt.datetime, days: int) -> list[dict[str, Any]]:
    """The workspace's workloads. ``definitions`` maps item id to a getDefinition result, or to
    ``{"error": text}`` when it couldn't be read; ``jobs`` maps item id to its job instances
    (``None`` when unread)."""
    out = []
    for it in items:
        item_id, kind = str(it.get("id") or ""), str(it.get("type") or "")
        if not item_id:
            continue
        w: dict[str, Any] = {"workspace_id": workspace.get("id"), "workspace_name": workspace.get("displayName") or "",
                             "capacity_id": workspace.get("capacityId"), "item_id": item_id, "type": kind,
                             "name": str(it.get("displayName") or ""), "definition": "unsupported", "problem": None,
                             "runs": run_summary(jobs.get(item_id), now, days)}
        if kind in ANALYZED:
            got = definitions.get(item_id)
            if got is None or "error" in got:
                w["definition"] = "not_read"
                w["problem"] = (got or {}).get("error") or "The definition wasn't read."
            else:
                try:
                    if kind == "Notebook":
                        w["cells"], w["default_lakehouse"] = notebook_content(got)
                    else:
                        w["activities"] = pipeline_activities(got, w["name"])
                    w["definition"] = "read"
                except (ValueError, DefinitionError, KeyError, TypeError) as exc:
                    w["definition"] = "not_read"
                    w["problem"] = f"The definition couldn't be read: {exc}"
        out.append(w)
    out.sort(key=lambda w: (w["type"], w["name"].lower(), w["item_id"]))
    return out
