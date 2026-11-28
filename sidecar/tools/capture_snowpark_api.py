"""Capture Snowpark Python's API signatures for fabric_desktop/snowpark_api.json (SF-007, option C).

Run by hand, never in CI, and never in the sidecar's own environment: Snowpark goes into a scratch
virtual environment of its own, used only for this, when the pinned version changes
(snowpark_api.SNOWPARK_VERSION):

    uv venv -p 3.12 snowpark-capture
    VIRTUAL_ENV=snowpark-capture uv pip install "snowflake-snowpark-python[localtest]==1.58.0"
    snowpark-capture/bin/python -I tools/capture_snowpark_api.py fabric_desktop/snowpark_api.json

It imports Snowpark and reads signatures with ``inspect``. It opens no session, local or remote, and
reaches no network. For each public class reachable from ``snowflake.snowpark`` it keeps each public
member: whether it is a method, a property or an attribute, the keywords a method takes, how many
positional arguments it takes, whether it takes ``**kwargs``, and the Snowpark class it returns when
its annotation names exactly one. For the modules a cell imports from (functions, types, window,
context and the package itself) it keeps each public name the same way.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any

import snowflake.snowpark as sp

# The modules a cell imports Snowpark names from. Others are internal.
MODULES = ("snowflake.snowpark", "snowflake.snowpark.functions", "snowflake.snowpark.types",
           "snowflake.snowpark.window", "snowflake.snowpark.context", "snowflake.snowpark.exceptions",
           "snowflake.snowpark.files", "snowflake.snowpark.mock")
# Modules whose classes a return annotation may name (DataFrameReader is never exported by name).
CLASS_MODULES = MODULES + ("snowflake.snowpark.session", "snowflake.snowpark.dataframe", "snowflake.snowpark.column",
                           "snowflake.snowpark.table", "snowflake.snowpark.row", "snowflake.snowpark.dataframe_reader",
                           "snowflake.snowpark.dataframe_writer", "snowflake.snowpark.relational_grouped_dataframe",
                           "snowflake.snowpark.dataframe_na_functions", "snowflake.snowpark.dataframe_stat_functions",
                           "snowflake.snowpark.dataframe_analytics_functions", "snowflake.snowpark.dataframe_ai_functions",
                           "snowflake.snowpark.file_operation", "snowflake.snowpark.udf", "snowflake.snowpark.udtf",
                           "snowflake.snowpark.udaf", "snowflake.snowpark.stored_procedure", "snowflake.snowpark.async_job",
                           "snowflake.snowpark.query_history", "snowflake.snowpark.lineage")
# What an attribute a class doesn't define gives (__getattr__ has no annotation). In 1.58.0's source
# DataFrame's (and so Table's) gives a Column for a column's name (dataframe.py), and Row's gives a
# field's value, of any type ("*").
GETATTR = {"DataFrame": "Column", "Table": "Column", "Row": "*"}
_CONTAINERS = re.compile(r"\b(List|list|Dict|dict|Iterator|Iterable|Tuple|tuple|Sequence|Set|set|Generator)\[")
# A dotted name's last part: "Session.SessionBuilder" and "snowflake.snowpark.Table" name one class each.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def class_index() -> dict[str, type]:
    found: dict[str, type] = {}
    for name in CLASS_MODULES:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        for attr, value in vars(module).items():
            if inspect.isclass(value) and not attr.startswith("_") and _public(value):
                found.setdefault(value.__name__, value)
                for inner_name, inner in vars(value).items():
                    if inspect.isclass(inner) and not inner_name.startswith("_"):
                        found.setdefault(inner.__name__, inner)
    return found


def _public(cls: type) -> bool:
    """A class of Snowpark's public API: not one of its internal plan or expression classes."""
    return cls.__module__.startswith("snowflake.snowpark") and "._internal" not in cls.__module__ \
        and not cls.__module__.startswith("snowflake.snowpark.mock._")


INDEX = class_index()


def returns(annotation: Any, owner: str | None) -> str | None:
    """The one Snowpark class an annotation names, or None (several, a container, or none)."""
    if annotation is inspect.Signature.empty:
        return None
    text = annotation.__name__ if inspect.isclass(annotation) else str(annotation)
    if _CONTAINERS.search(text):
        return None
    tokens = [t.rsplit(".", 1)[-1] for t in _IDENT.findall(text)]
    names = {t for t in tokens if t in INDEX}
    if "Self" in tokens and owner:
        names.add(owner)
    return names.pop() if len(names) == 1 else None


def signature(func: Any, owner: str | None, bound: bool) -> dict[str, Any]:
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return {"kind": "method", "params": None}
    params = list(sig.parameters.values())
    if bound and params and params[0].kind in (params[0].POSITIONAL_ONLY, params[0].POSITIONAL_OR_KEYWORD):
        params = params[1:]
    # Snowpark's own private keywords (_emit_ast, _ast_stmt) are left out: a cell never passes them.
    keywords = [p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and not p.name.startswith("_")]
    varargs = any(p.kind == p.VAR_POSITIONAL for p in params)
    positional = sum(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and not p.name.startswith("_") for p in params)
    out: dict[str, Any] = {"kind": "method", "params": keywords, "varkw": any(p.kind == p.VAR_KEYWORD for p in params),
                           "max_positional": None if varargs else positional}
    ret = returns(sig.return_annotation, owner)
    if ret:
        out["returns"] = ret
    return out


def member(cls: type, name: str) -> dict[str, Any]:
    raw = inspect.getattr_static(cls, name)
    owner = cls.__name__
    if isinstance(raw, property):
        ret = returns(inspect.signature(raw.fget).return_annotation, owner) if raw.fget else None
        return {"kind": "property", **({"returns": ret} if ret else {})}
    if isinstance(raw, staticmethod):
        return signature(raw.__func__, owner, bound=False)
    if isinstance(raw, classmethod):
        return signature(raw.__func__, owner, bound=True)
    if inspect.isfunction(raw):
        return signature(raw, owner, bound=True)
    if inspect.isclass(raw):
        return {"kind": "class", "class": raw.__name__}
    if type(raw).__name__ in INDEX and type(raw).__module__.startswith("snowflake.snowpark"):
        return {"kind": "attribute", "returns": type(raw).__name__}
    if callable(raw):
        return signature(raw, owner, bound=False)
    return {"kind": "attribute"}


def capture_class(cls: type) -> dict[str, Any]:
    members = {n: member(cls, n) for n in sorted(dir(cls)) if not n.startswith("_")}
    entry: dict[str, Any] = {"module": cls.__module__, "members": members}
    init = signature(vars(cls)["__init__"], cls.__name__, bound=True) if "__init__" in vars(cls) else None
    if init and init.get("params") is not None:
        entry["init"] = {k: v for k, v in init.items() if k != "kind"}
    if cls.__name__ in GETATTR:
        entry["getattr"] = GETATTR[cls.__name__]
    return entry


def module_entry(name: str) -> dict[str, Any]:
    module = importlib.import_module(name)
    public = getattr(module, "__all__", None) or [n for n in dir(module) if not n.startswith("_")]
    out: dict[str, Any] = {}
    for attr in sorted(set(public)):
        value = getattr(module, attr, None)
        if inspect.ismodule(value):
            if value.__name__.startswith("snowflake.snowpark") and "._internal" not in value.__name__:
                out[attr] = {"kind": "module", "module": value.__name__}
        elif inspect.isclass(value):
            if _public(value):
                out[attr] = {"kind": "class", "class": value.__name__}
        elif callable(value) and str(getattr(value, "__module__", "")).startswith("snowflake.snowpark") \
                and "._internal" not in str(value.__module__):
            out[attr] = {**signature(value, None, bound=False), "kind": "function"}
    return out


def main(target: Path) -> None:
    modules = {name: module_entry(name) for name in MODULES}
    wanted = {e["class"] for m in modules.values() for e in m.values() if e["kind"] == "class"}
    classes: dict[str, Any] = {}
    while wanted:
        name = wanted.pop()
        if name in classes or name not in INDEX:
            continue
        classes[name] = capture_class(INDEX[name])
        for m in classes[name]["members"].values():
            for key in ("returns", "class"):
                if m.get(key) and m[key] not in classes:
                    wanted.add(m[key])
        if classes[name].get("getattr", "*") != "*":
            wanted.add(classes[name]["getattr"])
    data = {"snowpark": sp.__version__, "python": "%d.%d" % sys.version_info[:2],
            "classes": dict(sorted(classes.items())), "modules": modules}
    target.write_text(json.dumps(data, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    functions = sum(e["kind"] == "function" for e in modules["snowflake.snowpark.functions"].values())
    print(f"Snowpark {sp.__version__}: {len(classes)} classes, {functions} functions -> {target}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "fabric_desktop" / "snowpark_api.json")
