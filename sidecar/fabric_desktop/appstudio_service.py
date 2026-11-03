"""Fabric App Studio (Preview, AS-06/07/08): the engine methods behind the App Studio page.

Projects live under ``<data>/appstudio/projects`` on this PC, one ``<name>.KFApp`` folder each
(appstudio/project.py). Every method names a project by its folder name, never by a path, so a call can't
reach a folder outside that one. Changes from Design are patch operations saved with their inverse; Undo,
Redo, versions, Save As, Clone, Export and Import are the project file's own. The preview uses sample
numbers made here (appstudio/sample.py).

Nothing here calls a model, reads OneLake or writes to Fabric: model connections and publishing come in
later App Studio updates, behind an IT policy and a typed approval.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from . import sql_exec
from .appstudio import project, sample, templates
from .appstudio import spec as specs

METHODS = {"appstudio_home", "appstudio_create", "appstudio_open", "appstudio_change", "appstudio_undo",
           "appstudio_redo", "appstudio_history", "appstudio_versions", "appstudio_save_version",
           "appstudio_restore_version", "appstudio_save_as", "appstudio_export", "appstudio_import",
           "appstudio_preview"}
SOURCES_FROM_UI = {"visual", "manual"}  # an AI change arrives through its own method, after its checks


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
        path.write_bytes(data)
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
