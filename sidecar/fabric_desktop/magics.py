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
        bare = text.removesuffix("\r")  # a cell saved on Windows: the magic's argument doesn't end in \r
        m = None if quote or depth or joined else _match(bare)
        out.append((text, m))
        if m is None:
            quote, depth, joined = _scan(bare, quote, depth)
        else:
            joined = False
    return out


def _scan(text: str, quote: str, depth: int) -> tuple[str, int, bool]:
    """The open triple quote and bracket depth after a line of Python, given those before it, and
    whether the line goes on to the next with a backslash. Single-quoted strings and comments end
    with the line; a backslash at the end of a comment (# in C:\\temp\\) doesn't join lines."""
    i, n, end = 0, len(text), len(text)
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
            end = i
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
    return quote, depth, not quote and text[:end].endswith("\\")


def words(line: str) -> list[str]:
    """A magic's arguments, split the way Windows' own command line splits them: spaces separate,
    quotes group a name with spaces, and a backslash is kept as it is (C:\\Users\\me\\req.txt
    arrives whole). Inside double quotes, \\" is a quote, as in "say \\"hi\\"", unless it ends
    the word ("C:\\My Folder\\" is a folder). Single quotes keep everything inside them."""
    out: list[str] = []
    word, started, quote, i, n = [], False, "", 0, len(line)
    while i < n:
        c = line[i]
        if quote == '"' and c == "\\" and line[i + 1:i + 2] == '"' and not _ends_word(line, i + 2):
            word.append('"')
            i += 2
            continue
        if quote:
            if c == quote:
                quote = ""
            else:
                word.append(c)
        elif c in "\"'":
            quote, started = c, True
        elif c.isspace():
            if started:
                out.append("".join(word))
            word, started = [], False
        else:
            word.append(c)
            started = True
        i += 1
    if quote:
        raise ValueError(f"No closing quotation ({quote}).")
    if started:
        out.append("".join(word))
    return out


def _ends_word(line: str, i: int) -> bool:
    return i >= len(line) or line[i].isspace()


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
    statements = [s for s in _statements(sql) if s.strip()]
    return bool(statements) and bool(_QUERY.match(statements[-1]))


def _statements(sql: str) -> list[str]:
    """Spark SQL split at its semicolons, with -- and /* */ comments taken out; a semicolon or
    comment marker inside a quoted string or `name` stays where it is."""
    out, part, quote, i, n = [], [], "", 0, len(sql)
    while i < n:
        c = sql[i]
        if quote:
            part.append(c)
            if c == "\\" and quote != "`" and i + 1 < n:
                part.append(sql[i + 1])
                i += 1
            elif c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
            part.append(c)
        elif sql.startswith("--", i):
            i = sql.find("\n", i)
            if i < 0:
                break
            continue
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            part.append(" ")
            continue
        elif c == ";":
            out.append("".join(part))
            part = []
        else:
            part.append(c)
        i += 1
    out.append("".join(part))
    return out


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
OTHER_HEADERS = {"%%writefile": "writefile", "%%javascript": "javascript", "%%js": "javascript", "%%tsql": "tsql"}
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
