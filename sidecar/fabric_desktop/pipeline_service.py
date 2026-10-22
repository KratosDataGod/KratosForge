"""Fabric pipeline definitions on this PC only: import, validate, inspect and export (ORCH-001),
what this version can do with each activity (ORCH-002), and local runs (ORCH-003).

Each call takes the definition as ``text`` (pipeline-content.json or an item definition with
parts) or a ``path`` (one of those files, or a Fabric Git ``<name>.DataPipeline`` folder). Nothing
is stored, run or sent to Fabric. See docs/orchestration/plan.md.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .orchestration import definition, expressions
from .orchestration.activities import default_registry
from .orchestration.definition import Activity, DefinitionError, ImportedPipeline
from .orchestration.runner import PipelineRun, RunRefused
from .orchestration.runs import RunStore
from .orchestration.validate import own_json, summary, validate
from .pipeline_data import LocalData
from .pipeline_notebooks import LocalNotebooks

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


MAX_RUNS = 2          # pipeline runs going at once on this PC
_SAVE_EVERY = 0.5     # seconds between saves of a running run's record


class PipelineRunMixin:
    """Local pipeline runs (ORCH-003): check, start, follow, cancel and list. Runs happen in this
    process, on this PC only; records go to ``<data>/pipeline_runs``. See docs/orchestration/local-runs.md."""

    root: Path

    def _runs(self) -> tuple[RunStore, dict[str, PipelineRun]]:
        if not hasattr(self, "_pipeline_run_store"):
            self._pipeline_run_store = RunStore(self.root / "pipeline_runs")
            self._pipeline_live: dict[str, PipelineRun] = {}
        return self._pipeline_run_store, self._pipeline_live

    def _prepare(self, text: str | None, path: str | None, parameters: dict[str, Any] | None,
                 mocks: dict[str, Any] | None, children: dict[str, str] | None,
                 notebooks: dict[str, str] | None = None, allow_uncontained: bool = False,
                 keep_outputs: bool = False, read_lakehouses: bool = False, workspace_id: str | None = None,
                 **options: Any) -> PipelineRun:
        imp = read(text, path)
        kids = {}
        for key, definition_text in (children or {}).items():
            if not isinstance(definition_text, str):
                raise DefinitionError(f"The child definition for {key!r} must be given as text.", "bad_request")
            kids[key] = definition.loads(definition_text)
        if notebooks is not None and not isinstance(notebooks, dict):
            raise DefinitionError("notebooks must be {activity name or notebookId: notebook name}.", "bad_request")
        run_id = str(uuid.uuid4())
        # Notebook steps need the engine's notebooks and clean kernel (validate_service.py), and a
        # caller that names the notebooks: external tools' calls (integrations/services.py) don't.
        runner = (LocalNotebooks(self, run_id, notebooks, allow_uncontained, keep_outputs)
                  if notebooks is not None and hasattr(self, "_validate_kernel") and hasattr(self, "notebooks") else None)
        # Lookup and Get Metadata read lakehouses only when the person starts the run with lakehouse
        # reads on; external tools' calls don't, so there they still need mocks.
        data = (LocalData(self, run_id, runner, workspace_id)
                if read_lakehouses and hasattr(self, "_proof_location") else None)
        return PipelineRun(imp, parameters=parameters, mocks=mocks, children=kids, run_id=run_id, notebooks=runner,
                           data=data, **options)

    def _pipeline_list_files(self, ref: dict[str, str], rel: str) -> list[dict[str, Any]]:
        """Children of a folder under the lakehouse's Files (Get Metadata's childItems)."""
        from .onelake import FileStore
        return FileStore(self.auth.storage_token).list(ref["workspace_id"], ref["lakehouse_id"], rel)  # type: ignore[attr-defined]

    def _pipeline_file_info(self, ref: dict[str, str], rel: str) -> dict[str, Any] | None:
        """``{"is_dir", "size", "modified"}`` of a file or folder under Files, or None when it isn't there."""
        import requests
        parent, _, name = rel.rpartition("/")
        if not parent:
            return {"is_dir": True, "size": 0, "modified": ""}   # Files itself
        try:
            entries = self._pipeline_list_files(ref, parent)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise
        return next((e for e in entries if e["name"] == name), None)

    def pipeline_run_check(self, text: str | None = None, path: str | None = None,
                           parameters: dict[str, Any] | None = None, mocks: dict[str, Any] | None = None,
                           children: dict[str, str] | None = None, notebooks: dict[str, str] | None = None,
                           allow_uncontained: bool = False, read_lakehouses: bool = False,
                           workspace_id: str | None = None) -> dict[str, Any]:
        """Whether this pipeline can run on this PC with these parameters, mocks and notebooks, and
        if not, why, activity by activity. Runs nothing."""
        try:
            run = self._prepare(text, path, parameters, mocks, children, notebooks, allow_uncontained,
                                read_lakehouses=read_lakehouses, workspace_id=workspace_id)
        except DefinitionError as exc:
            return {"ok": False, "problems": [exc.to_json()]}
        problems = run.check()
        return {"ok": not problems, "problems": problems, "pipeline": run.pipeline.name,
                "mocked": sorted(run.mocks), "registry_version": run.registry.version}

    def pipeline_run_start(self, text: str | None = None, path: str | None = None,
                           parameters: dict[str, Any] | None = None, mocks: dict[str, Any] | None = None,
                           children: dict[str, str] | None = None, max_parallel: int = 4, wait_scale: float = 1.0,
                           timeout_seconds: float | None = None, notebooks: dict[str, str] | None = None,
                           allow_uncontained: bool = False, keep_outputs: bool = False,
                           read_lakehouses: bool = False, workspace_id: str | None = None) -> dict[str, Any]:
        """Start a local run in the background and return its id at once. Refuses (with the
        problems) when an activity can't run here and has no mock. Nothing is sent to Fabric.

        ``notebooks`` says which notebook on this PC each Notebook activity runs ({activity name or
        notebookId: notebook name}). Notebook steps read the lakehouse's tables and write to the
        run's own folder in it, deleted when the run ends unless ``keep_outputs``; a notebook that
        calls the network or reads files on this PC needs ``allow_uncontained``. Without
        ``notebooks``, Notebook activities need a mock. With ``read_lakehouses``, Lookup and Get
        Metadata read the lakehouse their dataset names (pipeline_data.py), at the same pinned table
        versions; ``workspace_id`` is the pipeline's own workspace, for datasets that leave it out."""
        store, live = self._runs()
        store.recover(set(live))
        if sum(1 for r in live.values() if r.state in ("pending", "running")) >= MAX_RUNS:
            return {"ok": False, "problems": [{"code": "busy", "message": f"{MAX_RUNS} pipeline runs are already going "
                                               "on this PC; wait for one to finish or cancel it.", "activity": None,
                                               "path": "$"}]}
        try:
            run = self._prepare(text, path, parameters, mocks, children, notebooks, allow_uncontained, keep_outputs,
                                read_lakehouses=read_lakehouses, workspace_id=workspace_id,
                                max_parallel=max_parallel, wait_scale=wait_scale, timeout_seconds=timeout_seconds)
        except DefinitionError as exc:
            return {"ok": False, "problems": [exc.to_json()]}
        problems = run.check()
        if problems:
            return {"ok": False, "problems": problems}
        acts = run.pipeline.all_activities()
        if (run.notebooks is not None and run.notebooks.lakehouses(acts)) or (
                run.data is not None and run.data.lakehouses(acts)):
            self.auth.storage_token()  # type: ignore[attr-defined]  # sign in now, while the app is in front
        last = [0.0]

        def save(r: PipelineRun) -> None:
            now = time.monotonic()
            if r.state in ("pending", "running") and now - last[0] < _SAVE_EVERY:
                return
            last[0] = now
            store.save(r.to_json())

        run.on_change = save
        live[run.id] = run
        store.save(run.to_json())

        def go() -> None:
            try:
                run.run()
            except RunRefused:
                pass
            finally:
                if run.notebooks is not None:
                    run.notebooks.stop()
                    run.notebooks.finish()  # already done unless the run raised; then it cleans up here
                store.save(run.to_json())
                store.trim()
                for rid in [k for k, r in live.items() if r.state not in ("pending", "running")][:-MAX_RUNS]:
                    live.pop(rid, None)

        threading.Thread(target=go, name=f"pipeline-run-{run.id[:8]}", daemon=True).start()
        return {"ok": True, "run_id": run.id, "state": "running", "pipeline": run.pipeline.name}

    def pipeline_run_status(self, run_id: str, values: bool = True) -> dict[str, Any]:
        """A run's record: state, every activity run with its state, output and error, variables and
        return value. ``values=False`` leaves out outputs and variable values."""
        store, live = self._runs()
        run = live.get(run_id)
        if run is not None:
            return run.to_json(values)
        try:
            record = store.get(run_id)
        except ValueError:
            record = None
        if record is None:
            raise KeyError(f"no pipeline run {run_id}")
        return record if values else _without_values(record)

    def pipeline_run_cancel(self, run_id: str) -> dict[str, Any]:
        """Stop a run: nothing new starts, waits end at once, running activities stop where they can."""
        _, live = self._runs()
        run = live.get(run_id)
        if run is None or run.state not in ("pending", "running"):
            return {"cancelled": False, "state": run.state if run else "unknown"}
        run.cancel()
        if run.notebooks is not None:
            run.notebooks.stop()
        return {"cancelled": True, "state": "cancelling"}

    def pipeline_runs(self, limit: int = 50) -> dict[str, Any]:
        """Recent local runs, newest first, without activity details."""
        store, live = self._runs()
        rows = {r["run_id"]: r for r in store.list(limit)}
        for run in live.values():
            rows[run.id] = run.to_json(values=False)
        keep = ("run_id", "pipeline", "state", "stop_reason", "started", "ended", "seconds", "counts", "options")
        ordered = sorted(rows.values(), key=lambda r: r.get("started") or "", reverse=True)[:limit]
        return {"runs": [{k: r.get(k) for k in keep} for r in ordered]}


def _without_values(record: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in record.items() if k not in ("variables", "return_value")}
    params = record.get("parameters")
    out["parameters"] = sorted(params) if isinstance(params, dict) else params
    acts = []
    for a in record.get("activities", []):
        a = {k: v for k, v in a.items() if k not in ("output", "child_run")}
        if isinstance(a.get("error"), dict):
            a["error"] = {"errorCode": a["error"].get("errorCode")}
        acts.append(a)
    out["activities"] = acts
    return out
