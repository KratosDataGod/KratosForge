"""Optimization assessment of a workspace or a capacity (OE-001, engine only; the screen waits on a mockup).

``optimization_assess`` reads, in this order and nothing else: the workspace (or the workspaces on
a capacity that the signed-in person can see), its items, the definitions of its notebooks and
pipelines (getDefinition), and their job history. No Fabric job starts and nothing is written to
Fabric or OneLake. Imported usage files and the cost settings on this PC give CU and price.
``demo=True`` runs the labelled demonstration fixture instead, with no Fabric call at all.

The result carries no notebook code or pipeline JSON beyond the evidence each opportunity cites.
See docs/optimize/engine.md.
"""

from __future__ import annotations

import datetime as dt
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .opportunities import assess, fixture, inventory
from .redact import describe

_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MAX_WORKSPACES = 25  # per capacity assessment; the result says when more were left out
MAX_ITEMS = 300  # definitions and histories read per workspace
WORKERS = 4
MAX_CALLS = 1500  # Fabric reads per assessment, all workspaces together
MAX_SECONDS = 240.0  # wall time for those reads; what's left unread is reported, never guessed
MIN_DAYS, MAX_DAYS = 1, 90
METHODS = {"optimization_assess"}


class _Budget:
    """How many Fabric reads one assessment may still make, and until when. Shared by the workers."""

    def __init__(self, calls: int, seconds: float):
        self.calls, self.deadline, self.used = calls, time.monotonic() + seconds, 0
        self.spent = False
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.used >= self.calls or time.monotonic() >= self.deadline:
                self.spent = True
                return False
            self.used += 1
            return True

    def report(self) -> dict[str, Any]:
        return {"max_calls": self.calls, "max_seconds": MAX_SECONDS, "calls": self.used, "reached": self.spent}


def _guid(value: Any, what: str) -> str:
    text = str(value or "")
    if not _GUID.fullmatch(text):
        raise ValueError(f"Pick a {what}: its Fabric id is needed.")
    return text.lower()


class OpportunitiesMixin:
    def optimization_assess(self, workspace_id: str = "", capacity_id: str = "", days: int = 30,
                            demo: bool = False) -> dict[str, Any]:
        """Find optimization opportunities in one workspace, or in every workspace on a capacity you can see.

        Reads only. With ``demo`` the demonstration fixture is assessed instead, labelled as a fixture."""
        if demo:
            d = fixture.load()
            return assess.assess([d["source"]], usage_imports=d["usage_imports"],
                                 price=d["price"] | {"source": "demonstration fixture price"}, now=d["now"], days=30,
                                 scope={"kind": "demo"}, fixture=True)
        days = min(max(int(days), MIN_DAYS), MAX_DAYS)
        fabric = self.fabric  # type: ignore[attr-defined]
        truncated = 0
        if workspace_id:
            ws = _guid(workspace_id, "workspace")
            got = fabric.get_workspace(ws)
            workspaces = [{"id": got.get("id") or ws, "displayName": got.get("displayName") or "",
                           "capacityId": got.get("capacityId")}]
            scope = {"kind": "workspace", "workspace_id": ws}
        elif capacity_id:
            cap = _guid(capacity_id, "capacity")
            on = [w for w in fabric.list_workspaces() if str(w.get("capacityId") or "").lower() == cap]
            on.sort(key=lambda w: str(w.get("displayName") or "").lower())
            truncated = max(len(on) - MAX_WORKSPACES, 0)
            workspaces = on[:MAX_WORKSPACES]
            scope = {"kind": "capacity", "capacity_id": cap,
                     "note": "Only the workspaces you can see on this capacity are read."}
        else:
            raise ValueError("Pick a workspace or a capacity to assess.")
        budget = _Budget(MAX_CALLS, MAX_SECONDS)
        sources = [self._opportunity_source(fabric, w, budget) for w in workspaces]
        cost = self._cost_settings()  # type: ignore[attr-defined]
        saved = (self._settings().get("cost") or {})  # type: ignore[attr-defined]
        price = {"price_per_cu_hour": cost.get("price_per_cu_hour"), "currency": cost.get("currency"),
                 "source": "the price set in Optimize > Cost and savings" if saved.get("price_per_cu_hour")
                 else "the default list price, not your contract price"}
        result = assess.assess(sources, usage_imports=self._usage.all(), price=price,  # type: ignore[attr-defined]
                               now=dt.datetime.now(dt.timezone.utc), days=days, scope=scope)
        result["scope"]["workspaces_left_out"] = truncated
        result["scope"]["budget"] = budget.report()
        if budget.spent:
            result["scope"]["note"] = (f"The read budget ({MAX_CALLS} Fabric calls or {MAX_SECONDS:.0f} s) ran out; "
                                       "items past it are listed as not read. Assess fewer workspaces to cover them.")
        return result

    def _opportunity_source(self, fabric: Any, workspace: dict[str, Any], budget: _Budget) -> dict[str, Any]:
        """One workspace's items, definitions and job history. A failed read is recorded, never raised,
        so one unreadable item doesn't stop the assessment."""
        ws = str(workspace.get("id") or "")
        if not budget.take():
            return {"workspace": workspace, "items": [], "problem": "Not read: the assessment's read budget ran out."}
        try:
            items = fabric.list_items(ws)
        except Exception as exc:  # noqa: BLE001 - the workspace is reported as unread
            return {"workspace": workspace, "items": [], "problem": f"Its items couldn't be listed: {describe(exc)}"}
        wanted = [it for it in items if it.get("type") in inventory.ANALYZED][:MAX_ITEMS]

        def definition(it: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            if not budget.take():
                return it["id"], {"error": "Not read: the assessment's read budget ran out."}
            try:
                fmt = "ipynb" if it.get("type") == "Notebook" else None
                return it["id"], fabric.get_item_definition(ws, it["id"], fmt)
            except Exception as exc:  # noqa: BLE001 - recorded on the workload
                return it["id"], {"error": describe(exc)}

        def history(it: dict[str, Any]) -> tuple[str, list[dict[str, Any]] | None]:
            if not budget.take():
                return it["id"], None
            try:
                return it["id"], fabric.list_job_instances(ws, it["id"])
            except Exception:  # noqa: BLE001 - None means "not read", which the workload shows
                return it["id"], None

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            definitions = dict(pool.map(definition, wanted))
            jobs = dict(pool.map(history, wanted))
        left = len([it for it in items if it.get("type") in inventory.ANALYZED]) - len(wanted)
        return {"workspace": workspace, "items": items, "definitions": definitions, "jobs": jobs,
                "problem": f"{left} item(s) past the first {MAX_ITEMS} weren't read." if left > 0 else None}
