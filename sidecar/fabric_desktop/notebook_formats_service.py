"""Notebook export and import (notebook_formats.py has the formats).

``notebook_export`` writes one new file in the Downloads folder and never replaces one: a name that
is taken gets a number. ``notebook_formats_import_check`` reads a file the person picked (its content is
sent by the app; the file itself is never opened or changed here) and says what importing would
create. ``notebook_formats_import`` then writes one new notebook per notebook found, into the app's own
notebooks folder, each under a name no notebook has yet: the name is reserved by creating the
file (open "xb"), so an existing notebook is never replaced.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from pathlib import Path
from typing import Any

from . import notebook_formats as nbf
from .filenames import file_stem
from .notebook import to_ipynb

METHODS = {"notebook_export", "notebook_formats_import_check", "notebook_formats_import"}


def _read(file_name: str, content: str, encoding: str) -> dict[str, Any]:
    """{"format", "notebooks": [{"name", "folder", "cells", "notes"}]} from the picked file's content.
    ``notes`` say what a Databricks notebook's conversion dropped or changed (notebook_formats)."""
    suffix = Path(file_name.replace("\\", "/")).suffix.lower()
    stem = Path(file_name.replace("\\", "/")).stem
    if suffix == ".dbc":
        if encoding != "base64":
            raise ValueError("A .dbc archive is sent as base64.")
        if len(content) > nbf.MAX_DBC * 4 // 3 + 4:
            raise ValueError("The archive is larger than 64 MB, so it wasn't read.")
        try:
            data = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("The archive didn't arrive whole. Pick it again.") from None
        return {"format": "Databricks archive (.dbc)", "notebooks": nbf.parse_dbc(data)}
    if encoding != "text":
        raise ValueError("A notebook file is sent as text.")
    if suffix == ".ipynb":
        parsed = nbf.parse_ipynb(content)
    elif suffix in (".py", ".sql", ".scala"):
        parsed = nbf.parse_source(content, file_name)
    else:
        raise ValueError("KratosForge imports .ipynb, .py, .sql, .scala and .dbc notebook files.")
    return {"format": parsed["format"], "notebooks": [{"name": stem, "folder": "", "cells": parsed["cells"],
                                                       "notes": parsed.get("notes", [])}]}


class NotebookFormatsMixin:
    def _free_notebook_names(self, wanted: list[str]) -> list[str]:
        """A name for each, as the notebook would be called: the file's own name, or with " (2)"
        and so on when a notebook (or an earlier one in this import) already has it."""
        shelf = self.notebooks  # type: ignore[attr-defined]
        out: list[str] = []
        for w in wanted:
            base = nbf.notebook_name(w)
            for n in range(1, 10000):
                name = base if n == 1 else f"{base} ({n})"
                if name not in out and not shelf._taken(name):
                    out.append(name)
                    break
            else:
                raise ValueError(f"Couldn't find a free notebook name for {base}.")
        return out

    def notebook_formats_import_check(self, file_name: str, content: str, encoding: str = "text") -> dict[str, Any]:
        """What importing the file would create; nothing is written."""
        read = _read(file_name, content, encoding)
        names = self._free_notebook_names([nb["name"] for nb in read["notebooks"]])
        return {"format": read["format"], "notebooks": [
            {"name": name, "source_name": nb["name"], "folder": nb["folder"], "cells": len(nb["cells"]),
             "markdown": sum(c["type"] == "markdown" for c in nb["cells"]),
             "languages": sorted({(c["source"].split("\n", 1)[0] if c["source"].startswith("%%") else "python")
                                  for c in nb["cells"] if c["type"] == "code"}),
             "notes": nb.get("notes", [])}
            for name, nb in zip(names, read["notebooks"])]}

    def notebook_formats_import(self, file_name: str, content: str, encoding: str = "text") -> dict[str, Any]:
        """Creates a new notebook for each one in the file, in the app's own notebooks folder.
        Returns {"format", "created": [names], "notes": {name: notes}} (notes only for notebooks that have some). A name taken between the check and now gets the
        next number; a notebook that is already there is never touched."""
        read = _read(file_name, content, encoding)
        folder: Path = self.notebooks.folder  # type: ignore[attr-defined]
        folder.mkdir(parents=True, exist_ok=True)
        created: list[str] = []
        notes: dict[str, list[dict[str, Any]]] = {}
        try:
            for nb in read["notebooks"]:
                text = json.dumps(to_ipynb(nb["cells"] or [{"type": "code", "source": ""}], None), indent=1)
                created.append(self._write_new_notebook(folder, nb["name"], created, text))
                if nb.get("notes"):
                    notes[created[-1]] = nb["notes"]
        except OSError as e:
            raise OSError(f"Couldn't write the imported notebooks ({e}). {len(created)} of "
                          f"{len(read['notebooks'])} were created: {', '.join(created) or 'none'}.") from None
        return {"format": read["format"], "created": created, "notes": notes}

    def _write_new_notebook(self, folder: Path, wanted: str, taken: list[str], text: str) -> str:
        base = nbf.notebook_name(wanted)
        for n in range(1, 10000):
            name = base if n == 1 else f"{base} ({n})"
            if name in taken or self.notebooks._taken(name):  # type: ignore[attr-defined]
                continue
            path = folder / f"{name}.ipynb"
            try:
                f = open(path, "xb")  # refuses when a file of that name is already there
            except FileExistsError:
                continue
            try:
                with f:
                    f.write(text.encode("utf-8"))
            except OSError:
                path.unlink(missing_ok=True)  # never leave a half-written notebook
                raise
            return name
        raise ValueError(f"Couldn't find a free notebook name for {base}.")

    def notebook_export(self, name: str, cells: list[dict[str, Any]], format: str,
                        lakehouse_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """Writes the notebook as ``format`` (notebook_formats.FORMATS) to a new file in the
        Downloads folder and returns its path. The notebook itself isn't saved or changed."""
        if not isinstance(cells, list) or not all(isinstance(c, dict) for c in cells):
            raise ValueError("cells is a list of cells")
        text = nbf.export_text(format, name, cells, lakehouse_ref)
        folder = Path.home() / "Downloads"
        folder.mkdir(parents=True, exist_ok=True)
        ext = nbf.EXTENSIONS[format]
        base = file_stem(name, "Notebook") + (" with outputs" if format == "ipynb_outputs" else "")
        for n in range(1, 1000):
            path = folder / (f"{base}{ext}" if n == 1 else f"{base} ({n}){ext}")
            try:
                with open(path, "x", encoding="utf-8", newline="\n") as f:
                    f.write(text)
            except FileExistsError:
                continue
            return {"path": str(path), "format": format, "bytes": len(text.encode("utf-8")), "at": time.time(),
                    "notes": nbf.databricks_export_notes(cells, lakehouse_ref) if format == "databricks_py" else []}
        raise ValueError("Couldn't find a free file name in Downloads for the export.")
