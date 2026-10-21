"""Folders for notebooks: what the app calls (notebook_folders.py does the work).

Read: ``notebook_folders``. Writes, on this PC only (in a notebooks repository they show as changes
to commit): ``notebook_folder_create``, ``notebook_folder_rename``, ``notebook_folder_delete`` (an
empty folder only) and ``notebook_move_to_folder``. None of them replaces or deletes a notebook, and
nothing in Fabric changes.
"""

from __future__ import annotations

from typing import Any

from . import notebook_folders as folders

METHODS = {"notebook_folders", "notebook_folder_create", "notebook_folder_rename", "notebook_folder_delete",
           "notebook_move_to_folder"}


class NotebookFoldersMixin:
    def notebook_folders(self) -> dict[str, Any]:
        """Every folder in every place, with how many notebooks each holds, and how deep they go."""
        return {"folders": folders.listing(self.notebooks), "max_depth": folders.MAX_FOLDER_DEPTH}  # type: ignore[attr-defined]

    def notebook_folder_create(self, path: str, place: str | None = None) -> dict[str, Any]:
        """Makes an empty folder in the app's notebooks folder, or in the notebooks repository ``place``."""
        return folders.create(self.notebooks, path, place)  # type: ignore[attr-defined]

    def notebook_folder_rename(self, path: str, new_name: str, place: str | None = None) -> dict[str, Any]:
        """Renames the folder's last part. Its notebooks and their resources move with it on disk; their
        versions, this PC's schedules, sessions, environments and Fabric links follow the new names."""
        plan = folders.rename_plan(self.notebooks, path, new_name, place)  # type: ignore[attr-defined]
        folders.rename(self.notebooks, plan)  # type: ignore[attr-defined]
        schedules: list[str] = []
        problems: list[dict[str, str]] = []
        for m in plan["moves"]:  # every notebook, every step: one failure never leaves the rest behind
            done = self._notebook_renamed(m["from"], m["to"])  # type: ignore[attr-defined]
            schedules += done["schedules_moved"]
            problems += done["problems"]
        return {"path": plan["path"], "new_path": plan["new_path"], "moved": [{"from": m["from"], "to": m["to"]} for m in plan["moves"]],
                "schedules_moved": schedules, "problems": problems}

    def notebook_folder_delete(self, path: str, place: str | None = None) -> dict[str, Any]:
        """Deletes the folder only when it's empty; one with anything in it is refused."""
        return folders.delete(self.notebooks, path, place)  # type: ignore[attr-defined]

    def notebook_move_to_folder(self, name: str, folder: str = "") -> dict[str, Any]:
        """Moves the notebook into ``folder`` of its place ("" for none), as a rename: its resources
        folder, versions, this PC's schedules, session, environment and Fabric link go with it. A
        notebook of that name already there stops it, and nothing moves."""
        new = folders.move_target(self.notebooks, name, folder)  # type: ignore[attr-defined]
        if new == name:
            return {"name": name, "moved": False, "schedules_moved": [], "problems": []}
        out = self.rename_notebook(name, new)  # type: ignore[attr-defined]
        return {"name": new, "was": name, "moved": True, "schedules_moved": out.get("schedules_moved", []),
                "problems": out.get("problems", [])}
