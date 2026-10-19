"""Clean up a notebook: tidy-ups the person can apply, and suggestions they decide on.

``plan(cells)`` lists the tidy-ups (unused imports, an import an earlier cell already made,
spacing, empty cells, print lines) and the suggestions (a table read twice, a whole table turned
into pandas, a parameter's value written out again, a row-by-row loop). ``apply(cells, ids)``
returns the cells with the chosen tidy-ups made. Both read the code only: nothing runs, and
nothing is written; the app puts the new cells in the notebook, where Undo puts them back.

A tidy-up never changes what the code does: it leaves frozen cells alone, skips a notebook whose
names can be made at run time (``exec``, ``import *``, ``%run``), keeps a cell's code identical
when it only changes spacing, and drops any change after which the cell no longer parses.
"""

from __future__ import annotations

import ast
import io
import tokenize
from typing import Any

from . import depgraph

_KEEP_IMPORTS = {"__future__", "fabric_desktop"}  # imported for what importing does, or named in docs
_READS = {"read_table", "table", "open_table"}
_TO_PANDAS = {"to_pandas", "toPandas"}


def _python(c: dict[str, Any]) -> bool:
    if c.get("type") == "markdown":
        return False
    head = (c.get("source") or "").lstrip()
    return not head.startswith("%%") or head.startswith(("%%pyspark", "%%python"))


def _frozen(c: dict[str, Any]) -> bool:
    return bool((c.get("meta") or {}).get("frozen"))


def _params(c: dict[str, Any]) -> bool:
    return "parameters" in ((c.get("meta") or {}).get("tags") or [])


def _parse(source: str) -> ast.Module | None:
    code, _ = depgraph._python(source)
    if code is None:
        return None
    try:
        return ast.parse(code)
    except SyntaxError:
        return None


def _dynamic(cells: list[dict[str, Any]]) -> bool:
    # Names this notebook makes at run time: then no import can be called unused.
    for c in cells:
        if c.get("type") == "markdown":
            continue
        info = depgraph.analyze(c.get("source") or "")
        if any(not d["text"].startswith("doesn't parse") for d in info["dynamic"]):
            return True
    return False


def _change(id_: str, title: str, text: str, cells: list[int], on: bool = True) -> dict[str, Any]:
    return {"id": id_, "title": title, "text": text, "cells": cells, "on": on, "diff": []}


def _suggestion(code: str, title: str, text: str, fix: str, cell: int, cells: list[dict[str, Any]],
                kind: str = "optimize") -> dict[str, Any]:
    return {"code": code, "kind": kind, "title": title, "text": text, "fix": fix, "cell": cell,
            "cell_id": cells[cell - 1].get("cell_id")}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---- imports

def _alone(stmt: ast.stmt, body: list[ast.stmt], lines: list[str]) -> bool:
    """True when the statement has its lines to itself (no `a; import b`, no comment inside)."""
    for other in body:
        if other is not stmt and other.lineno <= stmt.end_lineno and other.end_lineno >= stmt.lineno:  # type: ignore[operator]
            return False
    first, last = stmt.lineno - 1, stmt.end_lineno  # type: ignore[operator]
    text = "\n".join(lines[first:last])
    return "#" not in text and lines[first][:stmt.col_offset].strip() == ""


def _bound(alias: ast.alias, node: ast.Import | ast.ImportFrom) -> str:
    return alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)


def _module(node: ast.Import | ast.ImportFrom, alias: ast.alias) -> str:
    return (alias.name if isinstance(node, ast.Import) else (node.module or "")).split(".")[0]


def _imports(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every top-level import statement in the code cells, with what each name binds."""
    out = []
    for n, c in enumerate(cells, 1):
        if not _python(c) or _frozen(c):
            continue
        tree = _parse(c.get("source") or "")
        if tree is None:
            continue
        lines = (c.get("source") or "").split("\n")
        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)) and not any(a.name == "*" for a in stmt.names):
                out.append({"cell": n, "stmt": stmt, "alone": _alone(stmt, tree.body, lines),
                            "names": [(_bound(a, stmt), a) for a in stmt.names]})
    return out


def _uses(cells: list[dict[str, Any]]) -> set[str]:
    used: set[str] = set()
    for c in cells:
        if c.get("type") == "markdown":
            continue
        used |= set(depgraph.analyze(c.get("source") or "").get("uses", []))
        tree = _parse(c.get("source") or "")
        if tree is not None:  # a name the same cell imports and uses isn't in "uses"
            used |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            used |= {n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    return used


def _rebinds(cells: list[dict[str, Any]], name: str, after: int, before: int) -> bool:
    for c in cells[after:before - 1]:
        if c.get("type") != "markdown" and name in depgraph.analyze(c.get("source") or "")["defines"]:
            return True
    return False


def _import_edits(cells: list[dict[str, Any]]) -> tuple[dict[int, dict[ast.stmt, dict[str, str]]], list[dict[str, Any]]]:
    """The names to drop from each import statement (name -> the change's id), and the changes."""
    drops: dict[int, dict[ast.stmt, dict[str, str]]] = {}
    changes: list[dict[str, Any]] = []
    if _dynamic(cells):
        return drops, changes
    imports = [i for i in _imports(cells) if i["alone"]]
    used = _uses(cells)
    unused: list[tuple[int, str]] = []
    repeated: list[tuple[int, str, int]] = []
    first: dict[tuple[str, str, str], int] = {}
    for imp in imports:
        stmt = imp["stmt"]
        for bound, alias in imp["names"]:
            if _module(stmt, alias) in _KEEP_IMPORTS:
                continue
            key = (type(stmt).__name__, getattr(stmt, "module", "") or "", f"{alias.name} as {bound}")
            earlier = first.get(key)
            if earlier is not None and not _rebinds(cells, bound, earlier, imp["cell"]):
                drops.setdefault(imp["cell"], {}).setdefault(stmt, {})[bound] = "repeated_imports"
                repeated.append((imp["cell"], bound, earlier))
                continue
            first.setdefault(key, imp["cell"])
            if bound not in used and not _params(cells[imp["cell"] - 1]):
                drops.setdefault(imp["cell"], {}).setdefault(stmt, {})[bound] = "unused_imports"
                unused.append((imp["cell"], bound))
    if unused:
        where = sorted({n for n, _ in unused})
        names = ", ".join(sorted({b for _, b in unused}))
        changes.append(_change("unused_imports", f"Remove {_plural(len(unused), 'unused import')}",
                               f"{_cells(where)}: no cell uses {names}.", where))
    if repeated:
        where = sorted({n for n, _, _ in repeated})
        text = "; ".join(f"cell {e} already imports {b}" for _, b, e in repeated)
        changes.append(_change("repeated_imports", f"Remove {_plural(len(repeated), 'repeated import')}",
                               f"{_cells(where)}: {text}.", where))
    return drops, changes


def _cells(ns: list[int]) -> str:
    return f"Cell {ns[0]}" if len(ns) == 1 else "Cells " + ", ".join(map(str, ns[:-1])) + f" and {ns[-1]}"


def _drop_imports(source: str, drops: dict[ast.stmt, set[str]]) -> str:
    lines = source.split("\n")
    # From the bottom, so earlier line numbers stay right.
    for stmt, names in sorted(drops.items(), key=lambda kv: -kv[0].lineno):
        keep = [a for a in stmt.names if _bound(a, stmt) not in names]  # type: ignore[arg-type]
        first, last = stmt.lineno - 1, stmt.end_lineno  # type: ignore[operator]
        if keep:
            new = (ast.Import(names=keep) if isinstance(stmt, ast.Import)
                   else ast.ImportFrom(module=stmt.module, names=keep, level=stmt.level))  # type: ignore[attr-defined]
            lines[first:last] = [lines[first][:stmt.col_offset] + ast.unparse(new)]
        else:
            del lines[first:last]
    return "\n".join(lines)


# ---- spacing and print lines

def _string_lines(source: str) -> set[int]:
    """1-based lines inside a multi-line string, whose spaces are part of the value."""
    inside: set[int] = set()
    fstrings: list[int] = []  # where each open f-string (3.12+ tokens) starts
    start_tok = getattr(tokenize, "FSTRING_START", None)
    end_tok = getattr(tokenize, "FSTRING_END", None)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == start_tok:
                fstrings.append(tok.start[0])
            elif tok.type == end_tok and fstrings:
                inside.update(range(fstrings.pop(), tok.end[0]))
            elif tok.type == tokenize.STRING:
                inside.update(range(tok.start[0], tok.end[0]))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set(range(1, source.count("\n") + 2))  # can't tell: leave every line alone
    return inside


def _tidy_spacing(source: str) -> str:
    keep = _string_lines(source)
    lines = source.split("\n")
    out: list[str] = []
    blanks = 0
    for i, line in enumerate(lines, 1):
        if i in keep:
            out.append(line)
            blanks = 0
            continue
        line = line.rstrip()
        blanks = blanks + 1 if not line else 0
        if blanks > 2:
            continue
        out.append(line)
    while out and not out[0].strip() and 1 not in keep:
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _same_code(a: str, b: str) -> bool:
    ta, tb = _parse(a), _parse(b)
    return ta is not None and tb is not None and ast.dump(ta) == ast.dump(tb)


def _print_lines(source: str) -> list[int]:
    tree = _parse(source)
    if tree is None:
        return []
    return [s.lineno for s in tree.body if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
            and isinstance(s.value.func, ast.Name) and s.value.func.id == "print" and s.lineno == s.end_lineno
            and _alone(s, tree.body, source.split("\n"))]


# ---- the plan

def _tidy(cells: list[dict[str, Any]], chosen: set[str] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """The cells after the chosen tidy-ups (all when ``chosen`` is None), the changes found,
    and the cell ids removed."""
    drops, changes = _import_edits(cells)
    new = [dict(c) for c in cells]
    want = (lambda i: True) if chosen is None else (lambda i: i in chosen)

    spaced, printed = [], []
    for n, c in enumerate(cells, 1):
        if not _python(c) or _frozen(c) or _parse(c.get("source") or "") is None:
            continue
        source = c.get("source") or ""
        tidy = _tidy_spacing(source)
        if source.strip() and tidy != source and _same_code(source, tidy):
            spaced.append(n)
        if _print_lines(source):
            printed.append(n)
    if spaced:
        changes.append(_change("spacing", "Tidy spacing",
                               f"{_cells(spaced)}: spaces at line ends, extra blank lines.", spaced))
    if printed:
        count = sum(len(_print_lines(cells[n - 1].get("source") or "")) for n in printed)
        changes.append(_change("print_lines", f"Remove {_plural(count, 'print line')}",
                               f"{_cells(printed)}. Off at first: you may want them.", printed, on=False))
    empty = [n for n, c in enumerate(cells, 1) if not (c.get("source") or "").strip() and not _frozen(c) and not _params(c)]
    if empty and len(empty) < len(cells):
        changes.append(_change("empty_cells", f"Remove {_plural(len(empty), 'empty cell')}",
                               f"{_cells(empty)} {'has' if len(empty) == 1 else 'have'} no code.", empty))

    removed: list[str] = []
    for n, c in enumerate(new, 1):
        before = c.get("source") or ""
        after = before
        picked = {stmt: {b for b, kind in names.items() if want(kind)} for stmt, names in drops.get(n, {}).items()}
        picked = {stmt: names for stmt, names in picked.items() if names}
        if picked:
            after = _drop_imports(after, picked)
        if want("print_lines") and n in printed:
            gone = set(_print_lines(after))
            after = "\n".join(line for i, line in enumerate(after.split("\n"), 1) if i not in gone)
        if want("spacing") and n in spaced:
            tidy = _tidy_spacing(after)
            if _same_code(after, tidy):
                after = tidy
        if after != before and _parse(after) is None:
            after = before  # never leave a cell that doesn't parse
        c["source"] = after
    if want("empty_cells") and any(ch["id"] == "empty_cells" for ch in changes):
        removed = [cells[n - 1].get("cell_id") for n in empty]
    for ch in changes:
        ch["diff"] = _diff(cells, ch, drops)
    return new, changes, removed


def _diff(cells: list[dict[str, Any]], change: dict[str, Any],
          drops: dict[int, dict[ast.stmt, dict[str, str]]]) -> list[dict[str, Any]]:
    """The lines a change removes or rewrites, per cell, for the panel to show (at most 6 a cell)."""
    out = []
    for n in change["cells"]:
        source = cells[n - 1].get("source") or ""
        lines = source.split("\n")
        if change["id"] in ("unused_imports", "repeated_imports"):
            gone = [lines[s.lineno - 1] for s, names in drops.get(n, {}).items() if change["id"] in names.values()]
        elif change["id"] == "print_lines":
            gone = [lines[i - 1] for i in _print_lines(source)]
        else:
            continue
        out.append({"cell": n, "removed": gone[:6]})
    return out


def _suggestions(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    infos = [None if c.get("type") == "markdown" else depgraph.analyze(c.get("source") or "") for c in cells]

    # A table read again by a later cell, with no write to it in between.
    read_by: dict[str, int] = {}
    for n, info in enumerate(infos, 1):
        if info is None:
            continue
        for r in info["reads"]:
            if r["kind"] != "table":
                continue
            m = read_by.get(r["name"])
            if m is not None and m != n:
                out.append(_suggestion("read_again", f"Cell {n} reads {r['name']} again",
                                       f"Cell {m} already reads it. Each read loads the table from OneLake again, which uses capacity.",
                                       f"Reuse the DataFrame from cell {m}.", n, cells))
            read_by.setdefault(r["name"], n)
        for w in info["writes"]:
            if w["kind"] == "table":
                read_by.pop(w["name"], None)

    params: dict[Any, str] = {}
    for c in cells:
        if not _params(c):
            continue
        tree = _parse(c.get("source") or "")
        for s in (tree.body if tree else []):
            if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name) \
                    and isinstance(s.value, ast.Constant) and _worth(s.value.value):
                params[s.value.value] = s.targets[0].id
    for n, c in enumerate(cells, 1):
        if not _python(c) or _params(c):
            continue
        tree = _parse(c.get("source") or "")
        if tree is None:
            continue
        pandas = loop = said = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                inner = node.func.value
                if node.func.attr in _TO_PANDAS and isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) \
                        and inner.func.attr in _READS:
                    pandas = pandas or node.lineno
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Attribute) \
                    and node.iter.func.attr in ("iterrows", "itertuples"):
                loop = loop or node.lineno
            if isinstance(node, ast.Constant) and _worth(node.value) and node.value in params and said is None:
                said = params[node.value]
        if pandas:
            out.append(_suggestion("whole_table_to_pandas", f"Cell {n} turns a whole table into pandas",
                                   "Every row and column is copied into memory before anything filters it.",
                                   "Filter rows and pick columns first, then convert, or stay in polars.", n, cells))
        if loop:
            out.append(_suggestion("row_by_row", f"Cell {n} loops over rows one at a time",
                                   "iterrows and itertuples run Python for each row, which is slow on large tables.",
                                   "Use column expressions (polars or pandas) instead of a loop.", n, cells))
        if said:
            out.append(_suggestion("parameter_value", f"Cell {n} repeats the {said} value",
                                   f"It's the value of the {said} parameter, so a pipeline that passes another value won't change it here.",
                                   f"Use {said}.", n, cells, kind="suggestion"))
    return out


def _worth(v: Any) -> bool:
    # Values distinctive enough that a match isn't a coincidence.
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, str):
        return len(v) >= 4
    return isinstance(v, (int, float)) and v not in (0, 1, -1, 10, 100)


def plan(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """The tidy-ups (each with ``on``: ticked at first) and the suggestions."""
    _, changes, _ = _tidy(cells, None)
    return {"tidy": changes, "suggestions": _suggestions(cells)}


def apply(cells: list[dict[str, Any]], ids: list[str]) -> dict[str, Any]:
    """The cells after the tidy-ups named in ``ids``: {cells: [{cell_id, source}], removed: [cell_id]}."""
    new, changes, removed = _tidy(cells, set(ids))
    known = {c["id"] for c in changes}
    return {"cells": [{"cell_id": c.get("cell_id"), "source": c.get("source") or ""} for c in new
                      if c.get("cell_id") not in removed],
            "removed": removed, "applied": [i for i in ids if i in known]}

