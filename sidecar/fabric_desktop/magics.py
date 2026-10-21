"""Line magics in notebook cells: how the kernel runs them and how reading tools see them.

A line magic is ``%name rest`` on a line of its own, or ``target = %name rest`` to keep what it
returns (``df = %sql SELECT 1``), as IPython, Fabric and Databricks notebooks allow. The kernel
turns each into a call it answers (``rewrite``); dependencies, the environment check and other
tools that read code instead of running it see the line blanked, with the assignment kept so the
name still counts as defined (``blank``). ``!command`` is the shell escape: it becomes the
``system`` magic, or ``sx`` (which keeps the output lines) when assigned, as in IPython.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# "%name rest", optionally after "target =" or "a, b =". "%%name" is a cell magic, not this.
_LINE = re.compile(r"^(?P<indent>\s*)(?:(?P<target>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*=\s*)?"
                   r"%(?!%)(?P<name>\w+)\s?(?P<rest>.*)$")
# "!command", optionally after "target =". ("!=" can't start a statement.)
_BANG = re.compile(r"^(?P<indent>\s*)(?:(?P<target>[A-Za-z_]\w*)\s*=\s*)?!(?!=)(?P<rest>.*)$")
CALL = "__fd_magic__"


class Magic(NamedTuple):
    line: int  # 1-based
    name: str
    rest: str
    target: str | None


def _match(text: str) -> tuple[str, str, str, str | None] | None:
    """(indent, name, rest, target) for a line magic or ``!`` line, else None."""
    if m := _LINE.match(text):
        return m["indent"], m["name"], m["rest"], m["target"]
    if m := _BANG.match(text):
        return m["indent"], "sx" if m["target"] else "system", m["rest"], m["target"]
    return None


def _lines(code: str) -> list[tuple[str, tuple[str, str, str, str | None] | None]]:
    """Each line with its magic, if it is one. As in IPython, only a line that starts a statement can
    be a magic: one inside a triple-quoted string, brackets or after a backslash is left alone."""
    out = []
    quote, depth, joined = "", 0, False
    for text in code.split("\n"):
        m = None if quote or depth or joined else _match(text)
        out.append((text, m))
        if m is None:
            quote, depth = _scan(text, quote, depth)
            joined = not quote and text.endswith("\\") and not text.endswith("\\\\")
        else:
            joined = False
    return out


def _scan(text: str, quote: str, depth: int) -> tuple[str, int]:
    """The open triple quote and bracket depth after a line of Python, given those before it.
    Single-quoted strings and comments end with the line."""
    i, n = 0, len(text)
    while i < n:
        if quote:
            if text[i] == "\\":
                i += 2
                continue
            if text.startswith(quote, i):
                i += len(quote)
                quote = ""
                continue
            i += 1
            continue
        c = text[i]
        if c == "#":
            break
        if c in "\"'":
            q = text[i:i + 3] if text[i:i + 3] in ('"""', "'''") else c
            if len(q) == 3:
                quote, i = q, i + 3
                continue
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(depth - 1, 0)
        i += 1
    return quote, depth


def find(code: str) -> list[Magic]:
    """The line magics (and ``!`` lines) in a cell's code, in order."""
    return [Magic(i, m[1], m[2], m[3]) for i, (_, m) in enumerate(_lines(code), 1) if m]


def rewrite(code: str) -> str:
    """The cell's code with each line magic replaced by a call the kernel answers."""
    out = []
    for text, m in _lines(code):
        if m is None:
            out.append(text)
            continue
        indent, name, rest, target = m
        out.append(f"{indent}{f'{target} = ' if target else ''}{CALL}({name!r}, {rest!r})")
    return "\n".join(out)


def blank(code: str) -> str:
    """The cell's code as reading tools see it: magic lines blanked, keeping line numbers, and
    ``x = %magic ...`` kept as ``x = None`` so ``x`` is still defined there."""
    out = []
    for text, m in _lines(code):
        if m is None:
            out.append(text)
        else:
            out.append(f"{m[0]}{m[3]} = None" if m[3] else "")
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
DATABRICKS_HEADERS = {"%sql": "sql", "%python": "python", "%scala": "scala", "%r": "r", "%md": "markdown",
                      "%sh": "shell"}
# Script cells (IPython's, and Databricks' %sh): the cell runs in that shell on this PC (shell.py).
SHELL_HEADERS = {"%%sh": "sh", "%%bash": "bash", "%%cmd": "cmd", "%%powershell": "powershell", "%sh": "sh"}
# IPython's cell magics that wrap Python (the body runs as Python, timed or captured), and the
# ones whose body isn't code at all.
PYTHON_WRAPPERS = {"%%time", "%%timeit", "%%capture"}
OTHER_HEADERS = {"%%writefile": "writefile", "%%javascript": "javascript", "%%js": "javascript"}
_ALONE_ONLY = {"%sql", "%python"}
# What Fabric calls the languages it runs, for publishing (markdown becomes a Markdown cell).
_TO_FABRIC = {"sql": "%%sql", "scala": "%%spark", "r": "%%sparkr", "shell": "%%sh"}


class Cell(NamedTuple):
    language: str  # python, sql, markdown, html, shell, scala, r, csharp or configure
    header: str  # the header as written, "" when the cell has none
    body: str  # the cell without its header; Python keeps its line numbers (the header line is blank)
    args: str = ""  # what follows the header on its line (%%capture out, %%writefile -a helper.py)


def cell(source: str) -> Cell:
    """The language a code cell is written in, read from its first line, and its body."""
    lead = len(source) - len(source.lstrip())
    first, newline, rest = source[lead:].partition("\n")
    word = first.split(None, 1)[0] if first.strip() else ""
    language = FABRIC_HEADERS.get(word) or ("shell" if word in SHELL_HEADERS else None) or \
        ("python" if word in PYTHON_WRAPPERS else None) or OTHER_HEADERS.get(word) or (DATABRICKS_HEADERS.get(word)
                                             if word not in _ALONE_ONLY or first.strip() == word else None)
    if language is None:
        return Cell("python", "", source)
    args = first[len(word):].strip() if word in PYTHON_WRAPPERS or word in OTHER_HEADERS else ""
    if word in OTHER_HEADERS:  # the first line holds arguments, not content
        return Cell(language, word, newline + rest, args)
    if language == "python":
        return Cell("python", word, source[:lead].replace(" ", "").replace("\t", "") + newline + rest, args)
    return Cell(language, word, first[len(word):] + newline + rest, args)


def fabric_cell(c: dict) -> dict:
    """A cell as Fabric reads it: Databricks headers become Fabric's (``%sql`` to ``%%sql``,
    ``%scala`` to ``%%spark``, ``%r`` to ``%%sparkr``), ``%md`` and ``%%markdown`` become a Markdown
    cell, and ``%python`` is dropped (Python is the notebook's language there)."""
    if c.get("type") == "markdown":
        return c
    parsed = cell(c.get("source", ""))
    if not parsed.header or parsed.header.startswith("%%") and parsed.language != "markdown":
        return c
    if parsed.language == "markdown":
        return {**c, "type": "markdown", "source": parsed.body.strip("\n").removeprefix(" ")}
    if parsed.language == "python":
        return {**c, "source": parsed.body.lstrip("\n")}
    body = parsed.body.lstrip(" \t")
    return {**c, "source": _TO_FABRIC[parsed.language] + ("" if body.startswith("\n") else "\n") + body}
