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

import hashlib
import json
import logging
import platform
from typing import Any

from .. import __version__, notebook_cells
from ..auth import default_cache_path
from ..fabric_run import compute_check
from ..redact import describe
from . import scope
from .core import CallContext, IntegrationError, redact_secrets

_log = logging.getLogger(__name__)

MAX_PAGE = 500
ALL = 100_000  # services that take a limit: ask for everything, then page here so total is right


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
        # Nobody has signed in on this PC: say so without building the sign-in client, which asks
        # Microsoft for its endpoints. A tool calls this first, often several times at once.
        if getattr(svc, "_auth", None) is not None or default_cache_path().exists():
            user, tenant = svc.auth.signed_in_user(), svc.auth.signed_in_tenant()
    except Exception as exc:  # no sign-in yet, or no client ID: reported as not signed in
        _log.info("Not signed in: %s", describe(exc))
    return {"product": "KratosForge", "version": __version__, "python": platform.python_version(),
            "signed_in": bool(user), "user": user, "tenant": tenant, "compute": "this PC",
            "caller": {"client_id": ctx.caller.client_id, "channel": ctx.caller.channel}}


def _notebooks(svc: Any, ctx: CallContext) -> list[dict[str, Any]]:
    """The saved notebooks a caller may see: for a tool limited to some workspaces, those whose
    lakehouse and publish workspace are in them (or that have neither)."""
    return scope.keep(ctx, svc.notebooks.list(), lambda n: scope.notebook_of(svc.notebooks.load(n["name"])))


def list_projects(svc: Any, ctx: CallContext) -> dict[str, Any]:
    """The work on this PC: the notebooks folder, and the metadata project if one is open."""
    projects = [{"id": "notebooks", "kind": "notebooks", "name": "Notebooks on this PC",
                 "items": len(_notebooks(svc, ctx))}]
    meta = svc.meta_project()
    if meta.get("path") and scope.keep(ctx, [meta], scope.metadata_of):
        projects.append({"id": "metadata", "kind": "metadata", "name": (meta.get("project") or {}).get("name") or "Metadata project",
                         "path": meta["path"], "entities": len(meta.get("entities", [])),
                         "layers": [l.get("name") for l in meta.get("layers", [])], "errors": len(meta.get("errors", []))})
    return {"projects": projects}


def get_project(svc: Any, ctx: CallContext, id: str) -> dict[str, Any]:  # noqa: A002 - the project id
    if id == "notebooks":
        return {"id": "notebooks", "kind": "notebooks", "notebooks": [
            {"name": n["name"], "modified": n["modified"]} for n in _notebooks(svc, ctx)]}
    if id == "metadata":
        meta = svc.meta_project()
        if not meta.get("path"):
            raise IntegrationError("not_found", "No metadata project is open in KratosForge.",
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
        raise IntegrationError("not_found", "No metadata project is open in KratosForge.")
    errors = meta.get("errors", [])
    return {"project": "metadata", "ok": not errors, "errors": errors, "entities": len(meta.get("entities", []))}


# ---- Fabric workspaces and items

def list_workspaces(svc: Any, ctx: CallContext, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    rows = [{"id": w["id"], "name": w.get("displayName", ""), "type": w.get("type", "Workspace"),
             "capacity_id": w.get("capacityId")} for w in svc.list_workspaces()]
    return page(scope.keep(ctx, rows, lambda w: {w["id"]}), offset, limit)


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
    return page([{"name": n["name"], "modified": n["modified"]} for n in _notebooks(svc, ctx)], offset, limit)


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
    runs = compute_check(cells, lakehouse, **svc._checks(name))
    proof = proof_check(cells, lakehouse, **svc._checks(name))
    out = {"name": name, "this_pc": runs["local"], "fabric": runs["fabric"],
           "proof": {"ok": proof["ok"], "blockers": proof["blockers"], "warnings": proof["warnings"]}}
    try:
        out["dependencies"] = svc.notebook_dependencies(cells)
    except Exception as exc:  # the dependency view is extra; the checks above still stand
        out["dependencies"] = {"error": redact_secrets(str(exc))}
    # Validate notebook's code checks (validate.py): every finding with its severity, cell and fix.
    checked = svc.notebook_validate_check(name, cells, lakehouse, nb.get("compute"))
    out |= {"findings": checked["findings"], "counts": checked["counts"], "verdict": checked["verdict"]}
    out["ok"] = bool(runs["local"].get("ok"))
    return out


# ---- Prove this notebook: plans, proofs, receipts and side-by-side runs, as metadata

def _metadata_only(record: dict[str, Any]) -> dict[str, Any]:
    """A proof, side-by-side run or receipt without data: cell outputs (which can print rows) are
    left out and the example rows of a verification are hidden (receipts.REDACTIONS "values")."""
    from ..receipts import redact
    out = redact(record, ["values"])
    for side in ("pc", "local"):
        cells = (out.get(side) or {}).get("cells")
        if isinstance(cells, dict):
            for cell in cells.values():
                if isinstance(cell, dict):
                    cell.pop("outputs", None)
    out.pop("_cancel", None)
    return out


def get_notebook_dependencies(svc: Any, ctx: CallContext, name: str) -> dict[str, Any]:
    return {"name": name, "dependencies": svc.notebook_dependencies(svc.notebooks.load(name)["cells"])}


def get_notebook_environment(svc: Any, ctx: CallContext, name: str) -> dict[str, Any]:
    nb = svc.notebooks.load(name)
    return {"name": name, "environment": svc.notebook_environment(name, nb["cells"], nb.get("lakehouse"), nb.get("compute"),
                                                                  nb.get("workspace"))}


def validate_pipeline(svc: Any, ctx: CallContext, definition: str) -> dict[str, Any]:
    return svc.pipeline_validate(text=definition)


def inspect_pipeline(svc: Any, ctx: CallContext, definition: str) -> dict[str, Any]:
    return svc.pipeline_inspect(text=definition)


def export_pipeline(svc: Any, ctx: CallContext, definition: str, format: str = "") -> dict[str, Any]:  # noqa: A002
    return svc.pipeline_export(text=definition, format=format or None)


def list_semantic_models(svc: Any, ctx: CallContext, env: str = "test") -> dict[str, Any]:
    """The semantic models in a metadata environment's workspace."""
    return svc.meta_model_ossie_models(env)


def export_semantic_model_ossie(svc: Any, ctx: CallContext, env: str, name: str, translate_dax: bool = True) -> dict[str, Any]:
    """A semantic model as Apache Ossie YAML, with counts and what isn't portable. Writes no file."""
    return svc.meta_model_ossie_export(env, name, translate_dax)


def preview_ossie_import(svc: Any, ctx: CallContext, env: str, text: str, name: str = "", mode: str = "update",
                         remove: list[str] | None = None) -> dict[str, Any]:
    """What importing an Ossie document would change in a semantic model. Writes nothing."""
    return svc.meta_model_ossie_preview(env, text, name, mode, remove or [])


def check_run_risk(svc: Any, ctx: CallContext, workload_id: str, action: str, env: str = "") -> dict[str, Any]:
    """The predeployment risk check (FINOPS-004) for one action on one workload."""
    return svc.risk_check(workload_id, action, env or None)


def get_placement_advice(svc: Any, ctx: CallContext, name: str) -> dict[str, Any]:
    """Where to run a notebook, on this PC or in Fabric, with the evidence (FINOPS-005). Changes nothing."""
    return svc.placement_advice(name)


def check_pipeline_compatibility(svc: Any, ctx: CallContext, definition: str) -> dict[str, Any]:
    return svc.pipeline_compatibility(text=definition)


def get_activity_registry(svc: Any, ctx: CallContext, activity_type: str = "") -> dict[str, Any]:
    return svc.pipeline_activity_registry(activity_type or None)


# Steps the desktop app and CLI can run (naming a notebook, or with lakehouse reads on) but an external
# tool can only mock: their problems name CLI flags the tool can't pass, so it gets this instead.
_MOCK_ONLY = {"notebook_needs_engine": "runs a notebook", "data_needs_engine": "reads a lakehouse"}


def _external(result: dict[str, Any]) -> dict[str, Any]:
    for p in result.get("problems") or []:
        what = _MOCK_ONLY.get(p.get("code"))
        if what:
            p["message"] = (f"{p.get('activity')} {what}, which an external tool's run can't do. Give a mock for "
                            "it, or run the pipeline in KratosForge.")
    return result


def check_pipeline_run(svc: Any, ctx: CallContext, definition: str, parameters: dict | None = None,
                       mocks: dict | None = None, children: dict | None = None) -> dict[str, Any]:
    return _external(svc.pipeline_run_check(text=definition, parameters=parameters, mocks=mocks, children=children))


def start_pipeline_run(svc: Any, ctx: CallContext, definition: str, parameters: dict | None = None,
                       mocks: dict | None = None, children: dict | None = None, max_parallel: int = 4,
                       wait_scale: float = 1.0, timeout_seconds: float = 0) -> dict[str, Any]:
    return _external(svc.pipeline_run_start(text=definition, parameters=parameters, mocks=mocks, children=children,
                                            max_parallel=max_parallel, wait_scale=wait_scale,
                                            timeout_seconds=timeout_seconds or None))


def get_pipeline_run(svc: Any, ctx: CallContext, id: str) -> dict[str, Any]:  # noqa: A002
    return svc.pipeline_run_status(id, values=False)


def list_pipeline_runs(svc: Any, ctx: CallContext, limit: int = 20) -> dict[str, Any]:
    return svc.pipeline_runs(limit)


def cancel_pipeline_run(svc: Any, ctx: CallContext, id: str) -> dict[str, Any]:  # noqa: A002
    return svc.pipeline_run_cancel(id)


def check_proof(svc: Any, ctx: CallContext, name: str) -> dict[str, Any]:
    nb = svc.notebooks.load(name)
    return {"name": name} | svc.prove_check(name, nb["cells"], nb.get("lakehouse"), nb.get("compute"), nb.get("workspace"))


def list_proofs(svc: Any, ctx: CallContext, name: str = "", offset: int = 0, limit: int = 20) -> dict[str, Any]:
    rows = scope.keep(ctx, svc.prove_list(name or None, limit=ALL), scope.record_of)
    return page([_metadata_only(r) for r in rows], offset, limit)


def get_proof(svc: Any, ctx: CallContext, id: str) -> dict[str, Any]:  # noqa: A002 - the proof id
    return _metadata_only(svc.prove_status(id))


def list_receipts(svc: Any, ctx: CallContext, name: str = "", offset: int = 0, limit: int = 50) -> dict[str, Any]:
    rows = scope.keep(ctx, svc.receipts_list(name or None, limit=ALL), lambda r: scope.receipt_of(svc, r["id"]))
    return page(rows, offset, limit)


def get_receipt(svc: Any, ctx: CallContext, id: str) -> dict[str, Any]:  # noqa: A002 - the receipt id
    got = svc.receipt_get(id)
    return {"id": got["id"], "receipt": _metadata_only(got["receipt"]), "hidden": ["values"]}


def list_side_by_side(svc: Any, ctx: CallContext, name: str = "", offset: int = 0, limit: int = 20) -> dict[str, Any]:
    rows = scope.keep(ctx, svc.side_by_side_list(name or None, limit=ALL), scope.record_of)
    return page([_metadata_only(r) for r in rows], offset, limit)


def get_side_by_side(svc: Any, ctx: CallContext, id: str) -> dict[str, Any]:  # noqa: A002 - the run id
    return _metadata_only(svc.side_by_side_status(id))


# ---- executions (executions.py): SQL, Python and notebooks run for an external tool

def _executions(svc: Any) -> Any:
    from .executions import ExecutionManager
    if getattr(svc, "_integration_executions", None) is None:
        svc._integration_executions = ExecutionManager(svc)
    return svc._integration_executions


def _view(found: Any) -> dict[str, Any]:
    return found.view() if hasattr(found, "view") else found


def execute_sql(svc: Any, ctx: CallContext, workspace_id: str, lakehouse_id: str, query: str, max_rows: int = 100,
                wait_seconds: float = 30, timeout_seconds: float = 120) -> dict[str, Any]:
    from .executions import Execution
    mgr = _executions(svc)
    run = Execution("sql", ctx.caller.client_id, query.strip().splitlines()[0][:120] if query.strip() else "query",
                    {"workspace_id": workspace_id, "lakehouse_id": lakehouse_id}, timeout_seconds)
    ref = _lakehouse_ref(svc, workspace_id, lakehouse_id)
    return mgr.start(run, lambda r: mgr.run_sql(r, query, ref, max_rows), ctx, wait_seconds)


def execute_python(svc: Any, ctx: CallContext, code: str, workspace_id: str = "", lakehouse_id: str = "",
                   wait_seconds: float = 30, timeout_seconds: float = 300) -> dict[str, Any]:
    from .executions import Execution
    if bool(workspace_id) != bool(lakehouse_id):
        raise ValueError("Give both workspace_id and lakehouse_id to attach a lakehouse, or neither.")
    mgr = _executions(svc)
    ref = _lakehouse_ref(svc, workspace_id, lakehouse_id) if lakehouse_id else None
    run = Execution("python", ctx.caller.client_id, f"{len(code.splitlines())} lines of Python",
                    {"workspace_id": workspace_id, "lakehouse_id": lakehouse_id} if ref else {}, timeout_seconds)
    return mgr.start(run, lambda r: mgr.run_cells(r, [code], ref), ctx, wait_seconds)


NOTEBOOK_CHANGED = "The notebook changed after it was approved. Ask again."


def _notebook_code(svc: Any, name: str, workspace_id: str) -> tuple[list[str], dict[str, Any] | None, str]:
    """The code cells a run of the saved notebook would run, its lakehouse, and what it skips (a
    cell marked Run on Fabric runs only on Fabric Spark)."""
    nb = svc.notebooks.load(name)
    lakehouse = nb.get("lakehouse") or None
    if lakehouse and lakehouse.get("workspace_id") != workspace_id:
        raise ValueError(f"{name} reads a lakehouse in workspace {lakehouse.get('workspace_id')}; pass that as workspace_id.")
    cells = [c.get("source") or "" for c in nb["cells"] if notebook_cells.runs(c, skip_fabric_step=True)]
    skipped = notebook_cells.skipped_step(nb["cells"])
    if not cells:
        raise ValueError(f"{name} has no code to run." + (f" {skipped}" if skipped else ""))
    return cells, lakehouse, skipped


def _cells_sha256(cells: list[str]) -> str:
    return hashlib.sha256(json.dumps(cells, ensure_ascii=False).encode()).hexdigest()


def bind_notebook(svc: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Read before the person approves a run: the code cells, their SHA-256 and the lakehouse, so
    the approval shows the code and is bound to this version of the notebook (Capability.bind)."""
    cells, lakehouse, _ = _notebook_code(svc, arguments["name"], arguments.get("workspace_id", ""))
    return {"cells": cells, "cells_sha256": _cells_sha256(cells), "lakehouse_id": (lakehouse or {}).get("lakehouse_id", "")}


def run_notebook(svc: Any, ctx: CallContext, name: str, workspace_id: str = "", wait_seconds: float = 30,
                 timeout_seconds: float = 900, *, cells: list[str], cells_sha256: str, lakehouse_id: str) -> dict[str, Any]:
    """Run a notebook's code cells on this PC, in order, in a fresh process. ``workspace_id`` must
    be the attached lakehouse's workspace, so a tool limited to some workspaces can't run a notebook
    that reads another. ``cells``, ``cells_sha256`` and ``lakehouse_id`` are what bind_notebook
    read and the person approved: exactly those cells run, and only if the saved notebook still
    matches them."""
    from .executions import Execution
    current, lakehouse, skipped = _notebook_code(svc, name, workspace_id)
    if (_cells_sha256(cells) != cells_sha256 or _cells_sha256(current) != cells_sha256
            or (lakehouse or {}).get("lakehouse_id", "") != lakehouse_id):
        raise IntegrationError("approval_required", NOTEBOOK_CHANGED, retryable=True,
                               hint="Call again so the person can approve the notebook as it is now.")
    mgr = _executions(svc)
    run = Execution("notebook", ctx.caller.client_id, name, {"notebook": name, "workspace_id": workspace_id}, timeout_seconds)
    return mgr.start(run, lambda r: mgr.run_cells(r, list(cells), lakehouse, notebook=name, skipped=skipped), ctx, wait_seconds)


def get_execution_status(svc: Any, ctx: CallContext, execution_id: str, wait_seconds: float = 0) -> dict[str, Any]:
    found = _executions(svc).get(execution_id, ctx.caller.client_id)
    if hasattr(found, "done") and wait_seconds:
        found.done.wait(min(wait_seconds, max(0.0, ctx.remaining() - 1)))
    out = _view(found)
    out.pop("logs", None)
    return out


def get_execution_logs(svc: Any, ctx: CallContext, execution_id: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    found = _executions(svc).get(execution_id, ctx.caller.client_id)
    logs = found.logs if hasattr(found, "logs") and not isinstance(found, dict) else found.get("logs", [])
    return {"id": execution_id} | page(list(logs), offset, limit)


def list_executions(svc: Any, ctx: CallContext, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    rows = scope.keep(ctx, _executions(svc).list(ctx.caller.client_id, limit=ALL), scope.execution_of)
    return page(rows, offset, limit)


def cancel_execution(svc: Any, ctx: CallContext, execution_id: str) -> dict[str, Any]:
    return _executions(svc).cancel(execution_id, ctx.caller.client_id)


# ---- Prove this notebook: actions that ask the person first

def _proof_inputs(nb: dict[str, Any], inputs: dict[str, Any]) -> str:
    """SHA-256 of what a proof uses: the cells, the lakehouse, the compute, where it publishes and
    the Parameters bar's values, which both of its runs use."""
    used = [nb.get("cells"), nb.get("lakehouse"), nb.get("compute"), nb.get("workspace"), inputs]
    return hashlib.sha256(json.dumps(used, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _bar_inputs(svc: Any, name: str) -> dict[str, Any]:
    """The Parameters and Widgets bar's values kept with the notebook, as the person set them."""
    return svc._saved_inputs(name) or {}


def bind_proof(svc: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Read before the person approves a proof, so the approval is bound to this version of the
    notebook, its lakehouse and the bar's values it runs with (Capability.bind), as bind_notebook
    does for a run. The values are shown with the approval."""
    inputs = _bar_inputs(svc, arguments["name"])
    return {"notebook_sha256": _proof_inputs(svc.notebooks.load(arguments["name"]), inputs), "inputs": inputs}


def start_proof(svc: Any, ctx: CallContext, name: str, *, notebook_sha256: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Starts only if the saved notebook and the bar's values still match what was checked and
    approved: a lakehouse changed during the approval wait would reach a workspace the tool's limit
    never saw, and a changed value would run something the person wasn't shown."""
    nb = svc.notebooks.load(name)
    current = _bar_inputs(svc, name)
    if _proof_inputs(nb, current) != notebook_sha256 or _proof_inputs(nb, inputs) != notebook_sha256:
        raise IntegrationError("approval_required", NOTEBOOK_CHANGED, retryable=True,
                               hint="Call again so the person can approve the notebook as it is now.")
    scope.enforce_on(ctx, scope.notebook_of(nb))
    return _metadata_only(svc.prove_start(name, nb["cells"], nb.get("lakehouse"), nb.get("compute"), nb.get("workspace")))


def cancel_proof(svc: Any, ctx: CallContext, id: str) -> dict[str, Any]:  # noqa: A002 - the proof id
    return svc.prove_cancel(id)


def verify_proof_table(svc: Any, ctx: CallContext, id: str, table: str, rules: dict[str, Any] | None = None,  # noqa: A002
                       offset: int = 0) -> dict[str, Any]:
    return svc.prove_verify(id, table, rules or None, offset, save=False)


def delete_proof_outputs(svc: Any, ctx: CallContext, id: str) -> dict[str, Any]:  # noqa: A002
    return svc.prove_delete_outputs(id)


def export_receipt(svc: Any, ctx: CallContext, id: str, format: str = "markdown", hide: list[str] | None = None) -> dict[str, Any]:  # noqa: A002
    from . import policy
    hidden = sorted(set(hide or []) | set(policy.receipt_redactions()))
    return svc.receipt_export(id, format, hidden) | {"hidden": hidden}
