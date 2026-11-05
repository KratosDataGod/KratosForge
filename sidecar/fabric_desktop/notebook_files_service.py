"""Notebook files: what the app calls (notebook_files.py does the work).

Reads: ``notebook_file_path``, ``notebook_disk_stamp`` and ``notebook_name_for_path`` (VS Code
alongside the app, and kratosforge:// links),
``notebook_listing``, ``notebook_search``, ``notebook_import_check``, ``notebook_delete_check``,
``notebook_deleted`` and ``notebook_fabric_check`` (reads a Fabric item's definition; nothing in
Fabric changes and no Fabric job runs).

Writes, on this PC only: ``notebook_save_copy``, ``notebook_import``, ``notebook_delete`` (to
Recently deleted, turning off this PC's schedules that run it), ``notebook_restore`` and
``notebook_from_fabric``. None of them replaces a notebook that is already there.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from pathlib import Path
from typing import Any

from . import notebook_files as files
from .notebook import disk_stamp, leaf_name
from .redact import describe

METHODS = {"notebook_file_path", "notebook_disk_stamp", "notebook_name_for_path", "notebook_listing", "notebook_search", "notebook_save_copy", "notebook_import_check", "notebook_import",
           "notebook_delete_check", "notebook_delete", "notebook_deleted", "notebook_restore",
           "notebook_fabric_check", "notebook_from_fabric",
           "notebook_publish_check"}  # cloud_service.py: what changed in Fabric before a Publish

_log = logging.getLogger(__name__)


class NotebookFilesMixin:
    @property
    def _published_dir(self) -> Path:
        return Path(self.root) / "published"  # type: ignore[attr-defined]

    def notebook_file_path(self, name: str) -> dict[str, Any]:
        """Where the notebook's file is, for Open in VS Code: the file, its folder, and the
        repository holding it (None for the app's own notebooks folder)."""
        path = self.notebooks.path(name)  # type: ignore[attr-defined]
        if not path.is_file():
            raise ValueError(f"There's no notebook named {name!r}.")
        place = self.notebooks.place_of(name)  # type: ignore[attr-defined]
        return {"path": str(path), "folder": str(path.parent),
                "repo_root": place["root"] if place else None, "repo_label": place["label"] if place else None,
                "notebooks_folder": str(self.notebooks.folder)}  # type: ignore[attr-defined]

    def notebook_disk_stamp(self, name: str) -> dict[str, Any]:
        """The saved file's ``disk_stamp`` (notebook.disk_stamp), so the screen can tell whether another
        program, such as VS Code, changed it. None when the file is gone or isn't a notebook."""
        try:
            nb = json.loads(self.notebooks.path(name).read_text(encoding="utf-8"))  # type: ignore[attr-defined]
            return {"disk_stamp": disk_stamp(nb) if isinstance(nb, dict) else None}
        except (OSError, ValueError):
            return {"disk_stamp": None}

    def notebook_name_for_path(self, path: str) -> dict[str, Any]:
        """The notebook a file is, for a kratosforge://notebook?path=... link: its name when the file is
        in the app's notebooks folder or a notebooks repository this PC opened, else None.

        The path is checked as text first, with no file system call: a network path (\\\\host\\share,
        //host/share, \\\\host@SSL\\dav) or a device path is refused, and so is anything outside those
        folders, because even looking at such a path makes Windows contact the host with the person's
        sign-in. Only a path that passes is resolved and read, and only when no folder or file below
        the known folder is a link or junction, which could point at a share."""
        if not _local_path(path):
            return {"name": None}
        full = os.path.abspath(path)
        if not full.lower().endswith(".ipynb"):
            return {"name": None}
        for _place, folder in self.notebooks._folders():  # type: ignore[attr-defined]
            top = os.path.abspath(folder)
            if not os.path.normcase(full).startswith(os.path.normcase(top).rstrip("\\/") + os.sep):
                continue
            name = Path(os.path.relpath(full, top)).with_suffix("").as_posix()
            if not _no_link_below(top, full):
                continue
            try:
                p = Path(full).resolve()
                if p.is_file() and self.notebooks.path(name).resolve() == p:  # type: ignore[attr-defined]
                    return {"name": name}
            except (OSError, ValueError):
                pass
        return {"name": None}

    def notebook_listing(self) -> list[dict[str, Any]]:
        """Every notebook (as list_notebooks), with the Fabric workspace it was last published to."""
        links = self.workload_store.links()  # type: ignore[attr-defined]
        return [n | {"published_to": ({"workspace_id": links[n["name"]]["workspace_id"],
                                       "workspace_name": links[n["name"]].get("workspace_name", "")}
                                      if n["name"] in links else None)}
                for n in self.notebooks.list()]  # type: ignore[attr-defined]

    def notebook_search(self, query: str, names_only: bool = False, saved_in: str | None = None,
                        open_notebook: dict[str, Any] | None = None) -> dict[str, Any]:
        return files.search(self.notebooks, query, names_only, saved_in, open_notebook)  # type: ignore[attr-defined]

    def notebook_save_copy(self, name: str, new_name: str, place: str | None = None) -> dict[str, Any]:
        return files.save_copy(self.notebooks, name, new_name, place)  # type: ignore[attr-defined]

    def notebook_import_check(self, file_name: str, content: str) -> dict[str, Any]:
        return files.import_check(self.notebooks, file_name, content)  # type: ignore[attr-defined]

    def notebook_import(self, content: str, name: str, place: str | None = None) -> dict[str, Any]:
        return files.import_file(self.notebooks, content, name, place)  # type: ignore[attr-defined]

    # ---- delete and restore

    def _pc_schedules_of(self, name: str) -> list[dict[str, Any]]:
        return [s for s in self.pc_schedules_for("notebook", name)]  # type: ignore[attr-defined]

    def notebook_delete_check(self, name: str) -> dict[str, Any]:
        """What deleting the notebook touches: where it's saved, its Fabric item and this PC's schedules."""
        self.notebooks.load(name)  # type: ignore[attr-defined]  # it exists and is readable
        link = self.workload_store.links().get(name)  # type: ignore[attr-defined]
        schedules = [s for s in self._pc_schedules_of(name) if s.get("enabled")]
        return {"name": name, "place": self.notebooks.place_of(name), "keep_days": files.KEEP_DAYS,  # type: ignore[attr-defined]
                "fabric_item": {"workspace_id": link["workspace_id"], "workspace_name": link.get("workspace_name", ""),
                                "item_id": link["item_id"]} if link else None,
                "schedules": [{"id": s["id"], "label": s.get("label", ""), "text": s.get("text", "")} for s in schedules]}

    def notebook_delete(self, name: str) -> dict[str, Any]:
        """Moves the notebook to Recently deleted. This PC's schedules that run it are turned off
        first, so none starts a notebook that isn't there; Restore turns them back on. Its Fabric
        item isn't touched."""
        self.notebooks.path(name)  # type: ignore[attr-defined]  # a valid name
        off = []
        for s in self._pc_schedules_of(name):
            if s.get("enabled"):
                self.set_pc_schedule_enabled(s["id"], False)  # type: ignore[attr-defined]
                off.append(s["id"])
        try:
            deleted = files.delete(self.notebooks, name, off)  # type: ignore[attr-defined]
        except BaseException:
            for sid in off:  # the notebook is still there: so are its schedules
                self.set_pc_schedule_enabled(sid, True)  # type: ignore[attr-defined]
            raise
        self.sessions.forget(name)  # type: ignore[attr-defined]  # its kernel, if it had one of its own
        return deleted

    def notebook_deleted(self) -> list[dict[str, Any]]:
        return files.deleted(self.notebooks)  # type: ignore[attr-defined]

    def notebook_restore(self, id: str, name: str | None = None) -> dict[str, Any]:  # noqa: A002
        out = files.restore(self.notebooks, id, name)  # type: ignore[attr-defined]
        on, left_off = [], []
        for sid in out.pop("schedules"):
            # A schedule runs a notebook by name: under a new name it would start nothing.
            if out["renamed"]:
                left_off.append(sid)
                continue
            try:
                self.set_pc_schedule_enabled(sid, True)  # type: ignore[attr-defined]
                on.append(sid)
            except (KeyError, ValueError):
                pass  # the schedule was deleted meanwhile
        return out | {"schedules_on": on, "schedules_left_off": left_off}

    # ---- a Fabric notebook on this PC

    def _linked_here(self, workspace_id: str, item_id: str) -> str | None:
        for name, link in self.workload_store.links().items():  # type: ignore[attr-defined]
            if link["item_id"] == item_id and link["workspace_id"] == workspace_id and self.notebooks._taken(name):  # type: ignore[attr-defined]  # noqa: SLF001
                return name
        return None

    def notebook_fabric_check(self, workspace_id: str, item_id: str, name: str) -> dict[str, Any]:
        """What Edit on this PC would make of a Fabric notebook item, without writing anything."""
        definition = self.fabric.get_item_definition(workspace_id, item_id, "ipynb")  # type: ignore[attr-defined]
        got = files.read(files.checked(files.ipynb_of(definition)))
        here = self._linked_here(workspace_id, item_id)
        taken = not here and self.notebooks._taken(name)  # type: ignore[attr-defined]  # noqa: SLF001
        return {"name": name, "here": here, "taken": bool(taken), "lakehouse": got["lakehouse"], **got["summary"]}

    def notebook_from_fabric(self, workspace_id: str, item_id: str, name: str, workspace_name: str = "",
                             place: str | None = None, copy_name: str | None = None) -> dict[str, Any]:
        """Saves a Fabric notebook item's cells as a notebook here, linked to that item, and keeps
        the cells as read so the next Publish can show what changed in Fabric meanwhile.

        With ``copy_name``, saves them as a separate notebook of that name, not linked to the item
        (to keep Fabric's version before replacing it with yours)."""
        if copy_name:
            definition = self.fabric.get_item_definition(workspace_id, item_id, "ipynb")  # type: ignore[attr-defined]
            got = files.read(files.checked(files.ipynb_of(definition)))
            if place is None:
                place = (self.notebooks.place_of(name) or {}).get("root") if self.notebooks._taken(name) else None  # type: ignore[attr-defined]  # noqa: SLF001
            return files.create(self.notebooks, copy_name, place, got["cells"], got["lakehouse"], got["compute"],  # type: ignore[attr-defined]
                                 {"workspace_id": workspace_id, "workspace_name": workspace_name})
        here = self._linked_here(workspace_id, item_id)
        if here:
            return {"exists": True, "name": here}
        definition = self.fabric.get_item_definition(workspace_id, item_id, "ipynb")  # type: ignore[attr-defined]
        item = {"workspace_id": workspace_id, "workspace_name": workspace_name, "item_id": item_id, "name": name}
        out = files.from_fabric(self.notebooks, definition, item, place, None)  # type: ignore[attr-defined]
        files.remember_published(self._published_dir, item_id, workspace_id, files.ipynb_of(definition),
                                 name=leaf_name(out["name"]))
        try:
            self.workload_store.record_link(out["name"], workspace_id, item_id, workspace_name, how="recorded",  # type: ignore[attr-defined]
                                            by=self._wl_user())  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the notebook is saved; Publish finds the item by name
            _log.warning("Couldn't record notebook %r's Fabric item: %s", out["name"], describe(exc))
        return out


def _no_link_below(top: str, full: str) -> bool:
    """Whether every folder and file from ``top`` down to ``full`` is a plain one: lstat looks at
    each without following it, and the walk stops at the first link, junction or missing part."""
    here = top
    for part in Path(os.path.relpath(full, top)).parts:
        here = os.path.join(here, part)
        try:
            st = os.lstat(here)
        except (OSError, ValueError):
            return False
        if _is_link(st):  # a symbolic link or junction; cloud placeholders pass
            return False
    return True


# Windows reparse tags with the name-surrogate bit point somewhere else: symbolic links (0xA000000C),
# junctions (0xA0000003), WSL links (0xA000001D) and any future link tag. Others, such as OneDrive's
# Files On-Demand placeholders (0x9000001A), are files and folders in place and pass.
_NAME_SURROGATE = 0x20000000  # IsReparseTagNameSurrogate


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_reparse_tag", 0) & _NAME_SURROGATE)


def _local_path(path: Any) -> bool:
    """A full path on a drive (C:\\... or C:/...); off Windows, an absolute path that isn't //host.
    Never a network or device path: \\\\host\\share, //host/share, \\\\?\\, \\\\.\\."""
    if not isinstance(path, str) or "\x00" in path:
        return False
    if os.name == "nt":
        return bool(re.match(r"[A-Za-z]:[\\/]", path))
    return path.startswith("/") and not path.startswith(("//", "/\\"))
