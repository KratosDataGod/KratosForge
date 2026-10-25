"""Dataflow Gen2 from a Fabric workspace (Data Factory DF-7, engine half): reads only.

``workspace_dataflows`` lists a workspace's dataflows, ``workspace_dataflow_definition`` reads one with
getDefinition and returns its Power Query (M) queries, where each loads and the connections it names,
and ``fabric_dataflow_refreshes`` lists its refresh history. None of them starts a refresh or uses
capacity: refreshing runs in Fabric and comes later, behind a confirm and the risk check. M text passes
through redact.secrets, since a query can carry a key inline (a Web.Contents header, say), and nothing
is written to disk. See docs/orchestration/data-factory.md.
"""

from __future__ import annotations

import re
from typing import Any

from .fabric_pipeline_runs_service import run_row
from .orchestration import mashup
from .redact import secrets

_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
REFRESHES = 50  # newest refreshes listed


def _guid(value: Any, what: str) -> str:
    text = str(value or "")
    if not _GUID.fullmatch(text):
        raise ValueError(f"Pick a {what}: its Fabric id is needed.")
    return text


class WorkspaceDataflowsMixin:
    def workspace_dataflows(self, workspace_id: str) -> dict[str, Any]:
        """The Dataflow Gen2 items in one workspace, by name. A read."""
        ws = _guid(workspace_id, "workspace")
        rows = [{"workspace_id": ws, "item_id": it.get("id") or "", "name": it.get("displayName") or "",
                 "description": it.get("description") or "", "folder_id": it.get("folderId") or ""}
                for it in self.fabric.list_items(ws) if it.get("type") == "Dataflow"]  # type: ignore[attr-defined]
        rows.sort(key=lambda r: r["name"].lower())
        return {"workspace_id": ws, "dataflows": rows}

    def workspace_dataflow_definition(self, workspace_id: str, item_id: str, name: str = "") -> dict[str, Any]:
        """One dataflow's queries, destinations and connections (getDefinition). A read; nothing is stored.

        A definition this version can't read (a Gen1 dataflow, say) comes back with ``ok: False`` and
        the reason in ``problems``, never an exception."""
        ws, item = _guid(workspace_id, "workspace"), _guid(item_id, "dataflow")
        out = mashup.describe(self.fabric.get_item_definition(ws, item))  # type: ignore[attr-defined]
        for q in out["queries"]:
            q["m"] = secrets(q["m"])
        out["mashup"] = secrets(out["mashup"])
        for c in out["connections"]:
            c["path"] = secrets(c["path"])
        return {"workspace_id": ws, "item_id": item, "name": name, **out}

    def fabric_dataflow_refreshes(self, workspace_id: str, item_id: str, name: str = "") -> dict[str, Any]:
        """A dataflow's refreshes in Fabric, newest first, in the shape Monitor > Runs lists. A read."""
        ws, item = _guid(workspace_id, "workspace"), _guid(item_id, "dataflow")
        jobs = self.fabric.list_job_instances(ws, item)  # type: ignore[attr-defined]
        owner = {"workspace_id": ws, "workspace_name": "", "id": item, "displayName": name}
        rows = [{**run_row(j, owner), "kind": "dataflow"} for j in jobs[:REFRESHES]]
        return {"workspace_id": ws, "item_id": item, "refreshes": rows}
