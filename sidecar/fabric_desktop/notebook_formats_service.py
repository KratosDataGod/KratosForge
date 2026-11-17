"""Notebook export and import (notebook_formats.py has the formats).

``notebook_export`` writes one new file in the Downloads folder and never replaces one: a name that
is taken gets a number. ``notebook_formats_import_check`` reads a file the person picked (its content is
sent by the app; the file itself is never opened or changed here) and says what importing would
create. ``notebook_formats_import`` then writes one new notebook per notebook found, into the app's own
notebooks folder, each under a name no notebook has yet: the name is reserved by creating the
file (open "xb"), so an existing notebook is never replaced.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import b64
from . import notebook_formats as nbf
from .filenames import file_stem
from .notebook import to_ipynb
from . import known_folders

METHODS = {"notebook_export", "notebook_print_page", "notebook_formats_import_check", "notebook_formats_import",
           "notebook_report_open", "notebook_report_page", "notebook_report_export"}
REPORT_MAX_BYTES = 64 * 1024 * 1024


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
            data = b64.decode_exact(content)
        except ValueError:
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
            for name, nb in zip(names, read["notebooks"], strict=True)]}

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

    def notebook_print_page(self, name: str, cells: list[dict[str, Any]]) -> dict[str, Any]:
        """The web page export (notebook_formats.to_html) as text, for the app to print, so the person can
        save it as a PDF with the system's print dialog. Nothing is written."""
        if not isinstance(cells, list) or not all(isinstance(c, dict) for c in cells):
            raise ValueError("cells is a list of cells")
        return {"html": nbf.to_html(name, cells)}

    # The read-only report (Notebook > Share > Open as report, Preview): the app sends the notebook's
    # cells and outputs as on screen, the engine keeps that one report in memory, and the report's own
    # window reads it. Nothing is written, until Export as HTML writes the same page to Downloads.
    def notebook_report_open(self, name: str, cells: list[dict[str, Any]], ran: str, stale: bool = False) -> dict[str, Any]:
        """Keep the report the report window shows: the page (to_html in report form) and whether the
        notebook changed after the run its outputs are from. Replaces the report kept before."""
        if not isinstance(cells, list) or not all(isinstance(c, dict) for c in cells):
            raise ValueError("cells is a list of cells")
        if not isinstance(ran, str) or len(ran) > 200:
            raise ValueError("ran is a short line saying which run the outputs are from")
        markdown = sum(1 for c in cells if c.get("type") == "markdown")
        count = (f"1 cell{', Markdown' if markdown else ''}" if len(cells) == 1
                 else f"{len(cells)} cells, {markdown} of them Markdown")
        line = f"{ran} Code is hidden. {count}."
        page = nbf.to_html(name, cells, report=line)
        if len(page.encode("utf-8")) > REPORT_MAX_BYTES:
            raise ValueError("The notebook's outputs are larger than 64 MB, so the report wasn't made. Clear the largest outputs and try again.")
        self._notebook_report = {"name": name, "html": page, "stale": bool(stale), "at": time.time()}
        return {"name": name, "bytes": len(page.encode("utf-8"))}

    def notebook_report_page(self) -> dict[str, Any]:
        """The report kept by notebook_report_open: {"name", "html", "stale", "at"}, or {} when there's none."""
        return dict(getattr(self, "_notebook_report", None) or {})

    def notebook_report_export(self) -> dict[str, Any]:
        """Write the report's page, as the report window shows it, to a new .html file in Downloads."""
        report = getattr(self, "_notebook_report", None)
        if not report:
            raise ValueError("There's no report to export. Open it again from the notebook (Share > Open as report).")
        folder = known_folders.downloads()
        folder.mkdir(parents=True, exist_ok=True)
        base = f"{file_stem(report['name'], 'Notebook')} report"
        for n in range(1, 1000):
            path = folder / (f"{base}.html" if n == 1 else f"{base} ({n}).html")
            try:
                with open(path, "x", encoding="utf-8", newline="\n") as f:
                    f.write(report["html"])
            except FileExistsError:
                continue
            return {"path": str(path)}
        raise ValueError("Couldn't find a free file name in Downloads for the report.")

    def notebook_export(self, name: str, cells: list[dict[str, Any]], format: str,
                        lakehouse_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """Writes the notebook as ``format`` (notebook_formats.FORMATS) to a new file in the
        Downloads folder and returns its path. The notebook itself isn't saved or changed."""
        if not isinstance(cells, list) or not all(isinstance(c, dict) for c in cells):
            raise ValueError("cells is a list of cells")
        text = nbf.export_text(format, name, cells, lakehouse_ref)
        folder = known_folders.downloads()
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
