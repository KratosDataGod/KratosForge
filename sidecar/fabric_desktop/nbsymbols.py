"""Go to definition, find references and rename across a notebook's Python cells (editor parity N10).

The notebook's cells share one module scope, as they do in the kernel, so a name a cell defines at
the top level is the same name in every later cell. This reads the cells with Python's own ``ast``
(magics blanked by depgraph, keeping line numbers) and nothing runs. Scopes are followed: a
function, lambda, comprehension or class that binds the name itself (an argument, an assignment, an
import, a loop target), without ``global``, has its own name there, which is neither a reference
nor renamed. Attributes (``df.total``), keyword argument names (``f(total=1)``), strings and
comments are never touched. Cells in another language and cells Python can't read are skipped and
listed, so the app can say what a rename didn't reach.

Positions are character offsets into each cell's source, as the editor counts them.
"""

from __future__ import annotations

import ast
import keyword
import re
from typing import Any

from .depgraph import _python


def _offsets(source: str) -> list[int]:
    """The character offset where each line (1-based) starts."""
    starts = [0, 0]
    for line in source.split("\n"):
        starts.append(starts[-1] + len(line) + 1)
    return starts


def _char_col(line: str, byte_col: int) -> int:
    """ast columns count UTF-8 bytes; the editor counts characters."""
    return len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))


class _Scope:
    __slots__ = ("bound", "globals", "nonlocals")

    def __init__(self) -> None:
        self.bound: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()


def _target_names(node: ast.AST, out: set[str]) -> None:
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.Starred) and isinstance(n.value, ast.Name):
            out.add(n.value.id)


def _args(a: ast.arguments) -> set[str]:
    names = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.ListComp, ast.SetComp,
           ast.DictComp, ast.GeneratorExp)


def _scope_of(node: ast.AST) -> _Scope:
    """The names a function, lambda, class or comprehension binds itself (not its nested scopes')."""
    s = _Scope()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        s.bound |= _args(node.args)
    body: list[ast.AST]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        body = list(node.body)
    elif isinstance(node, ast.Lambda):
        body = [node.body]
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        # A comprehension: its targets; walrus targets bind in the enclosing scope.
        for gen in node.generators:
            _target_names(gen.target, s.bound)
        return s
    else:
        return s
    stack = list(body)
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Global):
            s.globals.update(n.names)
        elif isinstance(n, ast.Nonlocal):
            s.nonlocals.update(n.names)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            s.bound.add(n.name)
            stack.extend(n.decorator_list)  # decorators run in this scope; the body is its own
            continue
        elif isinstance(n, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                s.bound.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            s.bound.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            s.bound.add(n.id)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            s.bound.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            s.bound.add(n.rest)
        stack.extend(ast.iter_child_nodes(n))
    return s


_DEF = {ast.FunctionDef: r"(?:async\s+)?def\s+", ast.AsyncFunctionDef: r"async\s+def\s+", ast.ClassDef: r"class\s+"}


def _def_col(lines: list[str], node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> tuple[int, int] | None:
    """(line, char col) of a def or class statement's name."""
    text = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
    m = re.compile(_DEF[type(node)] + re.escape(node.name) + r"\b").match(text, _char_col(text, node.col_offset))
    return (node.lineno, m.end() - len(node.name)) if m else None


def _alias_col(lines: list[str], a: ast.alias, bound: str) -> tuple[int, int] | None:
    """(line, char col) where an import binds ``bound``: the ``as`` name, or the first part of the module."""
    text = lines[a.lineno - 1] if 0 < a.lineno <= len(lines) else ""
    start = _char_col(text, a.col_offset)
    if not a.asname:
        return (a.lineno, start) if text.startswith(bound, start) else None
    m = re.compile(r"\s+as\s+(" + re.escape(bound) + r")\b").search(text, start + len(a.name))
    return (a.lineno, m.start(1)) if m else None


def _word_col(lines: list[str], line: int, char_from: int, name: str) -> int | None:
    text = lines[line - 1] if 0 < line <= len(lines) else ""
    m = re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])").search(text, char_from)
    return m.start() if m else None


class _Finder:
    """Every place a module-level name is used or bound in one cell: (line, char col, kind)."""

    def __init__(self, name: str, source_lines: list[str]) -> None:
        self.name = name
        self.lines = source_lines
        self.found: list[tuple[int, int, str]] = []  # kind: "definition" | "reference"

    def _add(self, line: int, byte_col: int, kind: str) -> None:
        text = self.lines[line - 1] if 0 < line <= len(self.lines) else ""
        col = _char_col(text, byte_col)
        if text[col:col + len(self.name)] == self.name:  # only where the name is written as it is
            self.found.append((line, col, kind))

    def visit(self, node: ast.AST, shadowed: bool, base: bool | None = None) -> None:
        """``shadowed``: the name here isn't the module's. ``base``: what a function, lambda or
        comprehension started here inherits, which skips a class body's own names."""
        name = self.name
        base = shadowed if base is None else base
        if isinstance(node, _SCOPES):
            scope = _scope_of(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # The def or class name is bound where the statement is; decorators, defaults,
                # annotations and bases are read there too.
                if node.name == name and not shadowed:
                    at = _def_col(self.lines, node)
                    if at:
                        self.found.append((*at, "definition"))
                outer: list[ast.AST] = list(node.decorator_list)
                if isinstance(node, ast.ClassDef):
                    outer += node.bases + [k.value for k in node.keywords]
                else:
                    a = node.args
                    outer += [d for d in a.defaults + a.kw_defaults if d is not None]
                    outer += [x.annotation for x in a.posonlyargs + a.args + a.kwonlyargs if x.annotation]
                    for extra in (a.vararg, a.kwarg):
                        if extra is not None and extra.annotation is not None:
                            outer.append(extra.annotation)
                    if node.returns:
                        outer.append(node.returns)
                for n in outer:
                    self.visit(n, shadowed, base)
            elif isinstance(node, ast.Lambda):
                for d in node.args.defaults + node.args.kw_defaults:
                    if d is not None:
                        self.visit(d, shadowed, base)
            else:  # a comprehension's first iterable is read where the comprehension is
                self.visit(node.generators[0].iter, shadowed, base)
            if name in scope.globals:
                inner = False
            elif name in scope.nonlocals:
                inner = base
            else:
                inner = base or name in scope.bound
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                for i, gen in enumerate(node.generators):
                    self.visit(gen.target, inner)
                    if i:
                        self.visit(gen.iter, inner)
                    for cond in gen.ifs:
                        self.visit(cond, inner)
                for p in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                    self.visit(p, inner)
                return
            body = [node.body] if isinstance(node, ast.Lambda) else node.body
            # Functions inside a class body don't see the class's own names.
            child_base = base if isinstance(node, ast.ClassDef) else inner
            for n in body:
                self.visit(n, inner, child_base)
            return
        if isinstance(node, ast.Name) and node.id == name:
            if not shadowed:
                self._add(node.lineno, node.col_offset, "definition" if isinstance(node.ctx, ast.Store) else "reference")
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and not shadowed:
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if bound == name and alias.name != "*":
                    at = _alias_col(self.lines, alias, bound)
                    if at:
                        self.found.append((*at, "definition"))
        elif isinstance(node, ast.Global) and name in node.names and not shadowed:
            text = self.lines[node.lineno - 1] if 0 < node.lineno <= len(self.lines) else ""
            col = _word_col(self.lines, node.lineno, _char_col(text, node.col_offset) + 6, name)
            if col is not None:
                self.found.append((node.lineno, col, "reference"))
        elif isinstance(node, ast.ExceptHandler) and node.name == name and not shadowed:
            text = self.lines[node.lineno - 1] if 0 < node.lineno <= len(self.lines) else ""
            m = re.compile(r"\bas\s+(" + re.escape(name) + r")\b").search(text, _char_col(text, node.col_offset))
            if m:
                self.found.append((node.lineno, m.start(1), "definition"))
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name and not shadowed:
            text = self.lines[node.lineno - 1] if 0 < node.lineno <= len(self.lines) else ""
            col = _word_col(self.lines, node.lineno, _char_col(text, node.col_offset), name)
            if col is not None:
                self.found.append((node.lineno, col, "definition"))
        for child in ast.iter_child_nodes(node):
            self.visit(child, shadowed, base)


def _parse(source: str) -> tuple[ast.Module | None, str]:
    """The cell's module, or None and why it was skipped."""
    code, _ = _python(source)
    if code is None:
        return None, "another language"
    try:
        return ast.parse(code), ""
    except (SyntaxError, ValueError):
        return None, "Python can't read it"


def _name_at(tree: ast.Module, source: str, offset: int) -> str | None:
    """The plain name (not an attribute or keyword argument) under a character offset."""
    starts = _offsets(source)
    lines = source.split("\n")
    for n in ast.walk(tree):
        found: list[tuple[int, int, str]] = []
        if isinstance(n, ast.Name):
            text = lines[n.lineno - 1] if 0 < n.lineno <= len(lines) else ""
            found.append((n.lineno, _char_col(text, n.col_offset), n.id))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            at = _def_col(lines, n)
            if at:
                found.append((*at, n.name))
        elif isinstance(n, ast.alias) and n.name != "*":
            bound = n.asname or n.name.split(".")[0]
            at = _alias_col(lines, n, bound)
            if at:
                found.append((*at, bound))
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            text = lines[n.lineno - 1] if 0 < n.lineno <= len(lines) else ""
            for nm in n.names:
                col = _word_col(lines, n.lineno, _char_col(text, n.col_offset) + 6, nm)
                if col is not None:
                    found.append((n.lineno, col, nm))
        for line, col, nm in found:
            start = starts[line] + col
            if start <= offset <= start + len(nm):
                return nm
    return None


def _bound_or_used(tree: ast.Module, name: str) -> int:
    """How many times ``name`` is written as a name anywhere in the cell, in any scope: an argument,
    a local, a comprehension variable, a def, class or import. A rename to such a name could be
    captured by that scope's own binding, so it's refused."""
    count = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            count += n.id == name
        elif isinstance(n, ast.arg):
            count += n.arg == name
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            count += n.name == name
        elif isinstance(n, ast.alias):
            count += (n.asname or n.name.split(".")[0]) == name
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            count += name in n.names
        elif isinstance(n, ast.ExceptHandler):
            count += n.name == name
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)):
            count += n.name == name
        elif isinstance(n, ast.MatchMapping):
            count += n.rest == name
    return count


def find_symbol(cells: list[dict[str, Any]], cell: int, offset: int, name: str | None = None) -> dict[str, Any]:
    """The module-level name at ``offset`` in cell ``cell`` (or ``name``), with every definition and
    reference in the notebook's Python cells: {"name", "places": [{cell, from, to, kind, line, text}],
    "skipped": [{cell, why}], "anywhere": how often the name is written in any scope, a function's own
    names included}. ``name`` is None when the place isn't on a plain name."""
    if not isinstance(cells, list) or not all(isinstance(c, dict) for c in cells):
        raise ValueError("cells is a list of cells")
    trees: list[ast.Module | None] = []
    skipped: list[dict[str, Any]] = []
    for i, c in enumerate(cells):
        if c.get("type") != "code":
            trees.append(None)
            continue
        tree, why = _parse(str(c.get("source") or ""))
        trees.append(tree)
        if tree is None and why != "another language":
            skipped.append({"cell": i, "why": why})
    if name is None:
        here = trees[cell] if 0 <= cell < len(cells) else None
        if here is None:
            return {"name": None, "places": [], "skipped": skipped}
        name = _name_at(here, str(cells[cell].get("source") or ""), int(offset))
        if name is None:
            return {"name": None, "places": [], "skipped": skipped}
    places: list[dict[str, Any]] = []
    for i, tree in enumerate(trees):
        if tree is None:
            continue
        source = str(cells[i].get("source") or "")
        lines = source.split("\n")
        finder = _Finder(name, lines)
        for stmt in tree.body:
            finder.visit(stmt, False)
        starts = _offsets(source)
        for line, col, kind in sorted(set(finder.found)):
            start = starts[line] + col
            places.append({"cell": i, "from": start, "to": start + len(name), "kind": kind, "line": line,
                           "text": lines[line - 1].strip()[:200]})
    elsewhere = sum(_bound_or_used(tree, name) for tree in trees if tree is not None)
    return {"name": name, "places": places, "skipped": skipped, "anywhere": elsewhere}


def rename_check(new: str) -> str | None:
    """Why ``new`` can't be a Python name, or None."""
    if not isinstance(new, str) or not new.isidentifier():
        return "That isn't a Python name: use letters, digits and underscores, not starting with a digit."
    if keyword.iskeyword(new):
        return f"{new} is a Python keyword."
    return None
