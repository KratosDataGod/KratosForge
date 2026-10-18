"""Fabric pipeline definitions on this PC only: import, validate, inspect and export (ORCH-001), and
what this version can do with each activity (ORCH-002).

Each call takes the definition as ``text`` (pipeline-content.json or an item definition with
parts) or a ``path`` (one of those files, or a Fabric Git ``<name>.DataPipeline`` folder). Nothing
is stored, run or sent to Fabric. See docs/orchestration/plan.md.
"""

from __future__ import annotations

from typing import Any

from .orchestration import definition, expressions
from .orchestration.activities import default_registry
from .orchestration.definition import Activity, DefinitionError, ImportedPipeline
from .orchestration.validate import own_json, summary, validate

HIDDEN = "(hidden)"


def read(text: str | None = None, path: str | None = None) -> ImportedPipeline:
    if bool(text) == bool(path):
        raise DefinitionError("Give the definition as text or as a path, not both.", "bad_request")
    return definition.loads(text) if text else definition.load(path)  # type: ignore[arg-type]


def failed(exc: DefinitionError) -> dict[str, Any]:
    return {"ok": False, "diagnostics": [exc.to_json()], "counts": {"error": 1, "warning": 0, "info": 0},
            "schema": None}


def _declared(decls: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name, decl in decls.items():
        decl = decl if isinstance(decl, dict) else {}
        secure = str(decl.get("type", "")).lower() == "securestring"
        row: dict[str, Any] = {"name": name, "type": decl.get("type")}
        if "defaultValue" in decl:
            row["default"] = HIDDEN if secure else decl["defaultValue"]
        out.append(row)
    return out


def _activity(a: Activity, deep: bool) -> dict[str, Any]:
    row: dict[str, Any] = {"name": a.name, "type": a.type, "path": a.path,
                           "depends_on": [{"activity": d.activity, "conditions": d.conditions} for d in a.depends_on]}
    if a.inactive:
        row["inactive"] = True
    if isinstance(a.raw.get("description"), str):
        row["description"] = a.raw["description"]
    if deep:
        row["expressions"] = [e.to_json() for e in expressions.scan(own_json(a), a.path)]
        row["type_properties"] = sorted(a.type_properties)
    if a.children:
        row["children"] = {branch: [_activity(k, deep) for k in kids] for branch, kids in a.children.items()}
    return row


def describe(imp: ImportedPipeline, deep: bool = False) -> dict[str, Any]:
    p = imp.pipeline
    diags = validate(imp)
    counts = summary(diags)
    return {"ok": counts["error"] == 0, "name": p.name, "source_format": imp.source_format,
            "activity_count": len(p.all_activities()),
            "parameters": _declared(p.parameters), "variables": _declared(p.variables),
            "annotations": p.properties.get("annotations", []),
            "activities": [_activity(a, deep) for a in p.activities],
            "diagnostics": [d.to_json() for d in diags], "counts": counts, "schema": imp.schema()}


class PipelineMixin:
    def pipeline_import(self, text: str | None = None, path: str | None = None) -> dict[str, Any]:
        """Read a pipeline definition: its parameters, variables, activities (nested ones included),
        dependencies, diagnostics and the schema record. Stores nothing."""
        try:
            return describe(read(text, path))
        except DefinitionError as exc:
            return failed(exc)

    def pipeline_validate(self, text: str | None = None, path: str | None = None) -> dict[str, Any]:
        """Only the diagnostics, with their counts and the schema record."""
        try:
            imp = read(text, path)
        except DefinitionError as exc:
            return failed(exc)
        diags = validate(imp)
        counts = summary(diags)
        return {"ok": counts["error"] == 0, "diagnostics": [d.to_json() for d in diags], "counts": counts,
                "schema": imp.schema()}

    def pipeline_inspect(self, text: str | None = None, path: str | None = None) -> dict[str, Any]:
        """``pipeline_import`` plus each activity's expressions (with what they refer to) and its
        typeProperties keys: what a debugger, an editor or an external tool needs."""
        try:
            return describe(read(text, path), deep=True)
        except DefinitionError as exc:
            return failed(exc)

    def pipeline_export(self, text: str | None = None, path: str | None = None, format: str | None = None) -> dict[str, Any]:  # noqa: A002
        """The definition in another format (pipeline_content, item_definition, git_folder), or the
        same one. Unchanged definitions come back exactly as given. Writes no file."""
        try:
            imp = read(text, path)
            out = definition.export(imp, format)
        except DefinitionError as exc:
            return failed(exc)
        key = "files" if isinstance(out, dict) else "text"
        return {"ok": True, "format": format or imp.source_format, key: out}

    def pipeline_compatibility(self, text: str | None = None, path: str | None = None) -> dict[str, Any]:
        """For every activity (nested ones included): its classification (local, simulated, static,
        cloud, unsupported), what is planned, the properties this version can't use, the sign-in it
        needs, its side effects and whether it can only run in Fabric, plus a pipeline summary.
        Reads the definition only: nothing runs and no Fabric call is made."""
        try:
            imp = read(text, path)
        except DefinitionError as exc:
            return failed(exc) | {"registry_version": default_registry().version}
        report = default_registry().compatibility(imp)
        return {"ok": report["counts"]["error"] == 0} | report

    def pipeline_activity_registry(self, activity_type: str | None = None) -> dict[str, Any]:
        """The activity registry: every known activity type and what this version does with it, or
        one type's entry (an unknown type gets the entry for unknown types)."""
        reg = default_registry()
        if activity_type:
            return {"registry_version": reg.version, "known": reg.known(activity_type),
                    "activity": reg.get(activity_type).to_json()}
        return reg.describe()
