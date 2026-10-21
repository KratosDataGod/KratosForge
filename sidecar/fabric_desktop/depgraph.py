"""How a notebook's cells depend on each other, found by reading the code (not by running it).

For each code cell: the names it defines and uses, the lakehouse tables and files it reads and
writes, what it changes outside the notebook (its side effects), and whether it does things
reading can't follow (``exec``, ``from x import *``, ``%run``). Then, for each cell, the earlier
cells it depends on and why: a variable, a table or a file another cell wrote, or a dependency
the person declared in the cell's metadata (``depends_on``: cell ids). Detected and declared
edges are kept apart, so the screen can say which is which.

The notebook screen uses this to show what a cell depends on, which outputs are stale after a
change, and which cells to run again, never re-running a cell with side effects without asking.
"""

from __future__ import annotations

import ast
import builtins
import re
from typing import Any

from . import magics

_BUILTINS = set(dir(builtins)) | {"display", "spark", "notebookutils", "mssparkutils", "dbutils", "sc", "sqlContext",
                                  "__name__", "__file__", "get_ipython"}
_READ_TABLE = {"read_table", "open_table", "table"}
_READ_FILE = {"read_csv", "read_parquet", "read_json", "path", "local_path"}
_FS_WRITE = {"put", "cp", "mv", "rm", "mkdirs", "append", "fastcp"}
_NETWORK = re.compile(r"\b(requests|httpx|urllib\.request|urllib3|aiohttp)\.(get|post|put|patch|delete|request|urlopen)\b")
_SQL_READ = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", re.I)
_SQL_WRITE = re.compile(r"\b(?:INSERT\s+(?:INTO|OVERWRITE)(?:\s+TABLE)?|CREATE\s+(?:OR\s+REPLACE\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?|"
                        r"MERGE\s+INTO|UPDATE|DELETE\s+FROM|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)\s+([A-Za-z_][\w.]*)", re.I)


def _table(name: str) -> str:
    name = name.strip("`[]\"")
    return (name if "." in name else f"dbo.{name}").lower()


def _const(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _sql(text: str, out: dict[str, Any], line: int) -> None:
    written = {_table(m.group(1)) for m in _SQL_WRITE.finditer(text)}
    for t in written:
        out["writes"].append({"kind": "table", "name": t, "line": line})
    for m in _SQL_READ.finditer(text):
        t = _table(m.group(1))
        if t not in written:
            out["reads"].append({"kind": "table", "name": t, "line": line})


class _Cell(ast.NodeVisitor):
    def __init__(self, out: dict[str, Any]):
        self.out = out
        self.defined: set[str] = set()
        self.scopes: list[set[str]] = []  # names local to the functions being read
        self.depth = 0  # inside a function body, names are read when it's called, not when it's defined
        self.now: set[str] = set()

    def define(self, name: str) -> None:
        if self.scopes:
            self.scopes[-1].add(name)
            return
        self.defined.add(name)
        if name not in self.out["defines"]:
            self.out["defines"].append(name)

    def use(self, name: str) -> None:
        if name in _BUILTINS or name in self.defined or any(name in s for s in self.scopes):
            return
        if not self.depth:
            self.now.add(name)
        if name not in self.out["uses"]:
            self.out["uses"].append(name)

    # names
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.use(node.id)
        else:
            self.define(node.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)  # the right side is read before the names are bound
        for t in node.targets:
            self.visit(t)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.use(node.target.id)
        self.visit(node.value)
        self.visit(node.target)

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.define(a.asname or a.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for a in node.names:
            if a.name == "*":
                self.out["dynamic"].append({"line": node.lineno, "text": f"from {node.module} import * can define any name"})
            else:
                self.define(a.asname or a.name)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        for d in getattr(node, "decorator_list", []):
            self.visit(d)
        args = node.args
        for d in args.defaults + [x for x in args.kw_defaults if x is not None]:
            self.visit(d)
        if not isinstance(node, ast.Lambda):
            self.define(node.name)
        local = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        local |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
        self.scopes.append(local)
        body = node.body if isinstance(node.body, list) else [node.body]
        # Names assigned anywhere in the function are local to it.
        for n in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
                local.add(n.id)
        self.depth += 1
        for stmt in body:
            self.visit(stmt)
        self.depth -= 1
        self.scopes.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for b in node.bases + node.decorator_list:
            self.visit(b)
        self.define(node.name)
        self.scopes.append(set())
        for stmt in node.body:
            self.visit(stmt)
        self.scopes.pop()

    def visit_comprehension_scope(self, node: ast.AST, elts: list[ast.AST]) -> None:
        targets = {n.id for g in node.generators for n in ast.walk(g.target) if isinstance(n, ast.Name)}  # type: ignore[attr-defined]
        self.scopes.append(targets)
        for g in node.generators:  # type: ignore[attr-defined]
            self.visit(g.iter)
            for cond in g.ifs:
                self.visit(cond)
        for e in elts:
            self.visit(e)
        self.scopes.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self.visit_comprehension_scope(node, [node.elt])

    visit_SetComp = visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.visit_comprehension_scope(node, [node.key, node.value])

    def visit_Global(self, node: ast.Global) -> None:
        for n in node.names:
            self.defined.add(n)
            if n not in self.out["defines"]:
                self.out["defines"].append(n)

    # data and side effects
    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        attr = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        owner = ast.unparse(f.value) if isinstance(f, ast.Attribute) else ""
        line = node.lineno
        first = _const(node.args[0]) if node.args else None
        if isinstance(f, ast.Name) and f.id in ("exec", "eval", "globals", "locals", "vars"):
            self.out["dynamic"].append({"line": line, "text": f"{f.id}() can read or define any name"})
        if attr == "write_table" and len(node.args) > 1 and _const(node.args[1]):
            self.out["writes"].append({"kind": "table", "name": _table(_const(node.args[1]) or ""), "line": line})
        elif attr == "saveAsTable" and first:
            self.out["writes"].append({"kind": "table", "name": _table(first), "line": line})
        elif attr in _READ_TABLE and first and (owner.endswith("lakehouse") or owner.endswith("read") or owner == "spark"):
            self.out["reads"].append({"kind": "table", "name": _table(first), "line": line})
        elif attr in _READ_FILE and first and first.startswith("Files"):
            self.out["reads"].append({"kind": "file", "name": first, "line": line})
        elif attr == "sql" and first:
            _sql(first, self.out, line)
        if owner.endswith(".fs") and attr in _FS_WRITE:
            target = _const(node.args[1]) if attr in ("cp", "mv", "fastcp") and len(node.args) > 1 else first
            self.out["effects"].append({"line": line, "text": f"changes files with notebookutils.fs.{attr}"})
            if target:
                self.out["writes"].append({"kind": "file", "name": target, "line": line})
        if owner.endswith("notebook") and attr == "run":
            self.out["dynamic"].append({"line": line, "text": "runs another notebook"})
        if attr in ("to_csv", "write_csv", "write_parquet", "to_parquet", "write_json", "to_json") and first:
            self.out["effects"].append({"line": line, "text": f"writes the file {first}"})
            self.out["writes"].append({"kind": "file", "name": first, "line": line})
        if isinstance(f, ast.Name) and f.id == "open" and len(node.args) > 1 and (_const(node.args[1]) or "r")[:1] in "wax":
            self.out["effects"].append({"line": line, "text": f"writes the file {first or 'a file'}"})
        self.generic_visit(node)


def _python(source: str) -> tuple[str | None, list[int]]:
    """The cell's Python (magics blanked, keeping line numbers, ``x = %sql ...`` kept as
    ``x = None``), or None for a %%sql cell."""
    head = source.lstrip()
    if head.startswith("%%sql"):
        return None, []
    found = []
    lines = source.split("\n")
    for i, ln in enumerate(lines):
        if re.match(r"^\s*%%", ln) and i == 0:
            lines[i] = ""
        elif re.match(r"^\s*(!|%%)", ln):
            found.append(i + 1)
            lines[i] = ""
    found += [m.line for m in magics.find("\n".join(lines))]
    return magics.blank("\n".join(lines)), sorted(found)


def analyze(source: str) -> dict[str, Any]:
    """What one cell defines, uses, reads, writes and changes, read from its code."""
    out: dict[str, Any] = {"parsed": True, "defines": [], "uses": [], "deferred": [], "reads": [], "writes": [],
                           "effects": [], "dynamic": []}
    code, lines = _python(source)
    if code is None:
        query = source.lstrip()[5:]
        _sql(query, out, 1)
        if magics.returns_rows(query):
            out["defines"].append("_sqldf")
    else:
        for line in lines:
            text = source.split("\n")[line - 1].strip()
            sql = next((m.rest for m in magics.find(text) if m.name == "sql"), None)
            if sql is not None:
                _sql(sql, out, line)
            elif text.startswith("%run"):
                out["dynamic"].append({"line": line, "text": f"{text} runs another notebook, which can define any name"})
            elif text.startswith(("%pip", "!pip", "%conda")):
                out["effects"].append({"line": line, "text": "installs packages"})
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            out["parsed"] = False
            out["dynamic"].append({"line": exc.lineno or 1, "text": f"doesn't parse: {exc.msg}"})
            return out
        visitor = _Cell(out)
        visitor.visit(tree)
        out["deferred"] = [n for n in out["uses"] if n not in visitor.now]  # used only inside function bodies
        for m in _NETWORK.finditer(code):
            out["effects"].append({"line": code.count("\n", 0, m.start()) + 1, "text": f"calls {m.group(0)} over the network"})
    for w in out["writes"]:
        if w["kind"] == "table":
            out["effects"].append({"line": w["line"], "text": f"writes the table {w['name']}"})
    return out


def graph(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Each code cell's analysis and the earlier cells it depends on.

    ``cells`` are the notebook screen's cells ({type, source, cell_id, meta}); cells are numbered
    from 1 among all cells, as the screen shows them. Each edge is {"cell_id", "n", "kind":
    variable|table|file|declared, "via": [names], "source": detected|declared}."""
    out = []
    last_def: dict[str, dict[str, Any]] = {}
    last_write: dict[tuple[str, str], dict[str, Any]] = {}
    later_defs: dict[str, list[int]] = {}
    infos = []
    for n, c in enumerate(cells, 1):
        if c.get("type") == "markdown" or not (c.get("source") or "").strip():
            infos.append(None)
            continue
        info = analyze(c.get("source") or "")
        info |= {"cell_id": c.get("cell_id"), "n": n}
        infos.append(info)
        for name in info["defines"]:
            later_defs.setdefault(name, []).append(n)
    ids = {c.get("cell_id"): n for n, c in enumerate(cells, 1)}
    for c, info in zip(cells, infos):
        if info is None:
            continue
        edges: dict[tuple[Any, str], dict[str, Any]] = {}

        def edge(src: dict[str, Any], kind: str, via: str, how: str = "detected") -> None:
            e = edges.setdefault((src["cell_id"], kind), {"cell_id": src["cell_id"], "n": src["n"], "kind": kind,
                                                          "via": [], "source": how})
            if via and via not in e["via"]:
                e["via"].append(via)

        unresolved = []
        for name in info["uses"]:
            if name in last_def:
                edge(last_def[name], "variable", name)
            elif later_defs.get(name):
                unresolved.append({"name": name, "text": f"{name} is defined only in a later cell ({later_defs[name][0]})"})
            else:
                unresolved.append({"name": name, "text": f"{name} isn't defined by any cell (an import, a typo, or "
                                                        "something defined dynamically)"})
        for r in info["reads"]:
            src = last_write.get((r["kind"], r["name"]))
            if src is not None:
                edge(src, r["kind"], r["name"])
        for dep in ((c.get("meta") or {}).get("depends_on") or []):
            n = ids.get(dep)
            if n is not None and n < info["n"] and infos[n - 1] is not None:
                edge(infos[n - 1], "declared", "", "declared")
        info["upstream"] = sorted(edges.values(), key=lambda e: e["n"])
        info["unresolved"] = unresolved
        for name in info["defines"]:
            last_def[name] = info
        for w in info["writes"]:
            last_write[(w["kind"], w["name"])] = info
        out.append(info)
    downstream: dict[int, list[int]] = {}
    for info in out:
        for e in info["upstream"]:
            downstream.setdefault(e["n"], []).append(info["n"])
    for info in out:
        info["downstream"] = sorted(set(downstream.get(info["n"], [])))
    return {"cells": out}
