"""Application services as an outside caller sees them: thin adapters over the engine's ``Service``.

Each function takes the engine's ``Service``, a ``CallContext`` and keyword arguments already
checked against the capability's schema (core.validate), calls the existing service method, and
shapes the result for a client that isn't the app's UI:

- results are plain JSON objects with stable field names, never bare lists;
- long lists are paged (``offset``, ``limit``, ``total``, ``next_offset``);
- nothing that is a credential is ever returned, and notebook text has secrets removed;
- rows of data are never returned by a metadata function.

No business logic lives here. If an adapter needs more than choosing fields, the logic belongs in
the service it calls, where the UI gets it too.
"""

from __future__ import annotations

import platform
from typing import Any

from .. import __version__
from ..fabric_run import compute_check
from .core import CallContext, IntegrationError, redact_secrets

MAX_PAGE = 500


def page(rows: list[Any], offset: int = 0, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_PAGE))
    offset = max(0, offset)
    chunk = rows[offset:offset + limit]
    end = offset + len(chunk)
    return {"items": chunk, "total": len(rows), "offset": offset, "next_offset": end if end < len(rows) else None}


def _lakehouse_ref(svc: Any, workspace_id: str, lakehouse_id: str) -> dict[str, str]:
    """What the engine's lakehouse functions take. The name is only used in messages and file names."""
    return {"workspace_id": workspace_id, "lakehouse_id": lakehouse_id, "name": lakehouse_id}


# ---- status and projects

def get_status(svc: Any, ctx: CallContext) -> dict[str, Any]:
    """Who is signed in and what this engine can do, without starting a sign-in."""
    user = tenant = None
    try:
        user, tenant = svc.auth.signed_in_user(), svc.auth.signed_in_tenant()
    except Exception:  # no sign-in yet, or no client ID: reported as not signed in
        pass
    return {"product": "Fabric Desktop by KratosBI", "version": __version__, "python": platform.python_version(),
            "signed_in": bool(user), "user": user, "tenant": tenant, "compute": "this PC",
            "caller": {"client_id": ctx.caller.client_id, "channel": ctx.caller.channel}}


def list_projects(svc: Any, ctx: CallContext) -> dict[str, Any]:
    """The work on this PC: the notebooks folder, and the metadata project if one is open."""
    projects = [{"id": "notebooks", "kind": "notebooks", "name": "Notebooks on this PC",
                 "items": len(svc.notebooks.list())}]
    meta = svc.meta_project()
    if meta.get("path"):
        projects.append({"id": "metadata", "kind": "metadata", "name": (meta.get("project") or {}).get("name") or "Metadata project",
                         "path": meta["path"], "entities": len(meta.get("entities", [])),
                         "layers": [l.get("name") for l in meta.get("layers", [])], "errors": len(meta.get("errors", []))})
    return {"projects": projects}


def get_project(svc: Any, ctx: CallContext, id: str) -> dict[str, Any]:  # noqa: A002 - the project id
    if id == "notebooks":
        return {"id": "notebooks", "kind": "notebooks", "notebooks": [
            {"name": n["name"], "modified": n["modified"]} for n in svc.notebooks.list()]}
    if id == "metadata":
        meta = svc.meta_project()
        if not meta.get("path"):
            raise IntegrationError("not_found", "No metadata project is open in Fabric Desktop by KratosBI.",
                                   hint="Open or create one on the Data mesh page first.")
        return {"id": "metadata", "kind": "metadata", "path": meta["path"], "version": meta.get("version"),
                "layers": meta.get("layers", []), "environments": _environments(meta.get("environments", {})),
                "entities": [_entity(e) for e in meta.get("entities", [])], "run_order": meta.get("run_order", []),
                "errors": meta.get("errors", [])}
    raise IntegrationError("not_found", f"No project {id!r}. Projects are listed by fabric_desktop_list_projects.")


def _environments(envs: dict[str, Any]) -> dict[str, Any]:
    keep = ("target", "compute", "workspace_id", "workspace_name", "tables_in", "runs_on")
    return {name: {k: v for k, v in (cfg or {}).items() if k in keep} for name, cfg in envs.items()}


def _entity(e: dict[str, Any]) -> dict[str, Any]:
    keep = ("name", "layer", "schema", "table", "owner", "mode", "source", "keys", "columns", "reads", "description",
            "checks", "clean", "derive", "freshness", "criticality")
    return {k: e.get(k) for k in keep if k in e}


def validate_project(svc: Any, ctx: CallContext) -> dict[str, Any]:
    """The open metadata project's own validation (the same list the Data mesh page shows)."""
    meta = svc.meta_project()
    if not meta.get("path"):
        raise IntegrationError("not_found", "No metadata project is open in Fabric Desktop by KratosBI.")
    errors = meta.get("errors", [])
    return {"project": "metadata", "ok": not errors, "errors": errors, "entities": len(meta.get("entities", []))}


# ---- Fabric workspaces and items

def list_workspaces(svc: Any, ctx: CallContext, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    rows = [{"id": w["id"], "name": w.get("displayName", ""), "type": w.get("type", "Workspace"),
             "capacity_id": w.get("capacityId")} for w in svc.list_workspaces()]
    return page(rows, offset, limit)


def list_artifacts(svc: Any, ctx: CallContext, workspace_id: str, type: str = "", offset: int = 0,  # noqa: A002
                   limit: int = 100) -> dict[str, Any]:
    rows = [{"id": i["id"], "name": i.get("displayName", ""), "type": i.get("type", ""),
             "description": i.get("description", "")} for i in svc.list_items(workspace_id)
            if not type or i.get("type", "").lower() == type.lower()]
    return page(rows, offset, limit)


def get_artifact_metadata(svc: Any, ctx: CallContext, workspace_id: str, item_id: str) -> dict[str, Any]:
    item = next((i for i in svc.list_items(workspace_id) if i["id"] == item_id), None)
    if item is None:
        raise IntegrationError("not_found", f"No item {item_id} in workspace {workspace_id}, or you can't see it.")
    out = {"id": item["id"], "name": item.get("displayName", ""), "type": item.get("type", ""),
           "description": item.get("description", ""), "workspace_id": workspace_id}
    if out["type"] == "Lakehouse":
        props = svc.fabric.get_lakehouse(workspace_id, item_id).get("properties") or {}
        out["lakehouse"] = {"schemas_enabled": bool(props.get("defaultSchema")), "default_schema": props.get("defaultSchema"),
                            "sql_endpoint_id": (props.get("sqlEndpointProperties") or {}).get("id")}
    return out


def list_tables(svc: Any, ctx: CallContext, workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
    by_schema = svc.list_tables(workspace_id, lakehouse_id)
    return {"workspace_id": workspace_id, "lakehouse_id": lakehouse_id,
            "tables": [{"schema": s, "name": t, "full_name": f"{s}.{t}"} for s, names in by_schema.items() for t in names]}


def get_table_schema(svc: Any, ctx: CallContext, workspace_id: str, lakehouse_id: str, table: str) -> dict[str, Any]:
    """Column names and Delta types only; no rows."""
    columns = svc.table_columns(_lakehouse_ref(svc, workspace_id, lakehouse_id), table)
    return {"workspace_id": workspace_id, "lakehouse_id": lakehouse_id, "table": table, "columns": columns}


# ---- notebooks on this PC

def list_notebooks(svc: Any, ctx: CallContext, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    return page([{"name": n["name"], "modified": n["modified"]} for n in svc.notebooks.list()], offset, limit)


def get_notebook(svc: Any, ctx: CallContext, name: str, include_source: bool = True) -> dict[str, Any]:
    """The notebook's cells (code with secrets removed; never outputs), lakehouse and run settings."""
    nb = svc.notebooks.load(name)
    cells = []
    for n, c in enumerate(nb["cells"], 1):
        cell = {"number": n, "cell_id": c.get("cell_id"), "type": c.get("type", "code"),
                "lines": len((c.get("source") or "").splitlines())}
        if include_source:
            cell["source"] = redact_secrets(c.get("source") or "")
        cells.append(cell)
    lakehouse = nb.get("lakehouse") or None
    return {"name": name, "cells": cells, "compute": nb.get("compute"), "workspace": nb.get("workspace"),
            "lakehouse": {k: lakehouse.get(k) for k in ("name", "workspace_id", "workspace_name", "lakehouse_id")} if lakehouse else None}


def validate_notebook(svc: Any, ctx: CallContext, name: str) -> dict[str, Any]:
    """Whether the notebook can run on this PC and in Fabric, what a proof would refuse, and which
    cells depend on which: the checks the notebook screen runs."""
    from ..proof import check as proof_check
    nb = svc.notebooks.load(name)
    cells, lakehouse = nb["cells"], nb.get("lakehouse")
    runs = compute_check(cells, lakehouse)
    proof = proof_check(cells, lakehouse)
    out = {"name": name, "this_pc": runs["local"], "fabric": runs["fabric"],
           "proof": {"ok": proof["ok"], "blockers": proof["blockers"], "warnings": proof["warnings"]}}
    try:
        out["dependencies"] = svc.notebook_dependencies(cells)
    except Exception as exc:  # the dependency view is extra; the checks above still stand
        out["dependencies"] = {"error": redact_secrets(str(exc))}
    out["ok"] = bool(runs["local"].get("ok"))
    return out
