"""The Microsoft Fabric Capacity Metrics app's exported data (the first usage provider).

A capacity admin opens the app, exports a table with the report's own Export data command and
hands the CSV to a developer. Nothing here reads the app's semantic model: Microsoft supports it
only for the app's own reports. Column names are the ones Microsoft Learn documents for the app's
tables:

- **Timepoint** page, interactive and background operations: Workspace, Item kind, Item name,
  Operation, Start, End, Status, User, Duration (s), Total CU (s), Timepoint CU (s), Billing
  type, Operation ID. One row per operation, with its own times. ``Total CU (s)`` is the
  operation's use; ``Timepoint CU (s)`` is only the share smoothed into one 30-second timepoint.
- **Compute** page, matrix by item: Workspace, Item kind, Item name, CU (s), Duration (s), Users,
  Billing type. One row per item, totaled over the period the page showed, which the file
  doesn't state, so the person gives it.

Learn notes that exports can be sampled, so a file may not hold every operation; every row says so.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, ClassVar

from .. import measurement as ms
from . import reader
from .providers import Context, Layout, Provider

SAMPLED = ("Exports from the Capacity Metrics app can be sampled (Microsoft Learn: Capacity Metrics app, "
           "considerations and limitations), so a file may not hold every operation.")

_COMMON = {
    "workspace": ("Workspace", "Workspace name"),
    "item": ("Item name", "Item"),
    "item_kind": ("Item kind", "Item type"),
    "duration": ("Duration (s)",),
    "billing": ("Billing type",),
}

OPERATIONS = Layout(
    id="operations", label="Operations (Timepoint page)", timed=True,
    columns=_COMMON | {"operation": ("Operation", "Operation name"), "operation_id": ("Operation ID", "OperationId"),
                       "start": ("Start", "Start time"), "end": ("End", "End time"), "status": ("Status",),
                       "cu": ("Total CU (s)",)},
    required=("workspace", "item", "cu", "start"),
    refuse={"Timepoint CU (s)": "Timepoint CU (s) is the part of an operation's use counted in one 30-second "
                                "timepoint, not the operation's own use: export the table with its Total CU (s) column."})

ITEMS = Layout(
    id="items", label="Items (Compute page)", timed=False,
    columns=_COMMON | {"cu": ("CU (s)",)},
    required=("workspace", "item", "cu"))


def _billable(text: str) -> bool | None:
    v = reader.normal(text)
    return {"billable": True, "nonbillable": False}.get(v)


def _key(*parts: Any) -> str:
    return "metrics_app:" + hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:24]


class MetricsApp(Provider):
    id = "metrics_app"
    label = "Capacity Metrics app export"
    layouts = (OPERATIONS, ITEMS)
    private: ClassVar[dict[str, str]] = {"User": "People's names aren't stored.", "Users": "People counts aren't stored."}
    about = ("a CSV exported from the Timepoint page's operations table (with Total CU (s)) or the Compute page's "
             "items matrix (with CU (s))")

    def row(self, cells: dict[str, str], layout: Layout, ctx: Context) -> dict[str, Any] | str:
        cu = reader.number(cells.get("cu", ""), ctx.decimal)
        if cu is None:
            return "No CU value" if not cells.get("cu") else "The CU value isn't a number"
        if cu < 0:
            return "Negative CU value"
        workspace, item = cells.get("workspace", ""), cells.get("item", "")
        if not workspace and not item:
            return "No workspace or item"
        start = end = None
        if layout.timed:
            start = reader.when(cells.get("start", ""), ctx.date_order, ctx.tz)
            if start is None:
                return "No start time" if not cells.get("start") else "The start time can't be read"
            end = reader.when(cells.get("end", ""), ctx.date_order, ctx.tz)
            if cells.get("end") and end is None:
                return "The end time can't be read"
            if end and end < start:
                return "Ends before it starts"
            p_start, p_end = start, end or start
        else:
            p_start, p_end = ctx.period
        op_id = cells.get("operation_id", "").strip()
        kind = cells.get("item_kind", "")
        if layout.timed:
            key = f"metrics_app:op:{op_id.lower()}" if op_id else _key(workspace, item, kind, cells.get("operation"),
                                                                        reader.iso(start), reader.iso(end), cu)
        else:
            key = _key("items", workspace, item, kind, reader.iso(p_start), reader.iso(p_end), cu)
        metric = "fabric_operation_cu" if layout.timed else "fabric_item_cu"
        m = ms.make(ms.MEASURED, metric, ms.CU_SECONDS, cu, source=self.source(ctx),
                    method=f"{layout.columns['cu'][0]} column of the {layout.label} export",
                    collected_at=ctx.imported_at, period_start=p_start, period_end=p_end,
                    uncertainty=SAMPLED, reconciliation="unreconciled")
        return {"key": key, "provider": self.id, "layout": layout.id, "workspace": workspace, "item": item,
                "item_kind": kind or None, "operation": cells.get("operation") or None, "operation_id": op_id or None,
                "status": cells.get("status") or None, "billable": _billable(cells.get("billing", "")),
                "duration_s": reader.number(cells.get("duration", ""), ctx.decimal),
                "start": reader.iso(start), "end": reader.iso(end), "cu": m}
