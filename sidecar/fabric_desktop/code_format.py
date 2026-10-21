"""Format notebook cells, as Fabric and Databricks notebooks do (Format cell, Format notebook).

Python goes through ruff's formatter (Black's style, 88 columns). ruff is a program of its own that
the engine runs with ``--isolated``, so no ``pyproject.toml`` or ``ruff.toml`` near the engine
changes the result. Each cell goes to ruff as a one-cell notebook: ruff then keeps line magics
(``%time``, ``%run``, ``!pip``) as they are, and a syntax error in one cell doesn't stop the others.
A ``%%sql`` cell's SQL goes through the SQL page's formatter (sql_format.py) in Spark SQL, which hands
the original back when it can't show the result means the same. Other languages (``%%html``,
``%md``, ``%sh``...) are left as they are. Formatting never runs the code.
"""

from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import sql_format

# A first line that makes the cell another language. %%sql and a bare %sql are formatted as SQL.
_SQL = re.compile(r"^\s*(%%sql\b[^\n]*|%sql[ \t]*)(\n|$)")
# A Python cell that names its language (Fabric's %%pyspark, Databricks' %python): the code under it is
# formatted and the magic line stays.
_PY = re.compile(r"^\s*(%%pyspark|%%python|%python)[ \t]*(\n|$)")
_OTHER = re.compile(r"^\s*(%%(?!sql\b)\w+|%(md|sh|fs|scala|r|sql|python)\b)")
# ruff: "error: Failed to parse cell.ipynb:cell 1:2:8: Expected ..." (the cell part varies by version).
_PARSE = re.compile(r"(\d+):(\d+): (.+)$")
_TIMEOUT = 20


def _ruff() -> str:
    from ruff import find_ruff_bin  # the engine ships ruff (pyproject.toml); its program sits beside the package
    return find_ruff_bin()


def _python(source: str, ruff: str) -> dict[str, Any]:
    nb = {"cells": [{"cell_type": "code", "source": source, "metadata": {}, "outputs": [], "execution_count": None}],
          "metadata": {"language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        res = subprocess.run([ruff, "format", "--isolated", "--stdin-filename", "cell.ipynb", "-"],
                             input=json.dumps(nb), capture_output=True, text=True, encoding="utf-8",
                             timeout=_TIMEOUT, creationflags=flags)
    except subprocess.TimeoutExpired:
        return {"source": source, "changed": False, "error": "Formatting took too long, so the cell was left as it was."}
    if res.returncode != 0:
        first = (res.stderr.strip().splitlines() or ["ruff failed"])[0]
        m = _PARSE.search(first)
        why = f"line {m.group(1)}: {m.group(3)}" if m and "parse" in first.lower() else first.removeprefix("error: ")
        return {"source": source, "changed": False, "error": f"Not formatted, because Python can't read it ({why})."}
    out = "".join(json.loads(res.stdout)["cells"][0]["source"])
    return {"source": out, "changed": out != source, "error": ""}


def _sql(source: str, magic: re.Match[str]) -> dict[str, Any]:
    head, body = source[:magic.end(1)], source[magic.end():]
    if not body.strip():
        return {"source": source, "changed": False, "error": ""}
    out = sql_format.format_sql(body, "spark")
    if out["warning"]:
        return {"source": source, "changed": False, "error": out["warning"]}
    text = f"{head}\n{out['sql'].rstrip()}"
    return {"source": text, "changed": text != source, "error": ""}


def format_cells(sources: list[str]) -> list[dict[str, Any]]:
    """Each cell's formatted source, ``changed``, and ``error`` (why it was left as it was, or "")."""
    ruff: str | None = None
    try:
        ruff = _ruff()
    except (ImportError, FileNotFoundError):  # ruff.RuffNotFound is a FileNotFoundError
        pass

    def one(source: str) -> dict[str, Any]:
        # A notebook saved on Windows can hold \r\n line ends; ruff would leave a stray \r behind.
        source = str(source).replace("\r\n", "\n")
        if (m := _PY.match(source)):
            head, body = source[:m.end(1)], source[m.end():]
            if not body.strip() or ruff is None:
                return {"source": source, "changed": False,
                        "error": "" if not body.strip() else "The code formatter (ruff) isn't installed in this engine."}
            out = _python(body, ruff)
            text = f"{head}\n{out['source']}" if out["changed"] else source
            # Line numbers count from the cell's first line, the magic line included.
            why = re.sub(r"\(line (\d+):", lambda n: f"(line {int(n.group(1)) + 1}:", out["error"])
            return {"source": text, "changed": text != source, "error": why}
        if not source.strip() or _OTHER.match(source) and not _SQL.match(source):
            return {"source": source, "changed": False, "error": ""}
        if (m := _SQL.match(source)):
            return _sql(source, m)
        if ruff is None:
            return {"source": source, "changed": False, "error": "The code formatter (ruff) isn't installed in this engine."}
        return _python(source, ruff)

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one, sources))
