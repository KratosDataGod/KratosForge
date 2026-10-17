"""The core capabilities: one registry entry per thing an external tool can ask the engine for.

Each entry is metadata plus a handler from services.py. Add a capability here (or from an
extension) and it appears in MCP, the local API and the Integrations page; nothing else changes.
Only capabilities the engine can actually perform are registered. Execution (PR-AI-007) and
writes add their entries in the pull requests that build them.
"""

from __future__ import annotations

from typing import Any

from .. import __version__
from . import services
from .core import CallContext
from .registry import (PAGING, PERMISSION_TEXT, PERMISSIONS, Capability, Registry, paged_output, schema)

STR = {"type": "string"}
ID = {"type": "string", "minLength": 1, "maxLength": 100, "pattern": r"^[A-Za-z0-9\-]+$"}
NAME = {"type": "string", "minLength": 1, "maxLength": 200}
FABRIC_LIMIT = ("Reads Fabric with your own account, so you see exactly what you can see in Fabric.",)

WORKSPACE = schema({"id": STR, "name": STR, "type": STR, "capacity_id": {"type": ["string", "null"]}}, extra=True)
ARTIFACT = schema({"id": STR, "name": STR, "type": STR, "description": STR}, extra=True)
NOTEBOOK_ROW = schema({"name": STR, "modified": {"type": "number"}}, extra=True)
CHECK = schema({"ok": {"type": "boolean"}, "reasons": {"type": "array"}}, extra=True)


def register_core(reg: Registry) -> None:
    def list_capabilities(svc: Any, ctx: CallContext, include_unavailable: bool = True) -> dict[str, Any]:
        return {"version": __version__, "permissions": [{"id": p, "description": PERMISSION_TEXT[p]} for p in PERMISSIONS],
                "capabilities": reg.describe(ctx, include_unavailable)}

    caps = [
        Capability(
            "fabric_desktop_get_status", "Get status",
            "Fabric Desktop by KratosBI's version, whether someone is signed in to Fabric and as whom, and how this "
            "client is connected. Call this first.",
            schema(), schema({"product": STR, "version": STR, "signed_in": {"type": "boolean"},
                              "user": {"type": ["string", "null"]}, "tenant": {"type": ["string", "null"]}},
                             ["product", "version", "signed_in"], extra=True),
            "READ_METADATA", services.get_status, timeout=15),
        Capability(
            "fabric_desktop_list_capabilities", "List capabilities",
            "Everything this Fabric Desktop by KratosBI can do for an external tool, with input and output schemas, "
            "the permission each needs, whether it needs the person's approval, and whether it is available to "
            "you now and if not why.",
            schema({"include_unavailable": {"type": "boolean", "default": True,
                                            "description": "Also list what you can't use now, with the reason."}}),
            schema({"version": STR, "permissions": {"type": "array"}, "capabilities": {"type": "array"}},
                   ["capabilities"], extra=True),
            "READ_METADATA", list_capabilities, timeout=15, needs_service=False),
        Capability(
            "fabric_desktop_list_projects", "List projects",
            "The work kept on this PC: the notebooks folder, and the metadata (medallion) project if one is open.",
            schema(), schema({"projects": {"type": "array", "items": schema({"id": STR, "kind": STR, "name": STR}, extra=True)}},
                             ["projects"], extra=True),
            "READ_METADATA", services.list_projects, timeout=30),
        Capability(
            "fabric_desktop_get_project", "Get project",
            "One project's structure: the notebooks in the notebooks folder, or the metadata project's layers, "
            "environments, entities (tables with their source, keys, columns and checks) and run order.",
            schema({"id": {"enum": ["notebooks", "metadata"], "description": "From fabric_desktop_list_projects."}}, ["id"]),
            schema({"id": STR, "kind": STR}, ["id", "kind"], extra=True),
            "READ_METADATA", services.get_project, timeout=30),
        Capability(
            "fabric_desktop_validate_project", "Validate project",
            "The metadata project's own validation: the same problems the Data mesh page lists (missing sources, "
            "key problems, cycles, broken links).",
            schema(), schema({"ok": {"type": "boolean"}, "errors": {"type": "array", "items": STR}}, ["ok", "errors"], extra=True),
            "READ_METADATA", services.validate_project, timeout=60),
        Capability(
            "fabric_desktop_list_workspaces", "List workspaces",
            "The Microsoft Fabric workspaces your account can open.",
            schema(dict(PAGING)), paged_output(WORKSPACE),
            "READ_METADATA", services.list_workspaces, locations=("fabric",), limitations=FABRIC_LIMIT, timeout=60),
        Capability(
            "fabric_desktop_list_artifacts", "List items in a workspace",
            "The items (lakehouses, notebooks, pipelines, semantic models, reports, ...) in one Fabric workspace, "
            "optionally of one type.",
            schema({"workspace_id": ID, "type": {"type": "string", "maxLength": 60, "default": "",
                                                 "description": "A Fabric item type such as Lakehouse or Notebook; empty for all."},
                    **PAGING}, ["workspace_id"]),
            paged_output(ARTIFACT),
            "READ_METADATA", services.list_artifacts, locations=("fabric",), limitations=FABRIC_LIMIT, timeout=60),
        Capability(
            "fabric_desktop_get_artifact_metadata", "Get item metadata",
            "One Fabric item's name, type and description; for a lakehouse also whether it uses schemas and its SQL "
            "endpoint. No data.",
            schema({"workspace_id": ID, "item_id": ID}, ["workspace_id", "item_id"]),
            schema({"id": STR, "name": STR, "type": STR, "workspace_id": STR}, ["id", "type"], extra=True),
            "READ_METADATA", services.get_artifact_metadata, locations=("fabric",), limitations=FABRIC_LIMIT, timeout=60),
        Capability(
            "fabric_desktop_list_tables", "List lakehouse tables",
            "The Delta tables in a lakehouse, by schema, read from OneLake.",
            schema({"workspace_id": ID, "lakehouse_id": ID}, ["workspace_id", "lakehouse_id"]),
            schema({"tables": {"type": "array", "items": schema({"schema": STR, "name": STR, "full_name": STR}, extra=True)}},
                   ["tables"], extra=True),
            "READ_METADATA", services.list_tables, locations=("fabric",), artifact_types=("Lakehouse",),
            limitations=FABRIC_LIMIT, timeout=120),
        Capability(
            "fabric_desktop_get_table_schema", "Get table schema",
            "A lakehouse table's columns and their Delta types, read from the table's Delta log. No rows.",
            schema({"workspace_id": ID, "lakehouse_id": ID,
                    "table": {"type": "string", "minLength": 1, "maxLength": 300, "description": "schema.table, or table for dbo"}},
                   ["workspace_id", "lakehouse_id", "table"]),
            schema({"table": STR, "columns": {"type": "array", "items": schema({"name": STR, "type": STR}, extra=True)}},
                   ["table", "columns"], extra=True),
            "READ_METADATA", services.get_table_schema, locations=("fabric",), artifact_types=("Lakehouse",),
            limitations=FABRIC_LIMIT, timeout=120),
        Capability(
            "fabric_desktop_list_notebooks", "List notebooks",
            "The notebooks saved in Fabric Desktop by KratosBI on this PC, newest first.",
            schema(dict(PAGING)), paged_output(NOTEBOOK_ROW),
            "READ_METADATA", services.list_notebooks, artifact_types=("Notebook",), timeout=30),
        Capability(
            "fabric_desktop_get_notebook", "Get notebook",
            "A notebook's cells (code and markdown, with anything that looks like a secret removed; never outputs), "
            "its attached lakehouse and where it runs.",
            schema({"name": NAME, "include_source": {"type": "boolean", "default": True}}, ["name"]),
            schema({"name": STR, "cells": {"type": "array"}, "lakehouse": {"type": ["object", "null"]}}, ["name", "cells"], extra=True),
            "READ_METADATA", services.get_notebook, artifact_types=("Notebook",),
            limitations=("Secret removal recognises common token and password shapes, not every secret.",), timeout=30),
        Capability(
            "fabric_desktop_validate_notebook", "Validate notebook",
            "Whether a notebook can run on this PC and in Fabric and which cells stop it, what a proof would refuse, "
            "and how its cells depend on each other: the checks the notebook screen runs. Runs no code.",
            schema({"name": NAME}, ["name"]),
            schema({"ok": {"type": "boolean"}, "this_pc": CHECK, "fabric": CHECK, "proof": {"type": "object"},
                    "dependencies": {"type": "object"}}, ["ok", "this_pc", "fabric"], extra=True),
            "READ_METADATA", services.validate_notebook, artifact_types=("Notebook",), timeout=60),
    ]
    for cap in caps:
        reg.register(cap)
