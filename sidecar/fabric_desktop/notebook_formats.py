"""Notebook files in the formats Fabric, Databricks and Jupyter tools write, both ways.

Export (``export_text``): Jupyter .ipynb with or without the outputs shown on screen; .py source in
Fabric's Git format (``# Fabric notebook source``, ``# CELL ********************``) or Databricks'
(``# Databricks notebook source``, ``# COMMAND ----------``); and a static HTML page of the cells
and their outputs, every text escaped and no script in it.

Import (``parse``): .ipynb; Fabric and Databricks .py source; Databricks .sql and .scala source;
percent-format scripts (``# %%``, as VS Code and Jupytext write); and Databricks .dbc archives,
read in memory (``parse_dbc``) with limits on entries and sizes. Nothing here touches the disk:
notebook_formats_service writes the files.

Databricks imports and exports also say what didn't carry over (``databricks_export_notes``,
and the ``notes`` of each imported notebook): one entry per dropped or changed construct, with its
cell, so nothing is lost without the person being told.

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


def _magic_first(source: str) -> str:
    """``source`` with its cell magic on the first line: the blank lines before a cell magic, and the
    indent of its line, are dropped, as Jupyter runs it. Any other cell is returned as it is."""
    rest = source.replace("\r\n", "\n").replace("\r", "\n").lstrip()
    return rest if _magic(rest) else source


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


def _prefixed(prefix: str, lines: list[str]) -> list[str]:
    """Each line behind ``prefix``. A blank line gets the bare prefix; any other line keeps its
    trailing spaces, which are part of the text (two at the end of a markdown line break it)."""
    return [f"{prefix} {line}" if line else prefix for line in lines]


_FABRIC_MARKERS = (FABRIC_CELL, FABRIC_PARAMS, FABRIC_MARKDOWN, FABRIC_METADATA)


def _misread(lines: list[str], markers: tuple[str, ...], titles: bool = False) -> bool:
    """Whether a Python cell written as it is would read back as something else: a line that is a
    cell marker would split it, and a cell of only ``# MAGIC`` lines would lose that prefix."""
    if any(ln.rstrip() in markers or (titles and ln.startswith("# DBTITLE")) for ln in lines):
        return True
    return any(ln.strip() for ln in lines) and all(ln.startswith("# MAGIC") for ln in lines if ln.strip())


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
            out += [FABRIC_MARKDOWN, ""] + _prefixed("#", _lines(source))
            continue
        source = _magic_first(source)
        magic = _magic(source)
        language = _FABRIC_LANGUAGE.get(magic or "", "python")
        out += [FABRIC_PARAMS if _is_params(c) else FABRIC_CELL, ""]
        lines = _lines(source)
        # A Python cell that would read back wrong is written as # MAGIC lines too, as a magic
        # cell is; its METADATA still says Python, so it reads back as the same code.
        out += _prefixed("# MAGIC", lines) if magic or _misread(lines, _FABRIC_MARKERS) else lines
        out += [""] + _meta_block({"language": language, "language_group": "synapse_pyspark"})
    return "\n".join(out) + "\n"


def to_databricks_py(cells: list[dict[str, Any]]) -> str:
    """The notebook as Databricks exports a Python notebook's source file. Databricks has no
    parameters cell (it uses widgets), so that tag isn't carried."""
    parts = []
    for c in cells:
        source = c.get("source", "")
        if c.get("type") == "markdown":
            parts.append("\n".join(_prefixed("# MAGIC", ["%md"] + _lines(source))))
            continue
        source = _magic_first(source)
        magic = _magic(source)
        lines = _lines(source)
        if magic:
            head = f"%{_TO_DATABRICKS[magic]}" if magic in _TO_DATABRICKS else lines[0]
            parts.append("\n".join(_prefixed("# MAGIC", [head] + lines[1:])))
        elif source.lstrip().startswith("%"):
            # A line magic cell (%run, %fs, %pip): Databricks keeps it as # MAGIC lines, never bare,
            # since a bare %line isn't Python.
            parts.append("\n".join(_prefixed("# MAGIC", lines)))
        elif not source.strip() or _misread(lines, (f"# {DATABRICKS_COMMAND}",), titles=True):
            # An empty cell, or Python that would read back wrong (a COMMAND line splits it, a
            # DBTITLE line is dropped): written as a %python cell, which Databricks reads as the same code.
            parts.append("\n".join(_prefixed("# MAGIC", ["%python"] + lines)))
        else:
            parts.append(source)
    return f"# {DATABRICKS_HEADER}\n" + f"\n\n# {DATABRICKS_COMMAND}\n\n".join(parts) + "\n"


# ---------- what a Databricks conversion doesn't carry over ----------
#
# A note is {"cell": 1-based cell number or None for the whole notebook, "kind", "what"}:
#   dropped  not in the converted notebook at all
#   changed  carried over in another form
#   check    carried over as written, but won't work the same where it now runs
#   fabric   runs on this PC as written, but needs a change before it runs in Fabric

def _note(cell: int | None, kind: str, what: str) -> dict[str, Any]:
    return {"cell": cell, "kind": kind, "what": what}


# Cell magics Databricks has (or that export maps to one), and IPython's that only wrap Python.
_DATABRICKS_KNOWN = set(_TO_DATABRICKS) | {"python"}
_FABRIC_UTILS = re.compile(r"\b(?:notebookutils|mssparkutils)\.")


def databricks_export_notes(cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """What exporting ``cells`` as Databricks source (``to_databricks_py``) leaves out or changes."""
    notes: list[dict[str, Any]] = []
    if lakehouse and lakehouse.get("lakehouse_id"):
        notes.append(_note(None, "dropped", "The attached lakehouse: Databricks source has no default lakehouse. "
                                            "Read tables by their full name, or attach storage in Databricks."))
    if any(c.get("type") == "code" and c.get("outputs") for c in cells):
        notes.append(_note(None, "dropped", "Outputs: Databricks source holds code only. Run the notebook there to get them."))
    for n, c in enumerate(cells, 1):
        meta = c.get("meta") or {}
        if _is_params(c):
            notes.append(_note(n, "dropped", "The parameter cell setting: Databricks uses widgets instead, such as "
                                             "dbutils.widgets.text(\"run_date\", \"\") and dbutils.widgets.get(\"run_date\")."))
        hidden = [label for key, label in (("source_hidden", "hidden code"), ("outputs_hidden", "hidden output"),
                                           ("frozen", "frozen")) if meta.get(key)]
        if hidden:
            notes.append(_note(n, "dropped", f"The cell's {' and '.join(hidden)} setting: Databricks source doesn't keep it."))
        if c.get("type") != "code":
            continue
        source = _magic_first(c.get("source", ""))
        magic = _magic(source)
        if magic and magic not in _DATABRICKS_KNOWN:
            notes.append(_note(n, "check", f"%%{magic} has no Databricks equivalent. The cell is kept as written, "
                                           "so it won't run there until it's rewritten."))
        elif magic == "md":
            notes.append(_note(n, "changed", "%%md became a %md cell, which Databricks shows as markdown, so it "
                                             "comes back from Databricks as a markdown cell."))
        elif magic in _TO_DATABRICKS and magic != "pyspark":
            notes.append(_note(n, "changed", f"%%{magic} became %{_TO_DATABRICKS[magic]}, Databricks' way to mark the language."))
        options = _lines(source)[0][len(magic or "") + 2:].strip()
        if magic in _TO_DATABRICKS and options:
            notes.append(_note(n, "dropped", f"The options on the %%{magic} line ({options}): the export writes "
                                             f"%{_TO_DATABRICKS[magic]} on its own."))
        rest = [ln for ln in _lines(source.lstrip())[1:] if ln.strip() and not ln.lstrip().startswith("%")]
        if not magic and source.lstrip().startswith("%") and rest:
            first = source.lstrip().split(None, 1)[0]
            notes.append(_note(n, "check", f"In Databricks a cell that starts with {first} is all magic, so the Python "
                                           "after it won't run there. Move that code into a cell of its own."))
        if _FABRIC_UTILS.search(source):
            notes.append(_note(n, "check", "notebookutils (mssparkutils) is Fabric's. Databricks has dbutils, with "
                                           "different names for some calls (dbutils.fs, dbutils.notebook, dbutils.widgets)."))
    return notes


# Calls and paths in an imported Databricks cell that reach what only a Databricks workspace has.
_HERE_CHECKS = (
    (re.compile(r"\bdbutils\.fs\.(?:mount|unmount|mounts|refreshMounts|updateMount)\b"),
     "dbutils.fs mounts are Databricks storage; there are no mounts on this PC."),
    (re.compile(r"\bdbutils\.library\.install"), "dbutils.library.install runs in Databricks; this PC's packages are fixed."),
    (re.compile(r"\bdbutils\.credentials\."), "dbutils.credentials is a Databricks on AWS feature; this PC signs in to Microsoft as you."),
    (re.compile(r"(?i)(?:dbfs:/|[\"'\s(]/dbfs/|[\"'\s(]/Volumes/|[\"'\s(]/Workspace/)"),
     "A Databricks storage path (dbfs:/, /dbfs/, /Volumes/ or /Workspace/), which neither this PC nor Fabric "
     "can reach. Point it at Files/ or Tables/ in a lakehouse, or at a folder on this PC."),
    # Delta Live Tables (Lakeflow declarative pipelines): Python decorators and SQL statements.
    (re.compile(r"(?m)^\s*(?:import\s+dlt\b|from\s+dlt\s+import\b|@dlt\.)|(?i:\bCREATE\s+(?:OR\s+REFRESH\s+)?"
                r"(?:STREAMING|LIVE)\s+(?:TABLE|LIVE\s+TABLE|VIEW)\b|\bCREATE\s+OR\s+REFRESH\b|"
                r"\b(?:APPLY\s+CHANGES|AUTO\s+CDC)\s+INTO\b|\bCONSTRAINT\s+\w+\s+EXPECT\s*\()"),
     "Delta Live Tables code, which only a Databricks pipeline runs: neither this PC nor Fabric has it. "
     "Rewrite each dataset as a cell that writes its table, check the data in code instead of EXPECT, "
     "and run the cells in order from a pipeline."),
    (re.compile(r"(?m)^\s*%run\s+(?:\.{1,2}/|/)"),
     "%run with a workspace path: on this PC it runs the notebook of that name from the notebooks folder, so import it too."),
)


# Databricks code that runs on this PC but needs a change before it runs in Fabric. Based on the
# migration gotchas in microsoft/skills-for-fabric (MIT), written in our own words:
# https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/databricks-migration/resources/migration-gotchas.md
# Not checked, because the source doesn't show them: Unity Catalog names (one schema-enabled
# lakehouse per catalog keeps schema.table as it was), Photon tuning, cluster init scripts, and
# Repos versus Fabric Git integration.
_FABRIC_CHECKS = (
    (re.compile(r"\bdbutils\.widgets\."),
     "dbutils.widgets runs on this PC, but Fabric has no widgets. Before running it in Fabric, put the "
     "defaults in a parameter cell; a pipeline or notebookutils.notebook.run passes the values."),
    (re.compile(r"\bdbutils\.library\.restartPython\b"),
     "In Fabric the call is notebookutils.session.restartPython(). For scheduled runs, add the packages "
     "to the Fabric environment rather than installing them in the notebook."),
    # spark.databricks.delta.* settings are Delta Lake's own and Fabric's Delta reads them; the rest
    # are Databricks Runtime settings.
    (re.compile(r"""["']spark\.databricks\.(?!delta\.)\w[\w.]*["']"""),
     "A Databricks Runtime setting (spark.databricks., other than Delta's), which Fabric doesn't have: "
     "setting it does nothing there, and reading it fails unless the call gives a default."),
    (re.compile(r"\.trigger\(\s*(?:processingTime|continuous)\s*="),
     "A stream that keeps running. In Fabric it uses capacity for as long as it runs, and continuous "
     "triggers aren't supported there. The usual Fabric pattern is trigger(availableNow=True) run on a "
     "schedule. On this PC it already runs once, as availableNow."),
)


def _here_notes(cells: list[dict[str, Any]], notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for n, c in enumerate(cells, 1):
        if c.get("type") == "code":
            source = c.get("source", "")
            notes += [_note(n, "check", what) for pattern, what in _HERE_CHECKS if pattern.search(source)]
            notes += [_note(n, "fabric", what) for pattern, what in _FABRIC_CHECKS if pattern.search(source)]
    return sorted(notes, key=lambda x: (x["cell"] or 0))


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
    if kind in ("stream", "text", "translation"):  # translation: the Snowflake SQL report, as text
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
    if kind in ("stream", "text", "translation"):
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


def _databricks_cell(text: str, comment: str, default: str | None,
                     notes: list[dict[str, Any]] | None = None, n: int = 0) -> dict[str, Any] | None:
    titles = [ln for ln in _lines(text) if ln.startswith(f"{comment} DBTITLE")]
    text = _trim([ln for ln in _lines(text) if not ln.startswith(f"{comment} DBTITLE")])
    if not text:
        return None
    if notes is not None and titles:
        title = titles[0].split(",", 1)[1].strip() if "," in titles[0] else ""
        notes.append(_note(n, "dropped", f"The cell title{f' {title!r}' if title else ''}: KratosForge cells have no titles."))
    magic_prefix = f"{comment} MAGIC"
    if all(ln.startswith(magic_prefix) for ln in _lines(text) if ln.strip()):
        body = _unprefix(_lines(text), f"{magic_prefix} ")
        return _databricks_magic(body, notes, n)
    return _code(f"{default}\n{text}" if default else text)


def _databricks_magic(body: list[str], notes: list[dict[str, Any]] | None = None, n: int = 0) -> dict[str, Any]:
    """A Databricks cell whose first line is a magic: %md, %sql, %scala, %r, %python, %run, %pip..."""
    first = body[0].strip() if body else ""
    m = re.match(r"^%([A-Za-z_-]+)\s*(.*)$", first)
    if not m:
        return _code("\n".join(body))
    name, rest = m.group(1).lower(), m.group(2)
    tail = ([rest] if rest else []) + body[1:]
    if name in ("md", "md-sandbox"):
        if notes is not None and name == "md-sandbox":
            notes.append(_note(n, "changed", "%md-sandbox became a markdown cell; its HTML and styles may show differently."))
        return _markdown(_trim(tail))
    if name == "python":
        return _code(_trim(tail))
    if name in _FROM_DATABRICKS:
        if notes is not None:
            notes.append(_note(n, "changed", f"%{name} became {_FROM_DATABRICKS[name]}, KratosForge's way to mark the language."))
        return _code(_FROM_DATABRICKS[name] + "\n" + _trim(tail))
    return _code("\n".join(body))  # %run, %pip, %fs...: line magics, kept as they are


def _parse_databricks(lines: list[str], comment: str, default: str | None,
                      notes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    sep = f"{comment} {DATABRICKS_COMMAND}"
    chunks: list[list[str]] = [[]]
    for ln in lines[1:]:
        if ln.rstrip() == sep:
            chunks.append([])
        else:
            chunks[-1].append(ln)
    cells: list[dict[str, Any]] = []
    for ch in chunks:
        cell = _databricks_cell("\n".join(ch), comment, default, notes, len(cells) + 1)
        if cell:
            cells.append(cell)
    return cells


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
            notes: list[dict[str, Any]] = []
            cells = _parse_databricks(lines, comment, default, notes)
            return {"format": label, "cells": cells, "notes": _here_notes(cells, notes)}
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
    cells: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for c in ordered:
        text = _trim(_lines(c["command"]))
        if not text:
            continue
        n = len(cells) + 1
        cells.append(_databricks_magic(_lines(text), notes, n) if text.startswith("%")
                     else _code(f"{default}\n{text}" if default else text))
        title = c.get("commandTitle")
        if isinstance(title, str) and title.strip():
            notes.append(_note(n, "dropped", f"The cell title {title.strip()!r}: KratosForge cells have no titles."))
        if c.get("results"):
            notes.append(_note(n, "dropped", "The cell's saved results: imported notebooks start without outputs. Run the cell to get them."))
        hidden = [label for key, label in (("hideCommandCode", "hidden code"), ("hideCommandResult", "hidden result"))
                  if c.get(key) is True]
        if hidden:
            notes.append(_note(n, "dropped", f"The cell's {' and '.join(hidden)} setting."))
    if doc.get("dashboards"):
        notes.append(_note(None, "dropped", "The notebook's dashboards: KratosForge has no notebook dashboards."))
    name = doc.get("name") if isinstance(doc.get("name"), str) and doc["name"].strip() else PurePosixPath(entry).stem
    return {"name": str(name), "folder": str(PurePosixPath(entry).parent).strip("."), "cells": cells,
            "notes": _here_notes(cells, notes)}


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
