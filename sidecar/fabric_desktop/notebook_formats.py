"""Notebook files in the formats Fabric, Databricks and Jupyter tools write, both ways.

Export (``export_text``): Jupyter .ipynb with or without the outputs shown on screen; .py source in
Fabric's Git format (``# Fabric notebook source``, ``# CELL ********************``) or Databricks'
(``# Databricks notebook source``, ``# COMMAND ----------``); and a static HTML page of the cells
and their outputs, every text escaped and no script in it.

Import (``parse``): .ipynb; Fabric and Databricks .py source; Databricks .sql and .scala source;
percent-format scripts (``# %%``, as VS Code and Jupytext write); and Databricks .dbc archives,
read in memory (``parse_dbc``) with limits on entries and sizes. Nothing here touches the disk:
notebook_formats_service writes the files.

Cells are the notebook screen's: ``{"type": "code"|"markdown", "source", "meta"}``. A cell in
another language starts with its Fabric cell magic (``%%sql``, ``%%spark`` for Scala, ``%%sparkr``),
which is how the notebook screen shows the cell's language.
"""

from __future__ import annotations

import html
import io
import json
import re
import zipfile
import zlib
from pathlib import PurePosixPath
from typing import Any

from .notebook import check_notebook_name, from_ipynb, to_ipynb

FORMATS = ("ipynb", "ipynb_outputs", "fabric_py", "databricks_py", "html")
EXTENSIONS = {"ipynb": ".ipynb", "ipynb_outputs": ".ipynb", "fabric_py": ".py", "databricks_py": ".py", "html": ".html"}

MAX_TEXT = 32 * 1024 * 1024      # a source file or .ipynb to import
MAX_DBC = 64 * 1024 * 1024       # a .dbc archive, as given
MAX_DBC_ENTRIES = 500            # files in the archive
MAX_DBC_ENTRY = 16 * 1024 * 1024  # one notebook, uncompressed
MAX_DBC_TOTAL = 128 * 1024 * 1024  # all of them, uncompressed
MAX_NOTEBOOKS = 200              # notebooks one import creates

FABRIC_HEADER = "# Fabric notebook source"
FABRIC_CELL = "# CELL ********************"
FABRIC_PARAMS = "# PARAMETERS CELL ********************"
FABRIC_MARKDOWN = "# MARKDOWN ********************"
FABRIC_METADATA = "# METADATA ********************"
DATABRICKS_HEADER = "Databricks notebook source"
DATABRICKS_COMMAND = "COMMAND ----------"

# Fabric cell magics and the Databricks language magics they match.
_TO_DATABRICKS = {"sql": "sql", "spark": "scala", "sparkr": "r", "pyspark": "python", "md": "md", "sh": "sh"}
_FROM_DATABRICKS = {"sql": "%%sql", "scala": "%%spark", "r": "%%sparkr", "sh": "%%sh"}
# Fabric's language name for a cell (its METADATA block) by cell magic.
_FABRIC_LANGUAGE = {"sql": "sparksql", "spark": "scala", "sparkr": "r", "html": "python"}
_FABRIC_MAGIC = {"sparksql": "%%sql", "scala": "%%spark", "r": "%%sparkr"}

_CELL_MAGIC = re.compile(r"^%%([A-Za-z_]+)[^\n]*(?:\n|$)")


def _magic(source: str) -> str | None:
    m = _CELL_MAGIC.match(source)
    return m.group(1).lower() if m else None


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _trim(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "\n".join(lines)


def _is_params(cell: dict[str, Any]) -> bool:
    return "parameters" in ((cell.get("meta") or {}).get("tags") or [])


# ---------- export ----------

def _meta_block(value: dict[str, Any]) -> list[str]:
    return [FABRIC_METADATA, ""] + [f"# META {line}" for line in json.dumps(value, indent=2).split("\n")]


def to_fabric_py(cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None = None) -> str:
    """The notebook as Fabric writes ``notebook-content.py`` in a Git-connected workspace."""
    meta: dict[str, Any] = {"kernel_info": {"name": "synapse_pyspark"}, "dependencies": {}}
    if lakehouse and lakehouse.get("lakehouse_id"):
        meta["dependencies"] = {"lakehouse": {
            "default_lakehouse": lakehouse["lakehouse_id"], "default_lakehouse_name": lakehouse.get("name", ""),
            "default_lakehouse_workspace_id": lakehouse.get("workspace_id", "")}}
    out = [FABRIC_HEADER, ""] + _meta_block(meta)
    for c in cells:
        source = c.get("source", "")
        out.append("")
        if c.get("type") == "markdown":
            out += [FABRIC_MARKDOWN, ""] + [f"# {line}".rstrip() for line in _lines(source)]
            continue
        magic = _magic(source)
        language = _FABRIC_LANGUAGE.get(magic or "", "python")
        out += [FABRIC_PARAMS if _is_params(c) else FABRIC_CELL, ""]
        out += [f"# MAGIC {line}".rstrip() for line in _lines(source)] if magic else _lines(source)
        out += [""] + _meta_block({"language": language, "language_group": "synapse_pyspark"})
    return "\n".join(out) + "\n"


def to_databricks_py(cells: list[dict[str, Any]]) -> str:
    """The notebook as Databricks exports a Python notebook's source file. Databricks has no
    parameters cell (it uses widgets), so that tag isn't carried."""
    parts = []
    for c in cells:
        source = c.get("source", "")
        if c.get("type") == "markdown":
            parts.append("\n".join(["# MAGIC %md"] + [f"# MAGIC {line}".rstrip() for line in _lines(source)]))
            continue
        magic = _magic(source)
        if magic:
            first, _, rest = source.partition("\n")
            head = f"%{_TO_DATABRICKS[magic]}" if magic in _TO_DATABRICKS else first
            body = [head] + (_lines(rest) if rest else [])
            parts.append("\n".join(f"# MAGIC {line}".rstrip() for line in body))
        else:
            parts.append(source)
    return f"# {DATABRICKS_HEADER}\n" + f"\n\n# {DATABRICKS_COMMAND}\n\n".join(parts) + "\n"


def _text_lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _table_plain(o: dict[str, Any]) -> str:
    rows = ["\t".join(str(c) for c in o.get("columns") or [])]
    rows += ["\t".join("" if v is None else str(v) for v in r) for r in o.get("rows") or []]
    return "\n".join(rows)


def _table_html(o: dict[str, Any]) -> str:
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in o.get("columns") or [])
    body = "".join("<tr>" + "".join(f"<td>{html.escape('' if v is None else str(v))}</td>" for v in r) + "</tr>"
                   for r in o.get("rows") or [])
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _nb_output(o: dict[str, Any]) -> dict[str, Any] | None:
    """A notebook-screen output as Jupyter stores it (nbformat 4)."""
    kind = o.get("type")
    if kind in ("stream", "text"):
        return {"output_type": "stream", "name": "stdout", "text": _text_lines(str(o.get("text", "")))}
    if kind == "error":
        tb = str(o.get("traceback") or "")
        return {"output_type": "error", "ename": str(o.get("ename", "")), "evalue": str(o.get("evalue", "")),
                "traceback": tb.split("\n") if tb else []}
    if kind == "table":
        return {"output_type": "display_data", "metadata": {},
                "data": {"text/plain": _text_lines(_table_plain(o)), "text/html": [_table_html(o)]}}
    if kind == "image" and o.get("mime") and o.get("data"):
        data = o["data"] if o["mime"] != "image/svg+xml" else _svg_text(o["data"])
        return {"output_type": "display_data", "metadata": {}, "data": {o["mime"]: data, "text/plain": ["<image>"]}}
    if kind == "html":
        return {"output_type": "display_data", "metadata": {}, "data": {"text/html": _text_lines(str(o.get("html", "")))}}
    if kind == "vegalite" and isinstance(o.get("spec"), dict):  # an Altair chart; Jupyter draws it from the spec
        return {"output_type": "display_data", "metadata": {},
                "data": {"application/vnd.vegalite.v6+json": o["spec"], "text/plain": ["<VegaLite 6 object>"]}}
    if kind == "plotly" and isinstance(o.get("figure"), dict):  # a plotly figure; Jupyter draws it from the JSON
        return {"output_type": "display_data", "metadata": {},
                "data": {"application/vnd.plotly.v1+json": o["figure"], "text/plain": ["<Plotly figure>"]}}
    return None


def _svg_text(data: str) -> str:
    import base64
    try:
        return base64.b64decode(data).decode("utf-8")
    except ValueError:
        return ""


def to_ipynb_text(cells: list[dict[str, Any]], outputs: bool, lakehouse: dict[str, Any] | None = None) -> str:
    """The notebook as .ipynb; with ``outputs``, each code cell's outputs and run count as well."""
    nb = to_ipynb(cells, lakehouse)
    if outputs:
        for cell, c in zip(nb["cells"], cells):
            if cell["cell_type"] != "code":
                continue
            cell["outputs"] = [x for x in (_nb_output(o) for o in c.get("outputs") or [] if isinstance(o, dict)) if x]
            if isinstance(c.get("count"), int):
                cell["execution_count"] = c["count"]
    return json.dumps(nb, indent=1) + "\n"


_IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/svg+xml")
_B64 = re.compile(r"^[A-Za-z0-9+/=\s]*$")

_PAGE_STYLE = """
body{font:14px/1.5 "Segoe UI",system-ui,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px;color:#1f1f1f;background:#fff}
h1{font-size:20px;margin:0 0 4px}.note{color:#616161;margin:0 0 20px}
.cell{border:1px solid #e0e0e0;border-radius:6px;margin:0 0 12px;overflow:hidden}
.label{font-size:12px;color:#616161;padding:4px 10px;background:#fafafa;border-bottom:1px solid #e0e0e0}
pre{margin:0;padding:10px;white-space:pre-wrap;word-break:break-word;font:13px/1.45 Consolas,monospace}
.src{background:#f7f7f7}.md{padding:10px;white-space:pre-wrap}
.out{border-top:1px dashed #e0e0e0}.err{color:#a4262c}.skipped{padding:8px 10px;color:#616161;font-style:italic}
table{border-collapse:collapse;margin:10px;font-size:13px}th,td{border:1px solid #e0e0e0;padding:3px 8px;text-align:left}
th{background:#fafafa}img{max-width:100%;margin:10px}
@media (prefers-color-scheme:dark){body{background:#1f1f1f;color:#eee}.cell,.label,th,td,.out{border-color:#444}
.label,th{background:#292929}.src{background:#262626}.note,.label,.skipped{color:#aaa}.err{color:#f1707b}}
"""


def _html_output(o: dict[str, Any]) -> str:
    kind = o.get("type")
    if kind in ("stream", "text"):
        return f'<pre class="out">{html.escape(str(o.get("text", "")))}</pre>'
    if kind == "error":
        text = f"{o.get('ename', '')}: {o.get('evalue', '')}"
        if o.get("traceback"):
            text += "\n\n" + str(o["traceback"])
        return f'<pre class="out err">{html.escape(text)}</pre>'
    if kind == "table":
        total = o.get("total_rows")
        shown = len(o.get("rows") or [])
        note = f'<div class="skipped">{shown} of {html.escape(str(total))} rows</div>' if total not in (None, shown) else ""
        return f'<div class="out">{_table_html(o)}{note}</div>'
    if kind == "image" and o.get("mime") in _IMAGE_TYPES and _B64.match(str(o.get("data", ""))):
        return f'<div class="out"><img alt="Chart from this cell" src="data:{o["mime"]};base64,{o["data"]}"></div>'
    if kind == "html":
        return '<div class="out skipped">HTML output isn\'t included in the page, so nothing in it can run.</div>'
    if kind == "vegalite":
        return '<div class="out skipped">Altair charts aren\'t included in the page, so nothing in it can run.</div>'
    if kind == "plotly":
        return '<div class="out skipped">plotly charts aren\'t included in the page, so nothing in it can run.</div>'
    return ""


def to_html(name: str, cells: list[dict[str, Any]]) -> str:
    """A page to read the notebook without KratosForge: the cells, then each one's outputs. Every
    text is escaped, HTML outputs are left out, and the page's policy allows no script at all."""
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">",
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>{html.escape(name)}</title><style>{_PAGE_STYLE}</style></head><body>",
           f"<h1>{html.escape(name)}</h1>", '<p class="note">Exported from KratosForge. Outputs are as they were on screen.</p>']
    n = 0
    for c in cells:
        source = html.escape(c.get("source", ""))
        if c.get("type") == "markdown":
            out.append(f'<div class="cell"><div class="label">Markdown</div><div class="md">{source}</div></div>')
            continue
        n += 1
        label = f"Cell {n}" + (f" [{c['count']}]" if isinstance(c.get("count"), int) else "") + (" (parameters)" if _is_params(c) else "")
        outs = "".join(_html_output(o) for o in c.get("outputs") or [] if isinstance(o, dict))
        out.append(f'<div class="cell"><div class="label">{html.escape(label)}</div><pre class="src">{source}</pre>{outs}</div>')
    out.append("</body></html>")
    return "\n".join(out) + "\n"


def export_text(fmt: str, name: str, cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None = None) -> str:
    if fmt not in FORMATS:
        raise ValueError(f"format is one of {', '.join(FORMATS)}")
    if fmt in ("ipynb", "ipynb_outputs"):
        return to_ipynb_text(cells, fmt == "ipynb_outputs", lakehouse)
    if fmt == "fabric_py":
        return to_fabric_py(cells, lakehouse)
    if fmt == "databricks_py":
        return to_databricks_py(cells)
    return to_html(name, cells)


# ---------- import ----------

def _code(source: str) -> dict[str, Any]:
    return {"type": "code", "source": source}


def _markdown(source: str) -> dict[str, Any]:
    return {"type": "markdown", "source": source}


def _unprefix(lines: list[str], prefix: str) -> list[str]:
    bare = prefix.rstrip()
    return [ln[len(prefix):] if ln.startswith(prefix) else "" if ln.rstrip() == bare else ln for ln in lines]


def _parse_fabric(lines: list[str]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    blocks: list[tuple[str, list[str]]] = []
    for ln in lines[1:]:
        s = ln.rstrip()
        if s in (FABRIC_CELL, FABRIC_PARAMS, FABRIC_MARKDOWN, FABRIC_METADATA):
            blocks.append((s, []))
        elif blocks:
            blocks[-1][1].append(ln)
    for kind, body in blocks:
        if kind == FABRIC_METADATA:
            if cells and cells[-1]["type"] == "code":
                meta = _fabric_meta(body)
                magic = _FABRIC_MAGIC.get(str(meta.get("language", "")))
                if magic and not cells[-1]["source"].startswith("%%"):
                    cells[-1]["source"] = f"{magic}\n{cells[-1]['source']}"
            continue
        if kind == FABRIC_MARKDOWN:
            cells.append(_markdown(_trim(_unprefix(body, "# "))))
            continue
        text = _trim(body)
        if all(ln.startswith("# MAGIC") for ln in _lines(text) if ln.strip()) and text:
            text = "\n".join(_unprefix(_lines(text), "# MAGIC "))
        cell = _code(text)
        if kind == FABRIC_PARAMS:
            cell["meta"] = {"tags": ["parameters"]}
        cells.append(cell)
    return cells


def _fabric_meta(body: list[str]) -> dict[str, Any]:
    text = "\n".join(_unprefix([ln for ln in body if ln.startswith("# META")], "# META "))
    try:
        value = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _databricks_cell(text: str, comment: str, default: str | None) -> dict[str, Any] | None:
    lines = [ln for ln in _lines(text) if not ln.startswith(f"{comment} DBTITLE")]
    text = _trim(lines)
    if not text:
        return None
    magic_prefix = f"{comment} MAGIC"
    if all(ln.startswith(magic_prefix) for ln in _lines(text) if ln.strip()):
        body = _unprefix(_lines(text), f"{magic_prefix} ")
        return _databricks_magic(body)
    return _code(f"{default}\n{text}" if default else text)


def _databricks_magic(body: list[str]) -> dict[str, Any]:
    """A Databricks cell whose first line is a magic: %md, %sql, %scala, %r, %python, %run, %pip..."""
    first = body[0].strip() if body else ""
    m = re.match(r"^%([A-Za-z_-]+)\s*(.*)$", first)
    if not m:
        return _code("\n".join(body))
    name, rest = m.group(1).lower(), m.group(2)
    tail = ([rest] if rest else []) + body[1:]
    if name in ("md", "md-sandbox"):
        return _markdown(_trim(tail))
    if name == "python":
        return _code(_trim(tail))
    if name in _FROM_DATABRICKS:
        return _code(_FROM_DATABRICKS[name] + "\n" + _trim(tail))
    return _code("\n".join(body))  # %run, %pip, %fs...: line magics, kept as they are


def _parse_databricks(lines: list[str], comment: str, default: str | None) -> list[dict[str, Any]]:
    sep = f"{comment} {DATABRICKS_COMMAND}"
    chunks: list[list[str]] = [[]]
    for ln in lines[1:]:
        if ln.rstrip() == sep:
            chunks.append([])
        else:
            chunks[-1].append(ln)
    return [c for c in (_databricks_cell("\n".join(ch), comment, default) for ch in chunks) if c]


_PERCENT = re.compile(r"^# %%(?!%)(.*)$")


def _parse_percent(lines: list[str]) -> list[dict[str, Any]]:
    # A Jupytext header (# --- ... # ---) at the top is its own settings, not a cell.
    if lines and lines[0].rstrip() == "# ---":
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "# ---":
                lines = lines[i + 1:]
                break
    chunks: list[tuple[str, list[str]]] = [("", [])]
    for ln in lines:
        m = _PERCENT.match(ln.rstrip())
        if m:
            chunks.append((m.group(1).strip(), []))
        else:
            chunks[-1][1].append(ln)
    cells = []
    for head, body in chunks:
        if re.search(r"\[(markdown|md)\]", head):
            text = _trim(body)
            # Jupytext writes markdown as # lines; a triple-quoted block is the other spelling.
            m = re.fullmatch(r'(?s)("""|\'\'\')\n?(.*?)\n?\1', text)
            cells.append(_markdown(m.group(2) if m else _trim(_unprefix(_lines(text), "# "))))
        else:
            text = _trim(body)
            if text:
                cell = _code(text)
                if "parameters" in head:
                    cell["meta"] = {"tags": ["parameters"]}
                cells.append(cell)
    return [c for c in cells if c["source"] or c["type"] == "code"]


def parse_source(text: str, file_name: str = "") -> dict[str, Any]:
    """Cells from a notebook source file: {"format", "cells"}. Fabric and Databricks files are
    known by their first line, percent-format scripts by a ``# %%`` line; any other script comes
    in as one code cell."""
    if len(text) > MAX_TEXT:
        raise ValueError("The file is larger than 32 MB, so it wasn't read.")
    text = text.lstrip("﻿")
    lines = _lines(text)
    first = lines[0].strip() if lines else ""
    ext = PurePosixPath(file_name.replace("\\", "/")).suffix.lower()
    if first == FABRIC_HEADER:
        return {"format": "Fabric notebook source", "cells": _parse_fabric(lines)}
    for comment, default, label in (("#", None, "Databricks notebook source"), ("--", "%%sql", "Databricks SQL notebook source"),
                                    ("//", "%%spark", "Databricks Scala notebook source")):
        if first == f"{comment} {DATABRICKS_HEADER}":
            return {"format": label, "cells": _parse_databricks(lines, comment, default)}
    if any(_PERCENT.match(ln.rstrip()) for ln in lines):
        return {"format": "Percent-format script (# %%)", "cells": _parse_percent(lines)}
    source = _trim(lines)
    if ext == ".sql" and source:
        source = f"%%sql\n{source}"
    return {"format": "Script (one cell)", "cells": [_code(source)] if source else []}


def parse_ipynb(text: str) -> dict[str, Any]:
    if len(text) > MAX_TEXT:
        raise ValueError("The file is larger than 32 MB, so it wasn't read.")
    try:
        nb = json.loads(text)
    except (ValueError, RecursionError):  # RecursionError: nested too deep to be a notebook
        raise ValueError("This .ipynb file isn't valid JSON, so it can't be imported.") from None
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise ValueError("This .ipynb file has no cells list, so it isn't a Jupyter notebook.")
    return {"format": "Jupyter notebook (.ipynb)", "cells": from_ipynb(nb)["cells"]}


def _safe_entry(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return False
    return ".." not in PurePosixPath(name).parts


_DBC_LANGUAGE = {"python": None, "sql": "%%sql", "scala": "%%spark", "r": "%%sparkr"}


def _dbc_notebook(doc: dict[str, Any], entry: str) -> dict[str, Any] | None:
    commands = doc.get("commands")
    if not isinstance(commands, list):
        return None
    default = _DBC_LANGUAGE.get(str(doc.get("language", "python")).lower())
    ordered = sorted((c for c in commands if isinstance(c, dict) and isinstance(c.get("command"), str)),
                     key=lambda c: c.get("position") if isinstance(c.get("position"), (int, float)) else 0)
    cells = []
    for c in ordered:
        text = _trim(_lines(c["command"]))
        if not text:
            continue
        cells.append(_databricks_magic(_lines(text)) if text.startswith("%") else _code(f"{default}\n{text}" if default else text))
    name = doc.get("name") if isinstance(doc.get("name"), str) and doc["name"].strip() else PurePosixPath(entry).stem
    return {"name": str(name), "folder": str(PurePosixPath(entry).parent).strip("."), "cells": cells}


def parse_dbc(data: bytes) -> list[dict[str, Any]]:
    """The notebooks in a Databricks archive (.dbc: a zip of one JSON document per notebook), read
    in memory. Folders and other files in it are skipped; paths that climb out of the archive, an
    encrypted entry, or more files or bytes than the limits stop the import."""
    if len(data) > MAX_DBC:
        raise ValueError("The archive is larger than 64 MB, so it wasn't read.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("This .dbc file isn't a Databricks archive (it isn't a zip file).") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_DBC_ENTRIES:
            raise ValueError(f"The archive has {len(infos)} files; at most {MAX_DBC_ENTRIES} can be imported at once.")
        total, out = 0, []
        for info in infos:
            if info.is_dir():
                continue
            if not _safe_entry(info.orig_filename):  # the raw name: Windows reads "\\" as "/"
                raise ValueError(f"The archive has a file with an unsafe path ({info.filename[:120]!r}), so it wasn't imported.")
            if info.flag_bits & 0x1:
                raise ValueError("The archive is encrypted, so it can't be read.")
            if PurePosixPath(info.filename).suffix.lower() not in (".python", ".sql", ".scala", ".r", ".json", ""):
                continue
            if info.file_size > MAX_DBC_ENTRY:
                raise ValueError(f"{info.filename[:120]} is larger than 16 MB unpacked, so the archive wasn't imported.")
            if total + info.file_size > MAX_DBC_TOTAL:
                raise ValueError("The archive unpacks to more than 128 MB, so it wasn't imported.")
            try:
                with zf.open(info) as f:
                    raw = f.read(MAX_DBC_ENTRY + 1)  # the size in the header can lie
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError):
                raise ValueError(f"{info.filename[:120]} in the archive is damaged, so the archive wasn't imported.") from None
            if len(raw) > MAX_DBC_ENTRY:
                raise ValueError(f"{info.filename[:120]} is larger than 16 MB unpacked, so the archive wasn't imported.")
            total += len(raw)  # what was really read, not what the header says
            if total > MAX_DBC_TOTAL:
                raise ValueError("The archive unpacks to more than 128 MB, so it wasn't imported.")
            try:
                doc = json.loads(raw.decode("utf-8-sig"))
            except ValueError:
                continue  # not a notebook document (the archive's manifest, for one)
            nb = _dbc_notebook(doc, info.filename) if isinstance(doc, dict) else None
            if nb:
                out.append(nb)
    if not out:
        raise ValueError("The archive has no notebooks in it.")
    if len(out) > MAX_NOTEBOOKS:
        raise ValueError(f"The archive has {len(out)} notebooks; at most {MAX_NOTEBOOKS} can be imported at once.")
    return out


def notebook_name(name: str) -> str:
    """A notebook name from a file or archive name: characters a file name can't hold become -,
    and an empty result is "Imported notebook"."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip().rstrip(".").strip()[:120]
    if not name:
        return "Imported notebook"
    return check_notebook_name(name)
