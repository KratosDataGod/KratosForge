"""Line magics in notebook cells: how the kernel runs them and how reading tools see them.

A line magic is ``%name rest`` on a line of its own, or ``target = %name rest`` to keep what it
returns (``df = %sql SELECT 1``), as IPython, Fabric and Databricks notebooks allow. The kernel
turns each into a call it answers (``rewrite``); dependencies, the environment check and other
tools that read code instead of running it see the line blanked, with the assignment kept so the
name still counts as defined (``blank``). ``!command`` lines are left to the shell support.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# "%name rest", optionally after "target =" or "a, b =". "%%name" is a cell magic, not this.
_LINE = re.compile(r"^(?P<indent>\s*)(?:(?P<target>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*=\s*)?"
                   r"%(?!%)(?P<name>\w+)\s?(?P<rest>.*)$")
CALL = "__fd_magic__"


class Magic(NamedTuple):
    line: int  # 1-based
    name: str
    rest: str
    target: str | None


def find(code: str) -> list[Magic]:
    """The line magics in a cell's code, in order."""
    found = []
    for i, text in enumerate(code.split("\n"), 1):
        if m := _LINE.match(text):
            found.append(Magic(i, m["name"], m["rest"], m["target"]))
    return found


def rewrite(code: str) -> str:
    """The cell's code with each line magic replaced by a call the kernel answers."""
    out = []
    for text in code.split("\n"):
        m = _LINE.match(text)
        if m is None:
            out.append(text)
            continue
        target = f"{m['target']} = " if m["target"] else ""
        out.append(f"{m['indent']}{target}{CALL}({m['name']!r}, {m['rest']!r})")
    return "\n".join(out)


def blank(code: str) -> str:
    """The cell's code as reading tools see it: magic lines blanked, keeping line numbers, and
    ``x = %magic ...`` kept as ``x = None`` so ``x`` is still defined there."""
    out = []
    for text in code.split("\n"):
        m = _LINE.match(text)
        if m is None:
            out.append(text)
        else:
            out.append(f"{m['indent']}{m['target']} = None" if m["target"] else "")
    return "\n".join(out)


# Statements whose result is rows, so a SQL cell leaves them in _sqldf.
_QUERY = re.compile(r"^\s*(\(\s*)*(SELECT|WITH|SHOW|DESCRIBE|DESC|EXPLAIN|VALUES|FROM|TABLE)\b", re.I)


def returns_rows(sql: str) -> bool:
    """Whether the last statement of some Spark SQL returns rows (a query, not a write or DDL)."""
    statements = [s for s in (_strip_comments(p) for p in re.split(r";\s*(?:\n|$)", sql)) if s.strip()]
    return bool(statements) and bool(_QUERY.match(statements[-1]))


def _strip_comments(sql: str) -> str:
    return re.sub(r"^(\s*--[^\n]*(\n|$))+", "", sql)


# ---- cell languages

# Fabric marks a cell's language with %%name on its first line; Databricks with %name.
FABRIC_HEADERS = {"%%sql": "sql", "%%pyspark": "python", "%%python": "python", "%%spark": "scala",
                  "%%sparkr": "r", "%%csharp": "csharp", "%%html": "html", "%%configure": "configure",
                  "%%markdown": "markdown", "%%md": "markdown"}
# %sql and %python are headers only alone on the first line; "%sql SELECT 1" is the line magic.
DATABRICKS_HEADERS = {"%sql": "sql", "%python": "python", "%scala": "scala", "%r": "r", "%md": "markdown"}
_ALONE_ONLY = {"%sql", "%python"}
# What Fabric calls the languages it runs, for publishing (markdown becomes a Markdown cell).
_TO_FABRIC = {"sql": "%%sql", "scala": "%%spark", "r": "%%sparkr"}


class Cell(NamedTuple):
    language: str  # python, sql, markdown, html, scala, r, csharp or configure
    header: str  # the header as written, "" when the cell has none
    body: str  # the cell without its header; Python keeps its line numbers (the header line is blank)


def cell(source: str) -> Cell:
    """The language a code cell is written in, read from its first line, and its body."""
    lead = len(source) - len(source.lstrip())
    first, newline, rest = source[lead:].partition("\n")
    word = first.split(None, 1)[0] if first.strip() else ""
    language = FABRIC_HEADERS.get(word) or (DATABRICKS_HEADERS.get(word)
                                             if word not in _ALONE_ONLY or first.strip() == word else None)
    if language is None:
        return Cell("python", "", source)
    if language == "python":
        return Cell("python", word, source[:lead].replace(" ", "").replace("\t", "") + newline + rest)
    return Cell(language, word, first[len(word):] + newline + rest)


def fabric_cell(c: dict) -> dict:
    """A cell as Fabric reads it: Databricks headers become Fabric's (``%sql`` to ``%%sql``,
    ``%scala`` to ``%%spark``, ``%r`` to ``%%sparkr``), ``%md`` and ``%%markdown`` become a Markdown
    cell, and ``%python`` is dropped (Python is the notebook's language there)."""
    if c.get("type") == "markdown":
        return c
    parsed = cell(c.get("source", ""))
    if not parsed.header or parsed.header in FABRIC_HEADERS and parsed.language != "markdown":
        return c
    if parsed.language == "markdown":
        return {**c, "type": "markdown", "source": parsed.body.strip("\n").removeprefix(" ")}
    if parsed.language == "python":
        return {**c, "source": parsed.body.lstrip("\n")}
    return {**c, "source": _TO_FABRIC[parsed.language] + ("\n" if not parsed.body.startswith("\n") else "")
            + parsed.body}
