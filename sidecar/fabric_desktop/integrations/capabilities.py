"""The core capabilities: one registry entry per thing an external tool can ask the engine for.

Each entry is metadata plus a handler from services.py. Add a capability here (or from an
extension) and it appears in MCP, the local API and the Integrations page; nothing else changes.
Only capabilities the engine can actually perform are registered. Execution (PR-AI-007) and
writes add their entries in the pull requests that build them.

``resource`` also serves a capability as an MCP resource at that fabric-desktop:// URI (paging and
options go in the query: ``fabric-desktop://notebooks?offset=100``). Resources carry metadata only.
"""

from __future__ import annotations

from typing import Any

from .. import __version__
from . import scope, services
from .core import CallContext
from .registry import (PAGING, PERMISSION_TEXT, PERMISSIONS, Capability, Registry, paged_output, schema)

STR = {"type": "string"}
ID = {"type": "string", "minLength": 1, "maxLength": 100, "pattern": r"^[A-Za-z0-9\-]+$"}
NAME = {"type": "string", "minLength": 1, "maxLength": 200}
FABRIC_LIMIT = ("Reads Fabric with your own account, so you see exactly what you can see in Fabric.",)

WORKSPACE = schema({"id": STR, "name": STR, "type": STR, "capacity_id": {"type": ["string", "null"]}}, extra=True)
ARTIFACT = schema({"id": STR, "name": STR, "type": STR, "description": STR}, extra=True)
NOTEBOOK_ROW = schema({"name": STR, "modified": {"type": "number"}}, extra=True)
CHECK = schema({"ok": {"type": "boolean"}, "status": {"enum": ["ok", "blocked", "unknown"]}, "reasons": {"type": "array"},
                "unknown": {"type": "array"}}, extra=True)


def register_core(reg: Registry) -> None:
    def list_capabilities(svc: Any, ctx: CallContext, include_unavailable: bool = True) -> dict[str, Any]:
        return {"version": __version__, "permissions": [{"id": p, "description": PERMISSION_TEXT[p]} for p in PERMISSIONS],
                "capabilities": reg.describe(ctx, include_unavailable)}

    caps = [
        Capability(
            "fabric_desktop_get_status", "Get status",
            "KratosForge's version, whether someone is signed in to Fabric and as whom, and how this "
            "client is connected. Call this first.",
            schema(), schema({"product": STR, "version": STR, "signed_in": {"type": "boolean"},
                              "user": {"type": ["string", "null"]}, "tenant": {"type": ["string", "null"]}},
                             ["product", "version", "signed_in"], extra=True),
            "READ_METADATA", services.get_status, workspaces=scope.NONE, timeout=15,
            resource="fabric-desktop://status"),
        Capability(
            "fabric_desktop_list_capabilities", "List capabilities",
            "Everything this KratosForge can do for an external tool, with input and output schemas, "
            "the permission each needs, whether it needs the person's approval, and whether it is available to "
            "you now and if not why.",
            schema({"include_unavailable": {"type": "boolean", "default": True,
                                            "description": "Also list what you can't use now, with the reason."}}),
            schema({"version": STR, "permissions": {"type": "array"}, "capabilities": {"type": "array"}},
                   ["capabilities"], extra=True),
            "READ_METADATA", list_capabilities, workspaces=scope.NONE, timeout=15, needs_service=False, public=True,
            resource="fabric-desktop://capabilities"),
        Capability(
            "fabric_desktop_list_projects", "List projects",
            "The work kept on this PC: the notebooks folder, and the metadata (medallion) project if one is open.",
            schema(), schema({"projects": {"type": "array", "items": schema({"id": STR, "kind": STR, "name": STR}, extra=True)}},
                             ["projects"], extra=True),
            "READ_METADATA", services.list_projects, workspaces=scope.FILTERED, timeout=30,
            resource="fabric-desktop://projects"),
        Capability(
            "fabric_desktop_get_project", "Get project",
            "One project's structure: the notebooks in the notebooks folder, or the metadata project's layers, "
            "environments, entities (tables with their source, keys, columns and checks) and run order.",
            schema({"id": {"enum": ["notebooks", "metadata"], "description": "From fabric_desktop_list_projects."}}, ["id"]),
            schema({"id": STR, "kind": STR}, ["id", "kind"], extra=True),
            "READ_METADATA", services.get_project, workspaces=scope.project, timeout=30,
            resource="fabric-desktop://projects/{id}"),
        Capability(
            "fabric_desktop_validate_project", "Validate project",
            "The metadata project's own validation: the same problems the Data mesh page lists (missing sources, "
            "key problems, cycles, broken links).",
            schema(), schema({"ok": {"type": "boolean"}, "errors": {"type": "array", "items": STR}}, ["ok", "errors"], extra=True),
            "READ_METADATA", services.validate_project, workspaces=scope.project, timeout=60,
            resource="fabric-desktop://projects/metadata/validation"),
        Capability(
            "fabric_desktop_list_workspaces", "List workspaces",
            "The Microsoft Fabric workspaces your account can open.",
            schema(dict(PAGING)), paged_output(WORKSPACE),
            "READ_METADATA", services.list_workspaces, workspaces=scope.FILTERED, locations=("fabric",), limitations=FABRIC_LIMIT, timeout=60,
            resource="fabric-desktop://workspaces"),
        Capability(
            "fabric_desktop_list_artifacts", "List items in a workspace",
            "The items (lakehouses, notebooks, pipelines, semantic models, reports, ...) in one Fabric workspace, "
            "optionally of one type.",
            schema({"workspace_id": ID, "type": {"type": "string", "maxLength": 60, "default": "",
                                                 "description": "A Fabric item type such as Lakehouse or Notebook; empty for all."},
                    **PAGING}, ["workspace_id"]),
            paged_output(ARTIFACT),
            "READ_METADATA", services.list_artifacts, workspaces=scope.ARGUMENT, locations=("fabric",), limitations=FABRIC_LIMIT, timeout=60,
            resource="fabric-desktop://workspaces/{workspace_id}/items"),
        Capability(
            "fabric_desktop_get_artifact_metadata", "Get item metadata",
            "One Fabric item's name, type and description; for a lakehouse also whether it uses schemas and its SQL "
            "endpoint. No data.",
            schema({"workspace_id": ID, "item_id": ID}, ["workspace_id", "item_id"]),
            schema({"id": STR, "name": STR, "type": STR, "workspace_id": STR}, ["id", "type"], extra=True),
            "READ_METADATA", services.get_artifact_metadata, workspaces=scope.ARGUMENT, locations=("fabric",), limitations=FABRIC_LIMIT, timeout=60,
            resource="fabric-desktop://workspaces/{workspace_id}/items/{item_id}"),
        Capability(
            "fabric_desktop_list_tables", "List lakehouse tables",
            "The Delta tables in a lakehouse, by schema, read from OneLake.",
            schema({"workspace_id": ID, "lakehouse_id": ID}, ["workspace_id", "lakehouse_id"]),
            schema({"tables": {"type": "array", "items": schema({"schema": STR, "name": STR, "full_name": STR}, extra=True)}},
                   ["tables"], extra=True),
            "READ_METADATA", services.list_tables, workspaces=scope.ARGUMENT, locations=("fabric",), artifact_types=("Lakehouse",),
            limitations=FABRIC_LIMIT, timeout=120,
            resource="fabric-desktop://workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables"),
        Capability(
            "fabric_desktop_get_table_schema", "Get table schema",
            "A lakehouse table's columns and their Delta types, read from the table's Delta log. No rows.",
            schema({"workspace_id": ID, "lakehouse_id": ID,
                    "table": {"type": "string", "minLength": 1, "maxLength": 300, "description": "schema.table, or table for dbo"}},
                   ["workspace_id", "lakehouse_id", "table"]),
            schema({"table": STR, "columns": {"type": "array", "items": schema({"name": STR, "type": STR}, extra=True)}},
                   ["table", "columns"], extra=True),
            "READ_METADATA", services.get_table_schema, workspaces=scope.ARGUMENT, locations=("fabric",), artifact_types=("Lakehouse",),
            limitations=FABRIC_LIMIT, timeout=120,
            resource="fabric-desktop://workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables/{table}/schema"),
        Capability(
            "fabric_desktop_list_notebooks", "List notebooks",
            "The notebooks saved in KratosForge on this PC, newest first.",
            schema(dict(PAGING)), paged_output(NOTEBOOK_ROW),
            "READ_METADATA", services.list_notebooks, workspaces=scope.FILTERED, artifact_types=("Notebook",), timeout=30,
            resource="fabric-desktop://notebooks"),
        Capability(
            "fabric_desktop_get_notebook", "Get notebook",
            "A notebook's cells (code and markdown, with anything that looks like a secret removed; never outputs), "
            "its attached lakehouse and where it runs.",
            schema({"name": NAME, "include_source": {"type": "boolean", "default": True}}, ["name"]),
            schema({"name": STR, "cells": {"type": "array"}, "lakehouse": {"type": ["object", "null"]}}, ["name", "cells"], extra=True),
            "READ_METADATA", services.get_notebook, workspaces=scope.notebook, artifact_types=("Notebook",),
            limitations=("Secret removal recognises common token and password shapes, not every secret.",), timeout=30,
            resource="fabric-desktop://notebooks/{name}"),
        Capability(
            "fabric_desktop_validate_notebook", "Validate notebook",
            "Whether a notebook can run on this PC and in Fabric and which cells stop it, what a proof would refuse, "
            "how its cells depend on each other, and Validate's findings (errors, warnings and info, each with its cell "
            "and a fix): the checks the notebook screen runs. Runs no code. ok is false when nothing stops it but some "
            "cells can't be read (status unknown).",
            schema({"name": NAME}, ["name"]),
            schema({"ok": {"type": "boolean"}, "this_pc": CHECK, "fabric": CHECK, "proof": {"type": "object"},
                    "dependencies": {"type": "object"}}, ["ok", "this_pc", "fabric"], extra=True),
            "READ_METADATA", services.validate_notebook, workspaces=scope.notebook, artifact_types=("Notebook",), timeout=60,
            resource="fabric-desktop://notebooks/{name}/validation"),
        Capability(
            "fabric_desktop_check_run_risk", "Check a run's risk",
            "Before a workload runs: whether anything would stop it (policy, sign-in, a paused capacity, definition "
            "problems, the memory guard) or should be looked at first (recent failures, a run already going, a "
            "schedule due soon, a CU threshold), as pass, warning, blocked or unknown, each with evidence. "
            "Starts nothing; unknown CU stays unknown. Reports only: it doesn't block runs, so a blocked result "
            "doesn't stop a run started another way.",
            schema({"workload_id": {"type": "string", "minLength": 1, "maxLength": 400},
                    "action": {"enum": ["run", "run_in_fabric", "run_item", "run_schedule"]},
                    "env": {"type": "string", "maxLength": 10, "default": ""}}, ["workload_id", "action"]),
            schema({"result": STR, "summary": STR, "findings": {"type": "array"}, "can_proceed": {"type": "boolean"}},
                   ["result", "findings"], extra=True),
            "READ_METADATA", services.check_run_risk, workspaces=scope.workload, status="preview", limitations=FABRIC_LIMIT + (
                "Reads the capacity's state; how busy a capacity is isn't available to developers.",), timeout=60),
        Capability(
            "fabric_desktop_placement_advice", "Advise where to run a notebook",
            "Whether a notebook is better run on this PC or in Fabric (pc, fabric, either or unknown), with the "
            "evidence: which cells stop it in either place, the newest proof of its current code (verdict and "
            "verification level as recorded), the proof's measured time and the Fabric job CU of its Fabric run with "
            "that figure's classification. A proof of older code doesn't count. Advice only: it changes nothing "
            "and starts nothing; CU is capacity use, not money saved.",
            schema({"name": NAME}, ["name"]),
            schema({"place": {"enum": ["pc", "fabric", "either", "unknown"]}, "headline": STR,
                    "reasons": {"type": "array"}}, ["place", "reasons"], extra=True),
            "READ_METADATA", services.get_placement_advice, workspaces=scope.notebook, status="preview",
            artifact_types=("Notebook",), timeout=60),
    ]
    caps += _prove() + _pipelines() + _pipeline_runs() + _executions() + _prove_actions() + _semantic_models()
    for cap in caps:
        reg.register(cap)


PIPELINE_LIMIT = ("Reads only the definition you pass; it doesn't fetch pipelines from Fabric.",
                  "Expressions are read, never evaluated; nothing in the pipeline runs.",
                  "Definitions up to 1 MB (the local API's request limit); the desktop app reads larger ones.")
COMPAT_LIMIT = ("No activity runs on this PC yet: local execution arrives with ORCH-003. 'planned' is a proposal, "
                "not support.",
                "No entry has been compared against Fabric yet; Fabric-only type names are marked unverified.")
DEFINITION = {"type": "string", "minLength": 2, "maxLength": 1_000_000,
              "description": "pipeline-content.json, or a getDefinition result with base64 parts, as text."}
DIAGNOSTICS = {"ok": {"type": "boolean"}, "diagnostics": {"type": "array"}, "counts": {"type": "object"},
               "schema": {"type": ["object", "null"]}}


def _pipelines() -> list[Capability]:
    """Fabric pipeline definitions (ORCH-001): read and check a definition the caller passes in."""
    return [
        Capability(
            "fabric_desktop_validate_pipeline", "Validate a pipeline definition",
            "Checks a Fabric Data Factory pipeline definition: required fields, duplicate activity names, "
            "dependencies on missing activities, dependency cycles, undeclared parameters and variables, and "
            "expressions that read activities not upstream. Each problem has the activity and JSON path.",
            schema({"definition": DEFINITION}, ["definition"]), schema(DIAGNOSTICS, ["ok", "diagnostics"], extra=True),
            "READ_METADATA", services.validate_pipeline, workspaces=scope.NONE, artifact_types=("DataPipeline",), status="preview",
            limitations=PIPELINE_LIMIT, timeout=60),
        Capability(
            "fabric_desktop_inspect_pipeline", "Inspect a pipeline definition",
            "A Fabric pipeline definition as a tree: parameters, variables, activities (nested ones included) "
            "with their dependencies, each expression and what it refers to, plus the diagnostics and a record of "
            "the format and activity types found. Secure parameter defaults are hidden.",
            schema({"definition": DEFINITION}, ["definition"]),
            schema(DIAGNOSTICS | {"name": STR, "activities": {"type": "array"}, "parameters": {"type": "array"},
                                  "variables": {"type": "array"}}, ["ok", "diagnostics"], extra=True),
            "READ_METADATA", services.inspect_pipeline, workspaces=scope.NONE, artifact_types=("DataPipeline",), status="preview",
            limitations=PIPELINE_LIMIT, timeout=60),
        Capability(
            "fabric_desktop_export_pipeline", "Convert a pipeline definition",
            "Returns a Fabric pipeline definition as pipeline-content.json, an item definition with base64 parts, "
            "or Fabric Git folder files. Every property is kept; an unchanged definition comes back exactly as given. "
            "Writes no file.",
            schema({"definition": DEFINITION,
                    "format": {"enum": ["", "pipeline_content", "item_definition", "git_folder"], "default": ""}},
                   ["definition"]),
            schema({"ok": {"type": "boolean"}, "format": STR, "text": STR, "files": {"type": "object"},
                    "diagnostics": {"type": "array"}}, ["ok"], extra=True),
            "READ_METADATA", services.export_pipeline, workspaces=scope.NONE, artifact_types=("DataPipeline",), status="preview",
            limitations=PIPELINE_LIMIT, timeout=60),
        Capability(
            "fabric_desktop_check_pipeline_compatibility", "Check what can run on this PC",
            "For every activity in a Fabric pipeline definition (nested ones included): whether KratosForge "
            "can run it here, simulate it, only check it, or must leave it to Fabric; what is planned and in "
            "which stage; the properties it can't use; the sign-in it needs; its side effects; and whether it can "
            "only ever run in Fabric. Fabric meters every activity run, so each activity that can't run here counts "
            "as needing Fabric capacity. Unknown activity types are kept and reported as unsupported.",
            schema({"definition": DEFINITION}, ["definition"]),
            schema(DIAGNOSTICS | {"registry_version": STR, "summary": {"type": "object"},
                                  "activities": {"type": "array"}}, ["ok", "diagnostics"], extra=True),
            "READ_METADATA", services.check_pipeline_compatibility, workspaces=scope.NONE, artifact_types=("DataPipeline",),
            status="preview", limitations=PIPELINE_LIMIT + COMPAT_LIMIT, timeout=60),
        Capability(
            "fabric_desktop_get_activity_registry", "List pipeline activity support",
            "The versioned registry of Fabric pipeline activity types: each type's classification, evidence, "
            "planned support, property-level support, sign-in, side effects, external dependencies and Fabric "
            "capacity need. Pass an activity type for one entry.",
            schema({"activity_type": {"type": "string", "maxLength": 200, "default": "",
                                      "description": "One activity type, such as Copy; empty for all."}}, []),
            schema({"registry_version": STR, "activities": {"type": "array"}, "activity": {"type": "object"}},
                   ["registry_version"], extra=True),
            "READ_METADATA", services.get_activity_registry, workspaces=scope.NONE, artifact_types=("DataPipeline",), status="preview",
            limitations=COMPAT_LIMIT, timeout=30),
    ]


OSSIE_LIMIT = FABRIC_LIMIT + (
    ("Reads the semantic models of an environment of the open metadata project (test or prod): Fabric's "
     "getDefinition, read only. Apache Ossie's spec (0.2.0.dev0) is a draft and may change."),
    ("Importing writes the model, so it is left to KratosForge's Semantic model tab, where the person sees the "
     "preview and confirms."))
ENV = {"enum": ["test", "prod", "dev"], "default": "test",
       "description": "The metadata project's environment whose workspace holds the model."}


def _semantic_models() -> list[Capability]:
    """Semantic models and Apache Ossie (docs/semantic-model-ossie.md), read-only: list, export, and
    preview an import. The import itself stays in the app, behind its confirm."""
    from ..model_ossie import MAX_CHARS
    return [
        Capability(
            "fabric_desktop_list_semantic_models", "List semantic models",
            "The semantic models in the workspace of one environment of the open metadata project.",
            schema({"env": ENV}), schema({"env": STR, "workspace_name": STR, "models": {"type": "array"}}, ["models"],
                                         extra=True),
            "READ_METADATA", services.list_semantic_models, workspaces=scope.environment, locations=("fabric",),
            artifact_types=("SemanticModel",), status="preview", limitations=OSSIE_LIMIT, timeout=60),
        Capability(
            "fabric_desktop_export_semantic_model_ossie", "Export a semantic model to Apache Ossie",
            "A semantic model as Apache Ossie YAML, the open semantic model format, converted on this PC. Simple "
            "measures (one aggregate over one column) also come as SQL; everything only Power BI understands "
            "(other DAX, roles, hierarchies) is kept in the file's Power BI section. The report lists what is kept "
            "and anything not converted. Writes no file.",
            schema({"env": ENV, "name": NAME,
                    "translate_dax": {"type": "boolean", "default": True,
                                      "description": "Also write simple measures as portable SQL."}}, ["env", "name"]),
            schema({"yaml": STR, "name": STR, "version": STR, "counts": {"type": "object"}, "report": {"type": "array"}},
                   ["yaml", "counts", "report"], extra=True),
            "READ_METADATA", services.export_semantic_model_ossie, workspaces=scope.environment, locations=("fabric",),
            artifact_types=("SemanticModel",), status="preview", limitations=OSSIE_LIMIT, timeout=120),
        Capability(
            "fabric_desktop_preview_ossie_import", "Preview an Apache Ossie import",
            "What importing an Apache Ossie document into a semantic model would change: every table, column, "
            "measure and relationship added, changed or removed, measures that would need DAX, and what can't be "
            "carried over. update keeps what the file doesn't mention unless named in remove (from removable); "
            "replace makes the model exactly the file; new creates it. Writes nothing.",
            schema({"env": ENV, "text": {"type": "string", "minLength": 1, "maxLength": MAX_CHARS,
                                         "description": "The Ossie document, YAML or JSON."},
                    "name": {"type": "string", "maxLength": 200, "default": "",
                             "description": "The model; empty for the name in the file."},
                    "mode": {"enum": ["update", "replace", "new"], "default": "update"},
                    "remove": {"type": "array", "items": {"type": "string", "maxLength": 400}, "maxItems": 10_000,
                               "default": []}}, ["env", "text"]),
            schema({"name": STR, "mode": STR, "exists": {"type": "boolean"}, "changes": {"type": "array"},
                    "removable": {"type": "array"}, "report": {"type": "array"}}, ["changes", "report"], extra=True),
            "READ_METADATA", services.preview_ossie_import, workspaces=scope.environment, locations=("fabric",),
            artifact_types=("SemanticModel",), status="preview", limitations=OSSIE_LIMIT, timeout=120),
    ]


RUN_INPUT = {
    "definition": DEFINITION,
    "parameters": {"type": "object", "default": {}, "description": "Pipeline parameter values by name."},
    "mocks": {"type": "object", "default": {}, "description": "Activity name to {status: Succeeded|Failed, output, "
              "error, seconds}: stands in for activities that can't run on this PC."},
    "children": {"type": "object", "default": {}, "description": "Child pipeline definitions as text, by "
                 "referenceName or pipelineId."},
}
RUN_LIMIT = ("Runs control flow (variables, conditions, loops, waits, Fail, Filter) and child pipelines on this PC. "
             "Copy, Notebook, Lookup, Get Metadata, Script and other data or compute activities need a mock; "
             "cloud-only and unknown activities refuse the run.",
             "Nothing is sent to Fabric and no capacity is used. Behaviour follows Data Factory's documentation and "
             "hasn't been compared with Fabric.",
             "Activity outputs and variable values are left out; open the run in KratosForge to see them.")


def _pipeline_runs() -> list[Capability]:
    """Local pipeline runs (ORCH-003)."""
    run_id = schema({"id": ID}, ["id"])
    return [
        Capability(
            "fabric_desktop_check_pipeline_run", "Check a pipeline can run on this PC",
            "Says whether a Fabric pipeline definition can run on this PC with the given parameters, mocks and "
            "child pipelines, and if not, why, activity by activity. Runs nothing.",
            schema(RUN_INPUT, ["definition"]), schema({"ok": {"type": "boolean"}, "problems": {"type": "array"}},
                                                      ["ok", "problems"], extra=True),
            "READ_METADATA", services.check_pipeline_run, workspaces=scope.NONE, artifact_types=("DataPipeline",), status="preview",
            limitations=PIPELINE_LIMIT + RUN_LIMIT[:2], timeout=60),
        Capability(
            "fabric_desktop_start_pipeline_run", "Run a pipeline on this PC",
            "Starts a local run of a Fabric pipeline definition and returns its id. Activities that can't run here "
            "must be mocked, or the run is refused before anything starts.",
            schema(RUN_INPUT | {"max_parallel": {"type": "integer", "minimum": 1, "maximum": 32, "default": 4},
                                "wait_scale": {"type": "number", "minimum": 0, "maximum": 1, "default": 1},
                                "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 86400, "default": 0}},
                   ["definition"]),
            schema({"ok": {"type": "boolean"}, "run_id": STR, "state": STR, "problems": {"type": "array"}}, ["ok"],
                   extra=True),
            "EXECUTE_LOCAL_CODE", services.start_pipeline_run, workspaces=scope.NONE, read_only=False, approval="always",
            locations=("this_pc",), artifact_types=("DataPipeline",), status="preview", limitations=RUN_LIMIT,
            timeout=60),
        Capability(
            "fabric_desktop_get_pipeline_run", "Get a local pipeline run",
            "A local pipeline run's state and every activity run: state, Fabric-style status, whether it ran, was "
            "mocked or was skipped, timings, attempts and error codes.",
            run_id, schema({"run_id": STR, "state": STR, "activities": {"type": "array"}}, ["run_id", "state"],
                           extra=True),
            "READ_METADATA", services.get_pipeline_run, workspaces=scope.NONE, artifact_types=("DataPipeline",), status="preview",
            limitations=RUN_LIMIT[2:], timeout=30),
        Capability(
            "fabric_desktop_list_pipeline_runs", "List local pipeline runs",
            "Recent local pipeline runs on this PC, newest first, with their state and activity counts.",
            schema({"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}}),
            schema({"runs": {"type": "array"}}, ["runs"], extra=True),
            "READ_METADATA", services.list_pipeline_runs, workspaces=scope.NONE, artifact_types=("DataPipeline",), status="preview",
            timeout=30),
        Capability(
            "fabric_desktop_cancel_pipeline_run", "Cancel a local pipeline run",
            "Stops a local pipeline run: nothing new starts and waits end at once.",
            run_id, schema({"cancelled": {"type": "boolean"}}, ["cancelled"], extra=True),
            "EXECUTE_LOCAL_CODE", services.cancel_pipeline_run, workspaces=scope.NONE, read_only=False, approval="always",
            locations=("this_pc",), status="preview", timeout=30),
    ]


PROVE_LIMIT = ("Cell outputs and example rows are left out: they can contain data. Open the proof in "
               "KratosForge to see them.",)
NAME_FILTER = {"name": {"type": "string", "maxLength": 200, "default": "", "description": "Only this notebook's; empty for all."}}


def _prove() -> list[Capability]:
    """Prove this notebook, read-only: what the notebook screen shows about plans, proofs, receipts
    and side-by-side runs. Starting, cancelling or exporting asks for approval (PR-AI-007)."""
    notebook = schema({"name": NAME}, ["name"])
    record = schema({"id": STR, "name": STR, "state": STR}, extra=True)
    return [
        Capability(
            "fabric_desktop_get_notebook_dependencies", "Get notebook cell dependencies",
            "Which cells of a notebook depend on which, read from their code (variables, tables and files each "
            "cell defines and uses). Runs no code.",
            notebook, schema({"name": STR, "dependencies": {"type": "object"}}, ["name", "dependencies"], extra=True),
            "READ_METADATA", services.get_notebook_dependencies, workspaces=scope.notebook, artifact_types=("Notebook",), timeout=30,
            resource="fabric-desktop://notebooks/{name}/dependencies"),
        Capability(
            "fabric_desktop_get_notebook_environment", "Get notebook environment",
            "The Python modules each cell imports and whether each is available on this PC and in Fabric.",
            notebook, schema({"name": STR, "environment": {"type": "object"}}, ["name", "environment"], extra=True),
            "READ_METADATA", services.get_notebook_environment, workspaces=scope.notebook, artifact_types=("Notebook",), timeout=60,
            resource="fabric-desktop://notebooks/{name}/environment"),
        Capability(
            "fabric_desktop_check_proof", "Check a proof plan",
            "What proving a notebook would do (run it on this PC and in Fabric and compare the outputs), what "
            "blocks it, and its environment. Starts nothing.",
            notebook, schema({"name": STR, "check": {"type": "object"}, "environment": {"type": "object"},
                              "plan": {"type": "object"}}, ["name", "check"], extra=True),
            "READ_METADATA", services.check_proof, workspaces=scope.notebook, artifact_types=("Notebook",), timeout=60,
            resource="fabric-desktop://notebooks/{name}/proof-plan"),
        Capability(
            "fabric_desktop_list_proofs", "List proofs",
            "Proofs of notebooks, newest first, running ones included: state, verdict and tables compared.",
            schema({**NAME_FILTER, **PAGING}), paged_output(record),
            "READ_METADATA", services.list_proofs, workspaces=scope.FILTERED, limitations=PROVE_LIMIT, timeout=30,
            resource="fabric-desktop://proofs"),
        Capability(
            "fabric_desktop_get_proof", "Get proof",
            "One proof: each side's progress, the tables compared and how, the verdict and its receipt id.",
            schema({"id": ID}, ["id"]), record,
            "READ_METADATA", services.get_proof, workspaces=scope.proof, limitations=PROVE_LIMIT, timeout=30,
            resource="fabric-desktop://proofs/{id}"),
        Capability(
            "fabric_desktop_list_receipts", "List receipts",
            "Receipts of finished proofs, newest first, with whether each still matches its id.",
            schema({**NAME_FILTER, **PAGING}), paged_output(schema({"id": STR}, extra=True)),
            "READ_METADATA", services.list_receipts, workspaces=scope.FILTERED, timeout=30,
            resource="fabric-desktop://receipts"),
        Capability(
            "fabric_desktop_get_receipt", "Get receipt",
            "One receipt: the code, inputs, environments, outputs and verification of a proof, fixed when it "
            "finished. Its id is the SHA-256 of its content, so a changed receipt is refused.",
            schema({"id": ID}, ["id"]), schema({"id": STR, "receipt": {"type": "object"}}, ["id", "receipt"], extra=True),
            "READ_METADATA", services.get_receipt, workspaces=scope.receipt, limitations=PROVE_LIMIT, timeout=30,
            resource="fabric-desktop://receipts/{id}"),
        Capability(
            "fabric_desktop_list_side_by_side", "List side-by-side runs",
            "Side-by-side runs (the same notebook on this PC and in Fabric, timed and compared), newest first.",
            schema({**NAME_FILTER, **PAGING}), paged_output(record),
            "READ_METADATA", services.list_side_by_side, workspaces=scope.FILTERED, limitations=PROVE_LIMIT, timeout=30,
            resource="fabric-desktop://side-by-side"),
        Capability(
            "fabric_desktop_get_side_by_side", "Get side-by-side run",
            "One side-by-side run: each side's progress and time, and how the outputs compared.",
            schema({"id": ID}, ["id"]), record,
            "READ_METADATA", services.get_side_by_side, workspaces=scope.side_by_side, limitations=PROVE_LIMIT, timeout=30,
            resource="fabric-desktop://side-by-side/{id}"),
    ]


WAIT = {"wait_seconds": {"type": "number", "minimum": 0, "maximum": 120, "default": 30,
                         "description": "How long to wait for the result before returning the execution id."}}
EXECUTION = schema({"id": STR, "kind": STR, "state": {"enum": ["queued", "running", "succeeded", "failed", "cancelled", "timed_out"]},
                    "seconds": {"type": "number"}, "result": {}, "error": {"type": "object"}}, ["id", "state"], extra=True)
OPT_ID = {"type": "string", "maxLength": 100, "pattern": r"^[A-Za-z0-9\-]*$", "default": ""}
EXECUTION_ID = {"type": "string", "pattern": r"^ex_[0-9a-f]{20}$"}
CODE_LIMITS = ("Runs in a Python process of its own on this PC, ended afterwards; nothing is kept between runs.",
               "Can read the attached lakehouse; writing lakehouse tables and getting other tokens are refused.",
               "Output is capped at 100,000 characters; images and HTML are left out.",
               "Running other notebooks (%run, notebookutils.notebook.run) is refused.",
               "Code over 20,000 characters (for a notebook, all its code cells) is refused: the approval shows it in full.")


def _executions() -> list[Capability]:
    """Running things for an external tool (executions.py). SQL only reads; code always asks."""
    return [
        Capability(
            "fabric_desktop_execute_sql", "Run a SQL query",
            "Runs one read-only Spark SQL (or DuckDB SQL) query over a lakehouse's tables on this PC and returns "
            "up to max_rows rows. SELECT only; local files, extensions and the network can't be reached from it.",
            schema({"workspace_id": ID, "lakehouse_id": ID,
                    "query": {"type": "string", "minLength": 1, "maxLength": 100_000},
                    "max_rows": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                    **WAIT, "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 600, "default": 120}},
                   ["workspace_id", "lakehouse_id", "query"]),
            EXECUTION, "READ_DATA", services.execute_sql, workspaces=scope.ARGUMENT, approval="ask", artifact_types=("Lakehouse",),
            limitations=("One statement per call. Reads tables at their current version.",), timeout=660),
        Capability(
            "fabric_desktop_execute_python", "Run Python",
            "Runs Python on this PC in a fresh process, optionally with a lakehouse attached (as a notebook cell "
            "would run). The person approves every run in KratosForge, seeing the code.",
            schema({"code": {"type": "string", "minLength": 1, "maxLength": 100_000},
                    "workspace_id": OPT_ID, "lakehouse_id": OPT_ID,
                    **WAIT, "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 3600, "default": 300}},
                   ["code"]),
            EXECUTION, "EXECUTE_LOCAL_CODE", services.execute_python, workspaces=scope.ARGUMENT, read_only=False, approval="always",
            limitations=CODE_LIMITS, timeout=3600),
        Capability(
            "fabric_desktop_run_notebook", "Run a notebook on this PC",
            "Runs a saved notebook's code cells in order on this PC in a fresh process, stopping at the first "
            "error, and returns each cell's output. The person approves every run, seeing every code cell; if the "
            "notebook changes after that, the run is refused. Running in Fabric isn't offered.",
            schema({"name": NAME, "workspace_id": OPT_ID | {"description": "The workspace of the notebook's lakehouse."},
                    **WAIT, "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 3600, "default": 900}},
                   ["name"]),
            EXECUTION, "EXECUTE_LOCAL_CODE", services.run_notebook, workspaces=scope.notebook, read_only=False, approval="always",
            artifact_types=("Notebook",), limitations=CODE_LIMITS + ("A notebook that writes tables stops at that cell.",),
            timeout=3600, bind=services.bind_notebook),
        Capability(
            "fabric_desktop_get_execution_status", "Get execution status",
            "The state of an execution you started, and its result once finished. wait_seconds waits for it to finish.",
            schema({"execution_id": EXECUTION_ID, "wait_seconds": {"type": "number", "minimum": 0, "maximum": 60, "default": 0}},
                   ["execution_id"]),
            EXECUTION, "READ_METADATA", services.get_execution_status, workspaces=scope.execution, timeout=70),
        Capability(
            "fabric_desktop_get_execution_logs", "Get execution logs",
            "The log lines of an execution you started.",
            schema({"execution_id": EXECUTION_ID, **PAGING}, ["execution_id"]), paged_output(STR),
            "READ_METADATA", services.get_execution_logs, workspaces=scope.execution, timeout=15),
        Capability(
            "fabric_desktop_list_executions", "List executions",
            "Executions you started, newest first, with their states (no results).",
            schema(dict(PAGING)), paged_output(EXECUTION),
            "READ_METADATA", services.list_executions, workspaces=scope.FILTERED, timeout=15),
        Capability(
            "fabric_desktop_cancel_execution", "Cancel an execution",
            "Stops an execution you started: interrupts it, then ends its process.",
            schema({"execution_id": EXECUTION_ID}, ["execution_id"]), EXECUTION,
            "EXECUTE_LOCAL_CODE", services.cancel_execution, workspaces=scope.execution, read_only=False, timeout=30),
    ]


def _prove_actions() -> list[Capability]:
    """Prove this notebook's actions. Starting a proof publishes a copy of the notebook to Fabric and
    deleting outputs deletes files in OneLake; neither permission is offered to external tools in
    this release, so both are listed as unavailable with the reason."""
    proof_id = schema({"id": ID}, ["id"])
    return [
        Capability(
            "fabric_desktop_start_proof", "Start a proof",
            "Proves a notebook: runs it on this PC and as a copy in Fabric, then compares every table it writes.",
            schema({"name": NAME}, ["name"]), schema({"id": STR, "state": STR}, ["id"], extra=True),
            "MODIFY_FABRIC_ARTIFACTS", services.start_proof, workspaces=scope.notebook, read_only=False, approval="always",
            bind=services.bind_proof,
            locations=("this_pc", "fabric"), artifact_types=("Notebook",), timeout=120,
            limitations=("Publishes a copy of the notebook to Fabric and runs it there.",)),
        Capability(
            "fabric_desktop_cancel_proof", "Cancel a proof",
            "Stops a running proof.",
            proof_id, schema({"cancelled": {"type": "boolean"}}, ["cancelled"], extra=True),
            "EXECUTE_LOCAL_CODE", services.cancel_proof, workspaces=scope.proof, read_only=False, approval="always", timeout=60),
        Capability(
            "fabric_desktop_verify_proof_table", "Verify a proof table again",
            "Compares one table of a finished proof again under other rules (tolerance, key columns, ignored "
            "columns), without running anything. Returns example rows that differ, so it needs READ_DATA.",
            schema({"id": ID, "table": {"type": "string", "minLength": 1, "maxLength": 300},
                    "rules": {"type": "object", "default": {}}, "offset": {"type": "integer", "minimum": 0, "default": 0}},
                   ["id", "table"]),
            schema({"table": STR, "result": STR, "level": STR}, ["table"], extra=True),
            "READ_DATA", services.verify_proof_table, workspaces=scope.proof, approval="ask", locations=("fabric",), timeout=600),
        Capability(
            "fabric_desktop_delete_proof_outputs", "Delete a proof's outputs",
            "Deletes the files a proof wrote under Files/ in the lakehouse. The record and receipt stay.",
            proof_id, schema({"deleted": STR}, ["deleted"], extra=True),
            "WRITE_ONELAKE", services.delete_proof_outputs, workspaces=scope.proof, read_only=False, destructive=True, approval="always",
            locations=("fabric",), timeout=300),
        Capability(
            "fabric_desktop_export_receipt", "Export a receipt",
            "A readable report of a receipt (markdown, HTML or JSON). Parts in hide, and any your organization "
            "requires, are left out. Without hiding values it includes example rows, so it needs READ_DATA.",
            schema({"id": ID, "format": {"enum": ["markdown", "html", "json"], "default": "markdown"},
                    "hide": {"type": "array", "items": {"enum": ["identity", "locations", "values", "machine"]}, "default": []}},
                   ["id"]),
            schema({"format": STR, "text": STR, "hidden": {"type": "array"}}, ["format", "text"], extra=True),
            "READ_DATA", services.export_receipt, workspaces=scope.receipt, approval="ask", timeout=60),
    ]
