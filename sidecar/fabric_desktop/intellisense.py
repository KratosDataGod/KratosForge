"""What the notebook editor shows about code without running it: hover docs, signature help while
typing a call, and Python syntax errors as you type.

Hover and signature help read live objects in the kernel's namespace the same way completion does
(``Kernel._lookup``): plain dotted names only, nothing is called and properties aren't read, so a
hover never runs user code or touches OneLake. Values of names that look like secrets are hidden.
The syntax check only compiles the cell's text; it needs no kernel.
"""
from __future__ import annotations

import ast
import inspect
import re
import warnings
from typing import Any, Callable

DOC_CHARS = 1500
_NAME_CHARS = re.compile(r"[\w.]")
_CALL_NAME = re.compile(r"([A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*)\s*$")


# ---------- hover ----------

def dotted_at(code: str, cursor: int) -> tuple[str, int, int] | None:
    """The dotted name the cursor is on (``df.group_by`` with the cursor in ``group``), cut at the
    cursor's word: hovering ``df`` in ``df.group_by`` gives ``df``. None when it isn't on a name."""
    if not 0 <= cursor <= len(code):
        return None
    start = cursor
    while start > 0 and _NAME_CHARS.match(code[start - 1]):
        start -= 1
    end = cursor
    while end < len(code) and re.match(r"\w", code[end]):
        end += 1
    name = code[start:end].strip(".")
    if not name or not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", name):
        return None
    return name, start, end


def describe(name: str, obj: Any, hidden: bool = False, preview: Callable[[Any], str] | None = None) -> dict[str, Any]:
    """What a hover card shows for an object: its kind, signature, type and first part of its docs."""
    kind = ("module" if inspect.ismodule(obj) else "class" if inspect.isclass(obj)
            else "function" if inspect.isroutine(obj) or callable(obj) else "variable")
    out: dict[str, Any] = {"name": name, "kind": kind, "type": type(obj).__name__}
    if kind in ("function", "class"):
        out["signature"] = signature_text(obj)
    if kind == "variable":
        out["value"] = "hidden" if hidden else (preview(obj) if preview else "")
        doc = ""  # a value's docs are its type's, which say nothing about it
    else:
        doc = _doc(obj)
    if doc:
        out["doc"] = doc
    return out


def signature_text(obj: Any) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "(...)"


def _doc(obj: Any) -> str:
    try:
        doc = inspect.getdoc(obj) or ""
    except Exception:  # noqa: BLE001 - a broken __doc__ just means no docs
        return ""
    return doc if len(doc) <= DOC_CHARS else doc[:DOC_CHARS].rstrip() + " ..."


# ---------- signature help ----------

def open_call(code: str) -> tuple[str, int, str | None] | None:
    """The call the end of ``code`` is inside: (dotted callable name, index of the argument being
    typed, keyword being typed or None). Strings and comments are skipped, so a ``(`` inside them
    doesn't count. None when the end isn't inside a call's parentheses."""
    stack: list[list[Any]] = []  # [bracket, position, commas]
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        if ch == "#":
            nl = code.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if ch in "\"'":
            quote = code[i:i + 3] if code[i:i + 3] in ('"""', "'''") else ch
            j = i + len(quote)
            while j < n and not code.startswith(quote, j):
                if code[j] == "\\":
                    j += 1
                elif code[j] == "\n" and len(quote) == 1:
                    break
                j += 1
            if j >= n:
                return None  # the end is inside a string
            i = j + len(quote)
            continue
        if ch in "([{":
            stack.append([ch, i, 0])
        elif ch in ")]}":
            if stack:
                stack.pop()
        elif ch == "," and stack:
            stack[-1][2] += 1
        i += 1
    if not stack or stack[-1][0] != "(":
        return None
    _, pos, commas = stack[-1]
    m = _CALL_NAME.search(code[:pos])
    if not m:
        return None
    name = re.sub(r"\s+", "", m.group(1))
    typed = code[pos + 1:].rsplit(",", 1)[-1]
    kw = re.match(r"\s*([A-Za-z_]\w*)\s*=(?!=)", typed)
    return name, commas, kw.group(1) if kw else None


def call_help(obj: Any, name: str, index: int, keyword: str | None) -> dict[str, Any] | None:
    """The parameters of ``obj`` with the one being typed marked, for the signature popup."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    params = list(sig.parameters.values())
    if inspect.isclass(obj) and params and params[0].name == "self":
        params = params[1:]
    active = None
    if keyword:
        active = next((i for i, p in enumerate(params) if p.name == keyword), None)
        if active is None:
            active = next((i for i, p in enumerate(params) if p.kind is p.VAR_KEYWORD), None)
    else:
        positional = [i for i, p in enumerate(params) if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if index < len(positional):
            active = positional[index]
        else:
            active = next((i for i, p in enumerate(params) if p.kind is p.VAR_POSITIONAL), None)
    doc = _doc(obj).split("\n\n", 1)[0]
    return {"name": name, "params": [str(p) for p in params], "active": active,
            "returns": "" if sig.return_annotation is sig.empty else inspect.formatannotation(sig.return_annotation),
            "doc": doc[:400]}


# ---------- syntax ----------

_CELL_MAGIC = re.compile(r"^\s*%%(\w+)")
_PY_CELL_MAGICS = ("pyspark", "python")


def syntax_problems(source: str) -> list[dict[str, Any]]:
    """The first Python syntax error in a cell, as {line, col, end_line, end_col, message} with
    1-based lines and 0-based columns in the cell's own text. Cells in another language (``%%sql``,
    ``%%html``...) and IPython syntax (``%magic``, ``!cmd``, ``x = %sql ...``) aren't errors."""
    first = source.lstrip("\n").split("\n", 1)[0]
    m = _CELL_MAGIC.match(first)
    offset = 0
    if m:
        if m.group(1).lower() not in _PY_CELL_MAGICS:
            return []
        offset = source[:source.index(first)].count("\n") + 1
        source = source.split("\n", offset)[-1] if source.count("\n") >= offset else ""
    lines = []
    for line in source.split("\n"):
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]
        if stripped.startswith(("%", "!")):
            lines.append(f"{indent}pass" + " " * max(0, len(stripped) - 4))
        else:
            # `x = %sql ...` or `files = !ls` (IPython assignment forms)
            lines.append(re.sub(r"(=\s*)[%!].*$", lambda a: a.group(1) + "None", line))
    text = "\n".join(lines)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(text, "<cell>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT, dont_inherit=True)
    except SyntaxError as err:
        line = (err.lineno or 1)
        col = max((err.offset or 1) - 1, 0)
        end_line = getattr(err, "end_lineno", None) or line
        end_col = max((getattr(err, "end_offset", None) or col + 2) - 1, col + 1)
        if end_line < line or (end_line == line and end_col <= col):
            end_line, end_col = line, col + 1
        return [{"line": line + offset, "col": col, "end_line": end_line + offset, "end_col": end_col,
                 "message": _message(err)}]
    return []


def _message(err: SyntaxError) -> str:
    msg = err.msg or "Invalid syntax"
    return msg[0].upper() + msg[1:]
