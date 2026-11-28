"""Snowpark API compatibility check (Preview, SF-007 option C).

Reads a Python cell's code without running it and checks each Snowpark call it can place against the
signatures of one pinned Snowpark Python version (``snowpark_api.json``, written by hand with
tools/capture_snowpark_api.py from that version's wheel, in a scratch environment; Snowpark is not a
dependency of the app). It reports:

- ``unknown_method``: an attribute or method a Snowpark class doesn't have in that version;
- ``unknown_function``: a name a Snowpark module (functions, types, window...) doesn't have;
- ``unknown_keyword``: a keyword the method or function doesn't take;
- ``too_many_arguments``: more positional arguments than it takes;
- ``not_locally_checkable``: calls in the families that need Snowflake's own servers (Cortex AI,
  external access and secrets, stages, pandas on Snowflake), flagged before anyone tries them there.

It is an API compatibility check only. It says nothing about results, and nothing here executes the
cell or reaches Snowflake. Python can't always tell which object a call is on: a call it can't place
is counted (``not_placed``), never guessed at. ``session`` is taken to be a Snowpark Session, as in a
Snowflake notebook, until the cell binds the name to something else.
"""

from __future__ import annotations

import ast
import functools
import json
import re
from pathlib import Path
from typing import Any

DATA = Path(__file__).with_name("snowpark_api.json")
SNOWPARK_VERSION = "1.58.0"
LABEL = f"API compatibility check against Snowpark Python {SNOWPARK_VERSION}"
NOTE = ("Reads the cell's code without running it, and checks that each Snowpark call, method and keyword "
        f"it can place exists in Snowpark Python {SNOWPARK_VERSION}. It says nothing about results.")
FUNCTIONS = "snowflake.snowpark.functions"
FAMILIES = {
    "cortex": "Cortex AI functions work only on Snowflake's servers, so they can't be checked on this PC.",
    "external_access": "External access integrations and secrets exist only in a Snowflake account, so they "
                       "can't be checked on this PC.",
    "stages": "Stage files live in a Snowflake account, so stage reads, writes and imports can't be checked on "
              "this PC beyond their names and keywords.",
    "pandas_on_snowflake": "pandas on Snowflake needs a Snowflake session, so it can't be checked on this PC.",
}
UNKNOWN = ("unknown_method", "unknown_function", "unknown_keyword", "too_many_arguments")
_CORTEX_FUNCTION = re.compile(r"^(ai_|snowflake_cortex_)")
_CORTEX_CLASSES = {"DataFrameAIFunctions"}
_STAGE_CLASSES = {"FileOperation", "SnowflakeFile"}
_STAGE_MEMBERS = {("DataFrameWriter", "copy_into_location"), ("DataFrame", "copy_into_table"),
                  ("Table", "copy_into_table"), ("Session", "add_import"), ("Session", "get_session_stage")}
_EXTERNAL_KEYWORDS = {"external_access_integrations", "secrets"}
_STAGE_KEYWORDS = {"stage_location"}
_MAGIC = re.compile(r"^\s*[%!]")

Type = tuple[str, ...] | None


@functools.cache
def api() -> dict[str, Any]:
    """The pinned signatures (snowpark_api.json)."""
    data: dict[str, Any] = json.loads(DATA.read_text(encoding="utf-8"))
    return data


@functools.cache
def _method_names() -> frozenset[str]:
    return frozenset(n for c in api()["classes"].values() for n, m in c["members"].items() if m["kind"] == "method")


def _is_module_prefix(path: str) -> bool:
    return any(m == path or m.startswith(path + ".") for m in api()["modules"])


class _Checker:
    def __init__(self) -> None:
        self.env: dict[str, Type] = {"session": ("instance", "Session")}
        self.findings: dict[tuple[int, int, str, str], dict[str, Any]] = {}
        self.checked = 0
        self.not_placed = 0

    # ---- findings

    def _add(self, node: ast.AST, kind: str, name: str, message: str, family: str = "") -> None:
        line, col = getattr(node, "lineno", 0), getattr(node, "col_offset", 0)
        entry = {"line": line, "col": col + 1, "kind": kind, "name": name, "message": message}
        if family:
            entry["family"] = family
        self.findings.setdefault((line, col, kind, family or name), entry)

    def _flag(self, node: ast.AST, family: str, name: str) -> None:
        self._add(node, "not_locally_checkable", name, FAMILIES[family], family)

    # ---- imports

    def _import(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                self._import_flags(node, alias.name)
                if alias.asname:
                    self.env[alias.asname] = ("module", alias.name) if _is_module_prefix(alias.name) else None
                else:
                    root = alias.name.split(".")[0]
                    self.env[root] = ("module", root) if _is_module_prefix(root) else None
            return
        module = node.module or ""
        if node.level:
            return
        self._import_flags(node, module)
        entry = api()["modules"].get(module)
        for alias in node.names:
            name = alias.asname or alias.name
            if alias.name == "*":
                continue
            self._import_flags(node, f"{module}.{alias.name}")
            full = f"{module}.{alias.name}"
            if _is_module_prefix(full):
                self.env[name] = ("module", full)
            elif entry is not None:
                self.env[name] = self._module_member(module, alias.name, node)
            else:
                self.env[name] = None

    def _import_flags(self, node: ast.AST, module: str) -> None:
        if module == "modin" or module.startswith(("modin.", "snowflake.snowpark.modin")):
            self._flag(node, "pandas_on_snowflake", module)
        elif module == "snowflake.cortex" or module.startswith("snowflake.cortex."):
            self._flag(node, "cortex", module)
        elif module == "_snowflake" or module.startswith("_snowflake."):
            self._flag(node, "external_access", module)
        elif module.startswith(FUNCTIONS + ".") and _CORTEX_FUNCTION.match(module.rsplit(".", 1)[1]):
            self._flag(node, "cortex", module.rsplit(".", 1)[1])

    def _module_member(self, module: str, name: str, node: ast.AST) -> Type:
        entry = api()["modules"][module].get(name)
        if entry is None:
            self._add(node, "unknown_function", name, f"{module} has no {name} in Snowpark Python {SNOWPARK_VERSION}.")
            return None
        if module == FUNCTIONS and _CORTEX_FUNCTION.match(name):
            self._flag(node, "cortex", name)
        if entry["kind"] == "class":
            return ("class", entry["class"])
        if entry["kind"] == "module":
            return ("module", entry["module"])
        return ("function", module, name)

    # ---- expressions

    def infer(self, node: ast.AST | None) -> Type:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
        if isinstance(node, ast.Attribute):
            return self._attribute(self.infer(node.value), node)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                func = self._attribute(self.infer(node.func.value), node.func, called=True)
            else:
                func = self.infer(node.func)
            for arg in node.args:
                self.infer(arg)
            for kw in node.keywords:
                self.infer(kw.value)
            return self._call(func, node)
        if isinstance(node, ast.Subscript):
            base = self.infer(node.value)
            self.infer(node.slice)
            if base and base[0] == "instance" and api()["classes"].get(base[1], {}).get("getattr") not in (None, "*"):
                return ("instance", api()["classes"][base[1]]["getattr"])
            return None
        if isinstance(node, (ast.BinOp, ast.Compare, ast.BoolOp, ast.UnaryOp)):
            parts = [self.infer(n) for n in ast.iter_child_nodes(node) if isinstance(n, ast.expr)]
            return ("instance", "Column") if ("instance", "Column") in parts else None
        if isinstance(node, ast.Lambda):
            saved = dict(self.env)
            for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                self.env[a.arg] = None
            self.infer(node.body)
            self.env = saved
            return None
        self._walk(node)
        return None

    def _attribute(self, base: Type, node: ast.Attribute, called: bool = False) -> Type:
        """What ``base.attr`` is. ``called``: it is called, so a DataFrame's column (which its
        __getattr__ gives for any other name) can't be what is meant."""
        attr = node.attr
        if base is None or attr.startswith("__"):
            return None
        kind = base[0]
        if kind == "module":
            path = f"{base[1]}.{attr}"
            if _is_module_prefix(path):
                self._import_flags(node, path)
                return ("module", path)
            if base[1] in api()["modules"]:
                return self._module_member(base[1], attr, node)
            return None
        if kind in ("class", "instance"):
            cls = api()["classes"].get(base[1])
            if cls is None:
                return None
            member = cls["members"].get(attr)
            if member is None:
                fallback = cls.get("getattr")
                if kind == "instance" and fallback and not (called and fallback != "*"):
                    return None if fallback == "*" else ("instance", fallback)
                if isinstance(node.ctx, ast.Load):
                    self._add(node, "unknown_method", attr,
                              f"{base[1]} has no {attr} in Snowpark Python {SNOWPARK_VERSION}.")
                return None
            if base[1] in _CORTEX_CLASSES:
                self._flag(node, "cortex", f"{base[1]}.{attr}")
            if base[1] in _STAGE_CLASSES or (base[1], attr) in _STAGE_MEMBERS:
                self._flag(node, "stages", f"{base[1]}.{attr}")
            if member["kind"] == "method":
                return ("method", base[1], attr)
            if member["kind"] == "class":
                return ("class", member["class"])
            return ("instance", member["returns"]) if member.get("returns") else None
        return None

    def _call(self, func: Type, node: ast.Call) -> Type:
        if func is None or func[0] == "instance":
            if func is None and isinstance(node.func, ast.Attribute) and node.func.attr in _method_names():
                self.not_placed += 1
            return None
        if func[0] == "module":
            return None
        if func[0] == "method":
            owner, name = func[1], func[2]
            sig = api()["classes"][owner]["members"][name]
            result: Type = ("instance", sig["returns"]) if sig.get("returns") else None
        elif func[0] == "function":
            owner, name = func[1], func[2]
            sig = api()["modules"][owner][name]
            result = ("instance", sig["returns"]) if sig.get("returns") else None
        else:  # a class: its constructor
            owner, name = func[1], "__init__"
            sig = api()["classes"].get(owner, {}).get("init") or {"params": None}
            result = ("instance", owner)
        self.checked += 1
        self._arguments(node, owner, name, sig)
        self._call_flags(node, owner, name)
        return result

    def _arguments(self, node: ast.Call, owner: str, name: str, sig: dict[str, Any]) -> None:
        shown = owner.rsplit(".", 1)[-1] if name == "__init__" else f"{owner.rsplit('.', 1)[-1]}.{name}"
        params = sig.get("params")
        if params is None:
            return
        for kw in node.keywords:
            if kw.arg is not None and kw.arg not in params and not sig.get("varkw"):
                self._add(kw, "unknown_keyword", kw.arg,
                          f"{shown} takes no keyword {kw.arg} in Snowpark Python {SNOWPARK_VERSION}.")
        most = sig.get("max_positional")
        given = len(node.args)
        if most is not None and given > most and not any(isinstance(a, ast.Starred) for a in node.args):
            self._add(node, "too_many_arguments", name,
                      f"{shown} takes at most {most} positional argument{'s' if most != 1 else ''} in Snowpark "
                      f"Python {SNOWPARK_VERSION}; this call gives {given}.")

    def _call_flags(self, node: ast.Call, owner: str, name: str) -> None:
        for kw in node.keywords:
            if kw.arg in _EXTERNAL_KEYWORDS:
                self._flag(kw, "external_access", kw.arg)
            elif kw.arg in _STAGE_KEYWORDS:
                self._flag(kw, "stages", kw.arg)
        values = [*node.args, *(k.value for k in node.keywords)]
        if any(isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.startswith("@") for v in values):
            self._flag(node, "stages", f"{owner.rsplit('.', 1)[-1]}.{name}")
        if owner == FUNCTIONS and name in ("call_function", "call_builtin", "builtin", "function") and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and (
                    _CORTEX_FUNCTION.match(first.value.lower()) or first.value.lower().startswith("snowflake.cortex.")):
                self._flag(node, "cortex", first.value)

    # ---- statements

    def _bind(self, target: ast.AST, value: Type) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for t in target.elts:
                self._bind(t.value if isinstance(t, ast.Starred) else t, None)
        elif isinstance(target, (ast.Attribute, ast.Subscript)):
            self.infer(target.value)

    def statement(self, node: ast.stmt) -> None:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            self._import(node)
        elif isinstance(node, ast.Assign):
            value = self.infer(node.value)
            for target in node.targets:
                self._bind(target, value)
        elif isinstance(node, ast.AnnAssign):
            value = self.infer(node.value)
            declared = self.infer(node.annotation)
            self._bind(node.target, ("instance", declared[1]) if declared and declared[0] == "class" else value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                self.infer(d)
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                self.infer(default)
            saved = dict(self.env)
            for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                declared = self.infer(a.annotation) if a.annotation else None
                self.env[a.arg] = ("instance", declared[1]) if declared and declared[0] == "class" else None
            for s in node.body:
                self.statement(s)
            self.env = saved
            self.env[node.name] = None
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                value = self.infer(item.context_expr)
                if item.optional_vars is not None:
                    self._bind(item.optional_vars, value)
            for s in node.body:
                self.statement(s)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self.infer(node.iter)
            self._bind(node.target, None)
            for s in [*node.body, *node.orelse]:
                self.statement(s)
        else:
            self._walk(node)

    def _walk(self, node: ast.AST) -> None:
        """Every expression and statement under ``node``, in order (if, try, while, class, match...)."""
        for _, value in ast.iter_fields(node):
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, ast.stmt):
                    self.statement(item)
                elif isinstance(item, ast.expr):
                    self.infer(item)
                elif isinstance(item, ast.AST):
                    self._walk(item)


def _source(code: str) -> str:
    """The cell without IPython magics and shell lines, keeping line numbers."""
    return "\n".join("" if _MAGIC.match(line) else line for line in code.splitlines())


TOO_DEEP = ("This cell is too deeply nested to check: it chains or nests too many calls or attributes. "
            "Split the chain into steps to check it.")


def check(code: str) -> dict[str, Any]:
    """The API compatibility check of one Python cell. ``compatible`` is false when a call, method or
    keyword isn't in the pinned version; flagged families don't change it."""
    out: dict[str, Any] = {"check": "api_compatibility", "label": LABEL, "snowpark": SNOWPARK_VERSION, "note": NOTE}
    if code.lstrip().startswith("%%"):
        return out | {"compatible": None, "checked": 0, "not_placed": 0, "findings": [], "families": [],
                      "error": "This cell starts with a cell magic, so it isn't Python to check."}
    try:
        tree = ast.parse(_source(code), "<cell>", "exec")
    except SyntaxError as exc:
        return out | {"compatible": None, "checked": 0, "not_placed": 0, "findings": [], "families": [],
                      "error": f"The cell can't be read as Python (line {exc.lineno}: {exc.msg})."}
    except RecursionError:  # the parser has a depth limit of its own
        return out | {"compatible": None, "checked": 0, "not_placed": 0, "findings": [], "families": [],
                      "error": TOO_DEEP}
    checker = _Checker()
    try:
        for node in tree.body:
            checker.statement(node)
    except RecursionError:
        # The checker reads one level per attribute or call, so a chain of about a thousand or more
        # (df.a.a... or .filter().filter()...) is deeper than Python allows.
        return out | {"compatible": None, "checked": 0, "not_placed": 0, "findings": [], "families": [],
                      "error": TOO_DEEP}
    findings = sorted(checker.findings.values(), key=lambda f: (f["line"], f["col"], f["kind"]))
    return out | {
        "compatible": not any(f["kind"] in UNKNOWN for f in findings),
        "checked": checker.checked, "not_placed": checker.not_placed, "findings": findings,
        "families": sorted({f["family"] for f in findings if f.get("family")}),
    }
