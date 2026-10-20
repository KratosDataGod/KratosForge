"""Notebook resources: what the notebook's explorer calls (notebook_resources.py has the folder).

Files are copied in from this PC, never over a file already there; rename never replaces another
file; delete removes one file. Nothing here reaches Fabric or OneLake.
"""

from __future__ import annotations

import base64
import binascii
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .notebook_resources import Resources

METHODS = {"notebook_resources", "notebook_resource_add", "notebook_resource_rename",
           "notebook_resource_delete", "notebook_resource_reveal"}


class NotebookResourcesMixin:
    def _resources(self, name: str) -> Resources:
        if not self.notebooks.path(name).is_file():  # type: ignore[attr-defined]
            raise ValueError(f"There's no notebook named {name!r}. Save it first.")
        return Resources(self.notebooks.resources(name))  # type: ignore[attr-defined]

    def _resources_dir(self, name: str | None) -> str:
        """The folder ``builtin/`` paths and nbResPath point at while ``name`` runs; "" for none."""
        if not name:
            return ""
        try:
            return str(self.notebooks.resources(name))  # type: ignore[attr-defined]
        except ValueError:  # not a name a notebook can have (a cell run from elsewhere)
            return ""

    def notebook_resources(self, name: str) -> dict[str, Any]:
        """The notebook's resource files, with their sizes, and the folder they are in."""
        return self._resources(name).list()

    def notebook_resource_add(self, name: str, file_name: str, content: str) -> dict[str, Any]:
        """Copies one file (``content`` is its bytes in base64) into the notebook's resources. When a
        file of that name is there, nothing is written and the answer suggests a free name."""
        try:
            data = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("The file didn't arrive whole. Add it again.") from None
        return self._resources(name).add(file_name, data)

    def notebook_resource_rename(self, name: str, file_name: str, new_name: str) -> dict[str, Any]:
        return self._resources(name).rename(file_name, new_name)

    def notebook_resource_delete(self, name: str, file_name: str) -> dict[str, Any]:
        return self._resources(name).delete(file_name)

    def notebook_resource_reveal(self, name: str, file_name: str | None = None) -> dict[str, Any]:
        """Shows the file (or, without one, the folder) in File Explorer. Files aren't opened: one
        could be a program. Showing the folder makes it when the notebook has none yet."""
        res = self._resources(name)
        if file_name:
            target = res.file(file_name)
        else:
            res.files.mkdir(parents=True, exist_ok=True)
            target = res.files
        _reveal(target, select=bool(file_name))
        return {"shown": str(target)}


def _reveal(path: Path, select: bool) -> None:
    if sys.platform == "win32":
        args = ["explorer", f"/select,{path}"] if select else ["explorer", str(path)]
    elif sys.platform == "darwin":
        args = ["open", "-R", str(path)] if select else ["open", str(path)]
    else:
        args = ["xdg-open", str(path.parent if select else path)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,  # noqa: S603
                     creationflags=flags, close_fds=os.name != "nt")

