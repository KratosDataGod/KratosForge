"""Pipelines straight from a Fabric workspace (Data Factory DF-2, engine half).

``workspace_pipelines`` lists a workspace's pipelines and ``workspace_pipeline_definition`` reads one
with getDefinition. Both are reads: no Fabric job starts and no capacity is used. The definition comes
back as ``text`` (the getDefinition result) that every existing pipeline call takes (pipeline_inspect,
pipeline_compatibility, pipeline_run_check, pipeline_run_start), plus what pipeline_import says about
it. Nothing is written to disk: a SecureString default stays in memory, as it would in a pasted
definition. See docs/orchestration/data-factory.md.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .orchestration.definition import DefinitionError, loads
from .pipeline_service import describe, failed

_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _guid(value: Any, what: str) -> str:
    text = str(value or "")
    if not _GUID.fullmatch(text):
        raise ValueError(f"Pick a {what}: its Fabric id is needed.")
    return text


class WorkspacePipelinesMixin:
    def workspace_pipelines(self, workspace_id: str) -> dict[str, Any]:
        """The pipelines in one workspace, by name. A read."""
        ws = _guid(workspace_id, "workspace")
        items = self.fabric.list_items(ws)  # type: ignore[attr-defined]
        rows = [{"workspace_id": ws, "item_id": it.get("id") or "", "name": it.get("displayName") or "",
                 "description": it.get("description") or "", "folder_id": it.get("folderId") or ""}
                for it in items if it.get("type") == "DataPipeline"]
        rows.sort(key=lambda r: r["name"].lower())
        return {"workspace_id": ws, "pipelines": rows}

    def workspace_pipeline_definition(self, workspace_id: str, item_id: str, name: str = "") -> dict[str, Any]:
        """One pipeline's definition from Fabric (getDefinition). A read; nothing is stored.

        Returns ``text`` for the other pipeline calls and ``pipeline``, what pipeline_import says.
        A definition this version can't read comes back with ``ok: False`` and the reason, never
        an exception, so the page can say what's wrong with it."""
        ws, item = _guid(workspace_id, "workspace"), _guid(item_id, "pipeline")
        result = self.fabric.get_item_definition(ws, item)  # type: ignore[attr-defined]
        text = json.dumps(result)
        try:
            pipeline = describe(loads(text, name_hint=name))
        except DefinitionError as exc:
            pipeline = failed(exc)
        return {"ok": bool(pipeline.get("ok")), "workspace_id": ws, "item_id": item,
                "name": pipeline.get("name") or name, "text": text, "pipeline": pipeline}
