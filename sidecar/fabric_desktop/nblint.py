"""The warnings the notebook editor underlines in a Python cell (parity N09), read without running it.

Two sources, both read on this PC:

- The notebook best-practice rules (notebook_practices.py), the same findings the Best practices panel
  lists, with the same governance settings, so an underline and the panel never disagree.
- Names a cell uses that nothing defines: not a Python built-in, not one the kernel provides (``spark``,
  ``display``, ``notebookutils``...), and bound by no cell of the notebook in any way (cells share one
  namespace and can run in any order, so a name any cell binds counts as defined). Only what is certain
  is reported: a notebook that can put names in the namespace some other way (``%run``, ``import *``,
  ``exec``, ``globals()``...) gets no undefined-name warnings at all, a cell in another language or one
  that doesn't parse counts every word in it as defined, and a name read inside ``try: ... except
  NameError`` is a deliberate probe. Unused imports aren't reported: a later cell may use them.

The syntax error underline stays intellisense.syntax_problems; a cell with one gets nothing from here.
"""

from __future__ import annotations

import ast
import builtins
import json
import re
from functools import lru_cache
from typing import Any

from . import notebook_practices
from .intellisense import python_text

MAX_CELL = 64_000         # characters: a bigger cell gets only the syntax check
MAX_NOTEBOOK = 1_000_000  # characters over every cell: past that, the practice rules are left to the panel
MAX_PROBLEMS = 100

# What a notebook's namespace has before any cell runs: notebook.Kernel._base_namespace on this PC, and what
# Fabric's and Databricks' Python kernels add (depgraph._BUILTINS lists the same for the dependency graph).
KERNEL_NAMES = frozenset({
    "spark", "sc", "sqlContext", "display", "displayHTML", "notebookutils", "mssparkutils", "dbutils",
    "getArgument", "table", "sql", "udf", "In", "Out", "input", "exit", "quit", "get_ipython",
})
_KNOWN = frozenset(dir(builtins)) | KERNEL_NAMES
_WORD = re.compile(r"[A-Za-z_]\w*")
# Ways a name can reach the namespace that no parse of the cells shows.
_OPAQUE = re.compile(r"(?m)^\s*%(?:run|store|load|macro|aimport)\b|\bimport\s+\*|"
                     r"\b(?:exec|eval|globals|locals|vars|__builtins__|builtins|__dict__|__main__|sys\.modules)\b")
_PROBE_ERRORS = {"NameError", "Exception", "BaseException"}

_SEVERITY = {"Error": "warning", "Warning": "warning", "Info": "info"}  # red stays for syntax errors
_practice_cache: dict[str, Any] = {}


def lint(source: str, cells: list[str], cell: int, settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Warnings for ``source``, which is code cell ``cell`` (0-based) of ``cells`` (the notebook's code cells,
    in order), as {line, col, end_line, end_col, severity, message, rule_id, source}: 1-based lines and 0-based
    columns in characters of the cell's text. ``settings`` are the governance settings the panel uses."""
    if not isinstance(cells, list) or not all(isinstance(c, str) for c in cells) or not 0 <= cell < len(cells):
        return []
    if len(source) > MAX_CELL:
        return []
    cells = [*cells[:cell], source, *cells[cell + 1:]]
    lines = source.split("\n")
    out = _undefined(source, cells)
    if sum(len(c) for c in cells) <= MAX_NOTEBOOK:
        out += _practices(lines, cells, cell, settings)
    out.sort(key=lambda p: (p["line"], p["col"]))
    return out[:MAX_PROBLEMS]


# ---------- names nothing defines ----------

def _undefined(source: str, cells: list[str]) -> list[dict[str, Any]]:
    if any(_OPAQUE.search(c) for c in cells):
        return []
    bound: set[str] = set()
    for c in cells:
        bound |= _read(c)[0]
    uses = _read(source)[1]
    return [{"line": line, "col": col, "end_line": line, "end_col": end, "severity": "warning", "rule_id": "undefined-name",
             "source": "Python", "message": f"{name} isn't defined: no cell of this notebook sets it, and it isn't a "
                                            "Python built-in. Check the spelling, or define or import it in a cell."}
            for name, line, col, end in uses if name not in bound and name not in _KNOWN]


@lru_cache(maxsize=2048)
def _read(source: str) -> tuple[frozenset[str], tuple[tuple[str, int, int, int], ...]]:
    """The names a cell binds, and the names it reads with where (name, line, col, end_col). A cell in another
    language, too big or that doesn't parse binds every word in it and reads nothing."""
    every = frozenset(_WORD.findall(source))
    cell = python_text(source) if len(source) <= MAX_CELL else None
    if cell is None:
        return every, ()
    text, offset = cell
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return every, ()
    raw = source.split("\n")
    lines = text.split("\n")
    # Magic and shell lines (`%%capture out`, `%sql -o df`, `x = %sql ...`) can bind names: every word in them counts.
    bound = set(_WORD.findall("\n".join(raw[:offset])))
    for i, line in enumerate(lines):
        if offset + i < len(raw) and raw[offset + i] != line:
            bound.update(_WORD.findall(raw[offset + i]))
    skip: set[int] = set()  # annotations (maybe never evaluated) and NameError probes
    for node in ast.walk(tree):
        bound.update(_binds(node))
        for part in _unevaluated(node):
            skip.update(id(n) for n in ast.walk(part))
    uses = tuple((n.id, n.lineno + offset, _chars(lines, n.lineno, n.col_offset), _chars(lines, n.lineno, n.end_col_offset))
                 for n in ast.walk(tree)
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and id(n) not in skip and not n.id.startswith("_"))
    return frozenset(bound), uses


def _binds(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return [node.id]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.alias):
        return [(node.asname or node.name).split(".")[0]]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return list(node.names)
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
        return [node.name]
    if isinstance(node, ast.MatchMapping) and node.rest:
        return [node.rest]
    if isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
        return [node.name]
    if isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
        return [node.name.id]
    return []


def _unevaluated(node: ast.AST) -> list[ast.AST]:
    """The parts of ``node`` whose names aren't reported: annotations and lazy type aliases, and the body
    of a try whose handler catches NameError (or everything)."""
    if isinstance(node, ast.arg):
        return [node.annotation] if node.annotation else []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [node.returns] if node.returns else []
    if isinstance(node, ast.AnnAssign):
        return [node.annotation]
    if isinstance(node, ast.TypeAlias):
        return [node.value]
    if isinstance(node, (ast.Try, ast.TryStar)) and any(_catches_name_error(h) for h in node.handlers):
        return list(node.body)
    return []


def _catches_name_error(handler: ast.ExceptHandler) -> bool:
    kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(k is None or (isinstance(k, ast.Name) and k.id in _PROBE_ERRORS) for k in kinds)


def _chars(lines: list[str], line: int, byte_col: int | None) -> int:
    """A column the parser gave in UTF-8 bytes, in characters of that line."""
    text = lines[line - 1] if 0 < line <= len(lines) else ""
    if byte_col is None:
        return len(text)
    return len(text.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))


# ---------- the best-practice rules ----------

def _practices(lines: list[str], cells: list[str], cell: int, settings: dict[str, Any] | None) -> list[dict[str, Any]]:
    settings = settings if isinstance(settings, dict) else {}
    key = json.dumps([cells, settings], sort_keys=True, default=str)
    if _practice_cache.get("key") != key:  # every cell of a notebook asks with the same cells when it opens
        _practice_cache.clear()
        _practice_cache.update(key=key, findings=notebook_practices.check(cells, False, settings)["findings"])
    out = []
    for f in _practice_cache["findings"]:
        if f["cell"] != cell + 1 or not 0 < f["line"] <= len(lines):
            continue
        line, text = f["line"], lines[f["line"] - 1]
        if "col" in f:
            col, end_line, end_col = f["col"], f["end_line"], f["end_col"]
        else:  # a rule that knows only the line: the line's code
            col, end_line, end_col = len(text) - len(text.lstrip()), line, len(text.rstrip())
        out.append({"line": line, "col": col, "end_line": end_line, "end_col": end_col,
                    "severity": _SEVERITY.get(f["severity"], "warning"), "rule_id": f["rule_id"], "source": "Best practices",
                    "message": f"{f['rule_id']} {f['rule']}. {f['description']}"})
    return out
