"""Fabric App Studio (Preview, AS-06/07/08): the engine methods behind the App Studio page.

Projects live under ``<data>/appstudio/projects`` on this PC, one ``<name>.KFApp`` folder each
(appstudio/project.py). Every method names a project by its folder name, never by a path, so a call can't
reach a folder outside that one. Changes from Design are patch operations saved with their inverse; Undo,
Redo, versions, Save As, Clone, Export and Import are the project file's own. The preview uses sample
numbers made here (appstudio/sample.py).

Model connections (AS-05/06) are the person's own AI model and key reference, kept in
``<data>/appstudio/connections.json`` with no secrets. The organization's policy (appstudio/policy.py) is
checked on every save, test and use: it can turn connections off, limit the providers and the hosts a
chosen address may reach, and refuse sample rows. Testing a connection lists the models its key can see
(no tokens are spent); nothing here sends application context to a model yet.

Nothing here reads OneLake or writes to Fabric; publishing comes in a later update, behind a typed approval.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import http_session, sql_exec
from .jsonfile import write_bytes
from .appstudio import discovery, edit, generate
from .appstudio import policy as as_policy
from .appstudio import project, sample, templates
from .appstudio import providers as prov
from .appstudio import spec as specs

METHODS = {"appstudio_home", "appstudio_create", "appstudio_open", "appstudio_change", "appstudio_undo",
           "appstudio_redo", "appstudio_history", "appstudio_versions", "appstudio_save_version",
           "appstudio_restore_version", "appstudio_save_as", "appstudio_export", "appstudio_import",
           "appstudio_preview", "appstudio_connections", "appstudio_connection_save", "appstudio_connection_delete",
           "appstudio_connection_test", "appstudio_plan", "appstudio_create_from_plan",
           "appstudio_change_plan", "appstudio_apply_change"}
SOURCES_FROM_UI = {"visual", "manual"}  # an AI change arrives through its own method, after its checks
MAX_DESCRIPTION = 4000
MAX_PLANS = 20  # plans waiting for approval, kept in memory only
PLAN_MINUTES = 60
_PLANS_LOCK = threading.Lock()  # guards every service's plan store, made once
_GUID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")


def _digest(spec: Any) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class AppStudioMixin:

    def _appstudio_root(self) -> Path:
        root = Path(self.root) / "appstudio" / "projects"  # type: ignore[attr-defined]
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _appstudio_project(self, name: str) -> Path:
        """The project folder called ``name`` under the projects folder. Refuses anything else."""
        name = str(name)
        if not name.endswith(project.SUFFIX) or project.folder_name(name[:-len(project.SUFFIX)]) != name:
            raise ValueError("Choose a project from the list.")
        root = self._appstudio_root()
        folder = root / name
        # A link of any kind (a symlink, or an NTFS junction on Windows) could point outside the projects folder.
        if (not folder.is_dir() or folder.is_symlink() or folder.is_junction()
                or folder.resolve().parent != root.resolve()):
            raise ValueError(f"{name} isn't in your App Studio projects.")
        return folder

    def _appstudio_opened(self, folder: Path) -> dict[str, Any]:
        got = project.open_project(folder)
        got["project"] = folder.name
        got.pop("path", None)
        spec = got.get("spec")
        target = spec.get("target") if isinstance(spec, dict) else None
        # The component types Design offers: the ones this app's target can express.
        got["components"] = sorted(specs.TARGETS[target]["components"]) if target in specs.TARGETS else []
        return got

    def appstudio_home(self) -> dict[str, Any]:
        """The templates, the targets and every project on this PC, newest change first."""
        rows = []
        for folder in sorted(self._appstudio_root().iterdir()):
            if folder.is_dir() and not folder.is_symlink() and folder.name.endswith(project.SUFFIX):
                try:
                    meta = project.open_project(folder)["meta"]
                except (ValueError, OSError):
                    rows.append({"project": folder.name, "name": folder.name[:-len(project.SUFFIX)], "broken": True})
                    continue
                rows.append({"project": folder.name, "name": meta.get("name"), "modified": meta.get("modified")})
        rows.sort(key=lambda r: str(r.get("modified") or ""), reverse=True)
        return {"templates": templates.catalog(), "projects": rows,
                "targets": [{"id": k, "label": v["label"], "components": sorted(v["components"]),
                             "interactions": sorted(v["interactions"])} for k, v in specs.TARGETS.items()]}

    def appstudio_create(self, name: str, template: str = "blank", target: str = "powerbi-report") -> dict[str, Any]:
        """A new project from a template. Refuses a name that's already a project."""
        clean = " ".join(str(name).split())[:120]
        if not clean:
            raise ValueError("Give the app a name.")
        doc = templates.build(str(template), clean, str(target))
        folder = project.create(self._appstudio_root(), clean, doc)
        return self._appstudio_opened(folder)

    def appstudio_open(self, project_name: str) -> dict[str, Any]:
        return self._appstudio_opened(self._appstudio_project(project_name))

    def appstudio_change(self, project_name: str, ops: list[Any], note: str = "", source: str = "visual") -> dict[str, Any]:
        """Apply Design's change (patch operations). All of it or none of it is saved."""
        if source not in SOURCES_FROM_UI:
            raise ValueError("source must be visual or manual.")
        folder = self._appstudio_project(project_name)
        project.change(folder, ops, note=str(note), source=source)
        return self._appstudio_opened(folder)

    def appstudio_undo(self, project_name: str) -> dict[str, Any]:
        folder = self._appstudio_project(project_name)
        project.undo(folder)
        return self._appstudio_opened(folder)

    def appstudio_redo(self, project_name: str) -> dict[str, Any]:
        folder = self._appstudio_project(project_name)
        project.redo(folder)
        return self._appstudio_opened(folder)

    def appstudio_history(self, project_name: str, limit: int = 100) -> list[dict[str, Any]]:
        return project.history(self._appstudio_project(project_name), limit)

    def appstudio_versions(self, project_name: str) -> list[dict[str, Any]]:
        return list(reversed(project.versions(self._appstudio_project(project_name))))

    def appstudio_save_version(self, project_name: str, label: str = "") -> dict[str, Any]:
        return project.save_version(self._appstudio_project(project_name), str(label))

    def appstudio_restore_version(self, project_name: str, version: int) -> dict[str, Any]:
        """Restore a version. What was current is kept as a new version first."""
        folder = self._appstudio_project(project_name)
        kept = project.restore_version(folder, version)["kept_as"]
        return {**self._appstudio_opened(folder), "kept_as": kept}

    def appstudio_save_as(self, project_name: str, name: str, clone: bool = False) -> dict[str, Any]:
        """Save As (keeps history and versions) or Clone (starts fresh) to a new project."""
        dest = project.save_as(self._appstudio_project(project_name), self._appstudio_root(), str(name), clone=clone)
        return self._appstudio_opened(dest)

    def appstudio_export(self, project_name: str) -> dict[str, Any]:
        """The project as a new .zip in Downloads; never replaces a file."""
        folder = self._appstudio_project(project_name)
        data = project.export(folder)
        out = Path.home() / "Downloads"
        out.mkdir(parents=True, exist_ok=True)
        path = sql_exec._reserve(out, folder.name[:-len(project.SUFFIX)] + " (App Studio)", ".zip")
        write_bytes(path, data)  # swapped in over the empty name reserved for it
        return {"path": str(path), "bytes": len(data)}

    def appstudio_import(self, content: str, name: str = "") -> dict[str, Any]:
        """A project from an exported .zip (base64), checked file by file, as a new project."""
        try:
            data = base64.b64decode(str(content), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("That file isn't an App Studio export.") from None
        if len(data) > project.MAX_EXPORT_BYTES:
            raise ValueError("That file is larger than an App Studio project can be.")
        folder = project.import_(data, self._appstudio_root(), str(name))
        return self._appstudio_opened(folder)

    def appstudio_preview(self, project_name: str, page: str = "", selection: dict[str, Any] | None = None) -> dict[str, Any]:
        """One page with sample numbers made on this PC, for the local preview."""
        got = project.open_project(self._appstudio_project(project_name))
        if not got["valid"]:
            raise specs.SpecError(got["problems"])
        return sample.preview(got["spec"], str(page), selection)

    # ---------------------------------------------------------------- model connections (AS-05/06)

    def _appstudio_store(self) -> prov.ConnectionStore:
        return prov.ConnectionStore(Path(self.root) / "appstudio")  # type: ignore[attr-defined]

    def _appstudio_model(self, conn_id: str = "") -> dict[str, Any]:
        """A saved connection the organization's policy allows now; the default when no id is given.
        Every use of a model goes through here, so a policy change applies at once."""
        conn = self._appstudio_store().get(str(conn_id))
        as_policy.check(conn)
        return conn

    def appstudio_connections(self) -> dict[str, Any]:
        """The organization's policy, the providers it allows, and every saved connection with this month's
        usage and why the policy stops it, if it does. Key references only: no key is read."""
        pol = as_policy.current()
        store = self._appstudio_store()
        listed = store.list()
        conns = [{**c, "usage": store.usage(c["id"]), "refused": as_policy.refusals(c, pol)} for c in listed["connections"]]
        catalog = [{"id": k, "label": v["label"], "endpoint": v["endpoint"], "key": v["key"],
                    "capabilities": dict(v["capabilities"]),
                    "allowed": pol["enabled"] and (pol["providers"] is None or k in pol["providers"])}
                   for k, v in prov.PROVIDERS.items()]
        return {"policy": {k: pol[k] for k in ("enabled", "providers", "hosts", "sample_rows", "problems")},
                "providers": catalog, "connections": conns, "default": listed["default"], "key_ref_help": prov.KEY_REF_HELP}

    def appstudio_connection_save(self, connection: dict[str, Any], make_default: bool = False) -> dict[str, Any]:
        """Add or change a connection. Refused when it has problems or the policy doesn't allow it."""
        clean = prov.normalize({k: v for k, v in connection.items() if k not in ("capabilities", "usage", "refused")})
        as_policy.check(clean)
        saved = self._appstudio_store().save(clean, make_default=make_default)
        return {**saved, "capabilities": prov.capabilities(saved)}

    def appstudio_connection_delete(self, conn_id: str) -> dict[str, Any]:
        """Remove a connection from this PC. Its key stays where it is (Credential Manager or the variable)."""
        store = self._appstudio_store()
        store.get(str(conn_id))  # a connection that isn't there says so
        store.delete(str(conn_id))
        return {"deleted": str(conn_id)}

    def appstudio_connection_test(self, conn_id: str = "") -> dict[str, Any]:
        """Check a connection works: read its key and list the models the key can see. Spends no tokens and
        sends no application context."""
        return prov.validate(self._appstudio_model(conn_id), http_session.session())

    # ---------------------------------------------------------------- generation (AS-10)

    def _appstudio_plan_store(self) -> dict[str, dict[str, Any]]:
        with _PLANS_LOCK:
            if getattr(self, "_appstudio_plans", None) is None:
                self._appstudio_plans: dict[str, dict[str, Any]] = {}
            return self._appstudio_plans

    def _appstudio_metadata(self, source: dict[str, Any] | None, include_expressions: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        """(metadata, spec source) for a plan: a semantic model's sanitized metadata, or the sample fields."""
        if not source:
            return generate.sample_metadata(), {"kind": "sample", "name": "Sample data"}
        ws, item = str(source.get("workspace_id", "")), str(source.get("item_id", ""))
        if not (_GUID.fullmatch(ws) and _GUID.fullmatch(item)):
            raise ValueError("Choose a semantic model: its workspace id and item id.")
        name = redact_name(source.get("name")) or "Semantic model"
        bim = self._model_bim(ws, {"id": item, "displayName": name})  # type: ignore[attr-defined]
        meta = discovery.from_bim(bim, include_expressions=include_expressions)
        return meta, {"kind": "semantic-model", "name": name, "workspace_id": ws, "item_id": item}

    def appstudio_plan(self, description: str, target: str = "powerbi-report", source: dict[str, Any] | None = None,
                       conn_id: str = "", include_expressions: bool = False) -> dict[str, Any]:
        """Ask the person's model for a plan. Sends the description and sanitized metadata only (no rows); measure
        formulas only with ``include_expressions``. The plan is checked against the metadata and kept here,
        in memory, until it is approved (appstudio_create_from_plan) or an hour passes. Nothing is saved."""
        text = " ".join(str(description).split())
        if not text:
            raise ValueError("Describe the application first.")
        if len(text) > MAX_DESCRIPTION:
            raise ValueError(f"Keep the description under {MAX_DESCRIPTION} characters.")
        if target not in specs.TARGETS:
            raise ValueError(f"target must be one of: {', '.join(sorted(specs.TARGETS))}.")
        conn = self._appstudio_model(conn_id)  # the organization's policy, every time
        if not conn["consent"]["context"]:  # before the semantic model is even read
            raise prov.ConsentError("Agree to send application context to this model first (App Studio > Model connections).")
        meta, spec_source = self._appstudio_metadata(source, bool(include_expressions))
        got = prov.complete(conn, self._appstudio_store(), http_session.session(), generate.instruction(target), text,
                            generate.PLAN_SCHEMA, generate.context(meta))
        checked = generate.check_plan(got["result"], meta, target)
        plans = self._appstudio_plan_store()
        plan_id = str(uuid.uuid4())
        now = time.monotonic()
        with _PLANS_LOCK:
            for k in [k for k, v in plans.items() if now - v["at"] > PLAN_MINUTES * 60]:
                del plans[k]
            while len(plans) >= MAX_PLANS:
                del plans[min(plans, key=lambda k: plans[k]["at"])]
            plans[plan_id] = {"metadata": meta, "target": target, "source": spec_source, "at": now}
        return {"plan_id": plan_id, "plan": checked["plan"], "left_out": checked["left_out"],
                "sent": {**generate.summary(meta), "description_chars": len(text)},
                "connection": {"name": conn["name"], "provider": conn["provider"], "model": conn["model"]},
                "usage": got["usage"], "warning": got["warning"]}

    def appstudio_create_from_plan(self, plan_id: str, plan: dict[str, Any], name: str = "") -> dict[str, Any]:
        """Approve a plan: check it again against the same metadata (the person may have edited it), make the
        specification here and save it as a new project. The plan can be used once."""
        plans = self._appstudio_plan_store()
        with _PLANS_LOCK:  # taken out at once, so two approvals of the same plan can't both use it
            kept = plans.pop(str(plan_id), None)
        if kept is None or kept.get("kind") == "change" or time.monotonic() - kept["at"] > PLAN_MINUTES * 60:
            if kept is not None and kept.get("kind") == "change":
                with _PLANS_LOCK:
                    plans.setdefault(str(plan_id), kept)
            raise ValueError("That plan has expired. Ask for a new one.")
        try:
            checked = generate.check_plan(plan, kept["metadata"], kept["target"])
            doc = generate.to_spec(checked["plan"], kept["metadata"], kept["target"], kept["source"],
                                   " ".join(str(name).split())[:120])
            folder = project.create(self._appstudio_root(), doc["name"], doc)
        except Exception:
            with _PLANS_LOCK:  # nothing was made: the plan can be fixed and approved again
                plans.setdefault(str(plan_id), kept)
            raise
        return {**self._appstudio_opened(folder), "left_out": checked["left_out"]}


    # ---------------------------------------------------------------- conversational edits (AS-11)

    def _appstudio_project_metadata(self, spec: dict[str, Any], include_expressions: bool) -> dict[str, Any]:
        """The metadata an edit may use: the app's semantic model, or the sample fields."""
        src = next((s for s in spec["sources"] if s.get("kind") == "semantic-model"), None)
        if src is None:
            return generate.sample_metadata()
        return self._appstudio_metadata({"workspace_id": src.get("workspace_id", ""), "item_id": src.get("item_id", ""),
                                         "name": src.get("name", "")}, include_expressions)[0]

    def appstudio_change_plan(self, project_name: str, request: str, conn_id: str = "",
                              include_expressions: bool = False) -> dict[str, Any]:
        """Ask the person's model how to make a change, and show the result without saving it. The model sees the
        app's pages and components (ids, types, titles, measure names, fields) and the source's sanitized
        metadata; never rows, and formulas only with ``include_expressions``. Its answer becomes patch operations
        here, applied to a copy that must validate. Approve with appstudio_apply_change."""
        text = " ".join(str(request).split())
        if not text:
            raise ValueError("Say what to change first.")
        if len(text) > MAX_DESCRIPTION:
            raise ValueError(f"Keep the request under {MAX_DESCRIPTION} characters.")
        folder = self._appstudio_project(project_name)
        got = project.open_project(folder)
        if not got["valid"]:
            raise specs.SpecError(got["problems"])
        spec = got["spec"]
        conn = self._appstudio_model(conn_id)
        if not conn["consent"]["context"]:
            raise prov.ConsentError("Agree to send application context to this model first (App Studio > Model connections).")
        meta = self._appstudio_project_metadata(spec, bool(include_expressions))
        answer = prov.complete(conn, self._appstudio_store(), http_session.session(), edit.instruction(spec["target"]),
                               text, edit.CHANGE_SCHEMA, edit.context(spec, meta))
        made = edit.to_ops(answer["result"], spec, meta)
        result = edit.preview(spec, made["ops"])
        plans = self._appstudio_plan_store()
        change_id = str(uuid.uuid4())
        now = time.monotonic()
        with _PLANS_LOCK:
            for k in [k for k, v in plans.items() if now - v["at"] > PLAN_MINUTES * 60]:
                del plans[k]
            while len(plans) >= MAX_PLANS:
                del plans[min(plans, key=lambda k: plans[k]["at"])]
            plans[change_id] = {"kind": "change", "project": folder.name, "ops": made["ops"], "base": _digest(spec),
                                "note": made["summary"], "at": now}
        return {"change_id": change_id, "summary": made["summary"], "done": made["done"], "left_out": made["left_out"],
                "questions": made["questions"], "changed": result["changed"], "spec": result["spec"],
                "sent": {**generate.summary(meta), "app_components": sum(len(p["components"]) for p in spec["pages"]),
                         "request_chars": len(text)},
                "connection": {"name": conn["name"], "provider": conn["provider"], "model": conn["model"]},
                "usage": answer["usage"], "warning": answer["warning"]}

    def appstudio_apply_change(self, project_name: str, change_id: str) -> dict[str, Any]:
        """Save an approved change as one step with its inverse (Undo takes it back). Refused when the app changed
        since the plan was made; a change can be applied once."""
        folder = self._appstudio_project(project_name)
        plans = self._appstudio_plan_store()
        with _PLANS_LOCK:
            kept = plans.pop(str(change_id), None)
        if (kept is None or kept.get("kind") != "change" or kept["project"] != folder.name
                or time.monotonic() - kept["at"] > PLAN_MINUTES * 60):
            if kept is not None and kept.get("kind") == "change" and kept["project"] != folder.name:
                with _PLANS_LOCK:
                    plans.setdefault(str(change_id), kept)
            raise ValueError("That change has expired. Ask again.")
        current = project.open_project(folder)
        if _digest(current["spec"]) != kept["base"]:
            raise ValueError("The app changed since this was planned. Ask again so the change fits what's there now.")
        try:
            project.change(folder, kept["ops"], note=kept["note"], source="ai")
        except Exception:
            with _PLANS_LOCK:
                plans.setdefault(str(change_id), kept)
            raise
        return self._appstudio_opened(folder)


def redact_name(value: Any) -> str:
    from .redact import one_line
    return one_line(value, 120) if isinstance(value, str) else ""
