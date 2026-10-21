"""A notebook's inputs: its parameter cell's values and the Databricks widgets it creates.

The notebook screen shows them in a bar above the cells. Values set there are kept with the
notebook (``fabric_desktop.inputs`` in its metadata) and never change its code: when a cell runs,
the app adds the lines ``suffix`` gives for that cell. For the parameter cell, that is an
assignment per changed value, run right after the cell, as Fabric does with a run's parameters.
Widget values are not code: each cell run on this PC starts with them (``Kernel.widget_values``),
so ``dbutils.widgets.get`` returns them, even in the cell that creates the widget.
"""

from __future__ import annotations

import ast
from typing import Any

# The types a parameter cell value can have for the bar to show and set it.
_TYPES = {str: "str", int: "int", float: "float", bool: "bool"}
_WIDGETS = ("text", "dropdown", "combobox", "multiselect")
_WIDGET_ARGS = ("name", "defaultValue", "choices", "label")
MAX_VALUE = 10_000


def _literal(node: ast.AST) -> tuple[bool, Any]:
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False, None


def _parse(source: str) -> ast.Module | None:
    head = source.lstrip()
    if head.startswith("%%"):
        return None
    try:
        return ast.parse("\n".join("" if ln.lstrip().startswith(("%", "!")) else ln for ln in source.split("\n")))
    except SyntaxError:
        return None


def _parameters(cell: dict[str, Any]) -> list[dict[str, Any]]:
    """``name = literal`` lines of the parameter cell, in order; the last assignment wins."""
    tree = _parse(cell.get("source", ""))
    found: dict[str, dict[str, Any]] = {}
    for node in tree.body if tree else []:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        else:
            continue
        ok, default = _literal(value)
        kind = _TYPES.get(type(default)) if ok else None
        if kind:
            found.pop(target, None)
            found[target] = {"name": target, "type": kind, "default": default}
    return list(found.values())


def _widgets(cell: dict[str, Any]) -> list[dict[str, Any]]:
    """``dbutils.widgets.<kind>(...)`` calls whose arguments are literals."""
    tree = _parse(cell.get("source", ""))
    out = []
    for node in ast.walk(tree) if tree else []:
        f = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(f, ast.Attribute) and f.attr in _WIDGETS
                and isinstance(f.value, ast.Attribute) and f.value.attr == "widgets"
                and isinstance(f.value.value, ast.Name) and f.value.value.id == "dbutils"):
            continue
        names = _WIDGET_ARGS if f.attr != "text" else ("name", "defaultValue", "label")
        args: dict[str, Any] = {}
        literal = True
        for key, arg in [*zip(names, node.args), *((k.arg, k.value) for k in node.keywords)]:
            ok, value = _literal(arg)
            literal = literal and ok
            args[key] = value
        if not literal or not isinstance(args.get("name"), str):
            continue
        choices = args.get("choices")
        out.append({"name": args["name"], "kind": f.attr, "default": str(args.get("defaultValue", "")),
                    "label": str(args.get("label") or args["name"]),
                    "choices": [str(c) for c in choices] if isinstance(choices, (list, tuple)) else None,
                    "line": node.lineno})
    return out


def _python_value(kind: str, text: str) -> str:
    """The Python literal for a value typed in the bar, or ValueError saying what's wrong."""
    if kind == "str":
        return repr(text)
    if kind == "bool":
        if text not in ("True", "False"):
            raise ValueError("Pick True or False.")
        return text
    if kind == "int":
        try:
            return repr(int(text.strip()))
        except ValueError:
            raise ValueError("Type a whole number.") from None
    try:
        return repr(float(text.strip()))
    except ValueError:
        raise ValueError("Type a number.") from None


def check_values(values: Any) -> dict[str, dict[str, str]]:
    """``{"parameters": {name: text}, "widgets": {name: text}}``, as the bar sends it."""
    if not isinstance(values, dict) or set(values) - {"parameters", "widgets"}:
        raise ValueError('values is {"parameters": {...}, "widgets": {...}}')
    out: dict[str, dict[str, str]] = {}
    for group in ("parameters", "widgets"):
        given = values.get(group) or {}
        if not isinstance(given, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in given.items()):
            raise ValueError(f"{group} maps names to text values")
        if any(len(v) > MAX_VALUE for v in given.values()):
            raise ValueError(f"A value is longer than {MAX_VALUE:,} characters.")
        if group == "parameters" and not all(k.isidentifier() for k in given):
            raise ValueError("Parameter names are Python variable names")
        out[group] = dict(given)
    return out


def inputs(cells: list[dict[str, Any]], values: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the bar shows, and the code to add after each cell (``suffix``, by cell id).

    A value that equals the code's default, or names nothing the notebook has, is left out."""
    values = check_values(values or {})
    params_cell = next((c for c in cells if c.get("type") == "code"
                        and "parameters" in ((c.get("meta") or {}).get("tags") or [])), None)
    suffix: dict[str, str] = {}
    parameters = []
    if params_cell is not None:
        lines = []
        for p in _parameters(params_cell):
            default = str(p["default"]) if p["type"] != "str" else p["default"]
            text = values["parameters"].get(p["name"])
            row = {**p, "default": default, "value": default if text is None else text, "error": None}
            if text is not None and text != default:
                try:
                    lines.append(f"{p['name']} = {_python_value(p['type'], text)}")
                except ValueError as exc:
                    row["error"] = str(exc)
            parameters.append(row)
        if lines:
            suffix[params_cell["cell_id"]] = "\n".join(["# Values from the Parameters bar", *lines])
    widgets = []
    seen: set[str] = set()
    for cell in cells:
        if cell.get("type") != "code":
            continue
        for w in _widgets(cell):
            if w["name"] in seen:
                continue
            seen.add(w["name"])
            text = values["widgets"].get(w["name"])
            row = {**w, "cell": cell["cell_id"], "value": w["default"] if text is None else text, "error": None}
            if text is not None and w["kind"] in ("dropdown", "multiselect") and w["choices"] is not None:
                picked = text.split(",") if w["kind"] == "multiselect" and text else [text]
                if any(p not in w["choices"] for p in picked):
                    row["error"] = "Pick from the choices."
            widgets.append(row)
    return {"parameters": parameters, "widgets": widgets, "parameter_cell": params_cell["cell_id"] if params_cell else None,
            "suffix": suffix}
