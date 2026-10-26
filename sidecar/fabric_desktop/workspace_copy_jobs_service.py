"""Copy jobs from a Fabric workspace (Data Factory DF-6, engine half): reads only.

``workspace_copy_jobs`` lists a workspace's copy jobs, ``workspace_copy_job_definition`` reads one with
getDefinition and returns its source, destination and tables, with whether a run on this PC could take
it (``local_check``), and ``fabric_copy_job_runs`` lists its runs in Fabric. None of them starts a run
or uses capacity: running comes later, behind a confirm and the risk check. Text fields pass through
redact.secrets and nothing is written to disk. See docs/orchestration/data-factory.md.
"""

from __future__ import annotations

from typing import Any

from .fabric_pipeline_runs_service import run_row
from .orchestration import copyjob
from .redact import secrets
from .workspace_dataflows_service import _guid

RUNS = 50  # newest runs listed


class WorkspaceCopyJobsMixin:
    def workspace_copy_jobs(self, workspace_id: str) -> dict[str, Any]:
        """The copy jobs in one workspace, by name. A read."""
        ws = _guid(workspace_id, "workspace")
        rows = [{"workspace_id": ws, "item_id": it.get("id") or "", "name": it.get("displayName") or "",
                 "description": it.get("description") or "", "folder_id": it.get("folderId") or ""}
                for it in self.fabric.list_items(ws) if it.get("type") == "CopyJob"]  # type: ignore[attr-defined]
        rows.sort(key=lambda r: r["name"].lower())
        return {"workspace_id": ws, "copy_jobs": rows}

    def workspace_copy_job_definition(self, workspace_id: str, item_id: str, name: str = "") -> dict[str, Any]:
        """One copy job's source, destination and tables (getDefinition). A read; nothing is stored.

        A definition this version can't read comes back with ``ok: False`` and the reason in
        ``problems``, never an exception."""
        ws, item = _guid(workspace_id, "workspace"), _guid(item_id, "copy job")
        out = copyjob.describe(self.fabric.get_item_definition(ws, item))  # type: ignore[attr-defined]
        for end in (out["source"], out["destination"]):
            end["database"] = secrets(end["database"])
        for t in out["tables"]:
            for side in (t["source"], t["destination"]):
                side["folder"] = secrets(side["folder"])
        return {"workspace_id": ws, "item_id": item, "name": name, **out}

    def fabric_copy_job_runs(self, workspace_id: str, item_id: str, name: str = "") -> dict[str, Any]:
        """A copy job's runs in Fabric, newest first, in the shape Monitor > Runs lists. A read."""
        ws, item = _guid(workspace_id, "workspace"), _guid(item_id, "copy job")
        jobs = self.fabric.list_job_instances(ws, item)  # type: ignore[attr-defined]
        owner = {"workspace_id": ws, "workspace_name": "", "id": item, "displayName": name}
        return {"workspace_id": ws, "item_id": item, "runs": [{**run_row(j, owner), "kind": "copy job"} for j in jobs[:RUNS]]}
