"""Checks an RPC call's parameters against the ``Service`` method's signature before the call (#1173).

Python takes ``enabled=[1]`` for a ``bool`` or ``name=7`` for a ``str`` and carries on, so a wrong
type turned a switch on or reached a file path. Here each parameter's JSON type is checked against
the outermost type of its annotation, and the call is refused with a ``TypeError`` that names it.
Only the outer type is checked: what a list or object holds is the method's to check.
"""

from __future__ import annotations

import functools
import inspect
import types
import typing
from typing import Any, Callable

_NAMES = {str: "text", bool: "true or false", int: "a whole number", float: "a number", list: "a list",
          dict: "an object", type(None): "null"}


def _kinds(annotation: Any) -> tuple[type, ...] | None:
    """The JSON types ``annotation`` allows, or None for any (``Any``, or a type not listed here)."""
    if annotation is Any:
        return None
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        kinds: tuple[type, ...] = ()
        for arg in typing.get_args(annotation):
            inner = _kinds(arg)
            if inner is None:
                return None
            kinds += inner
        return kinds
    base = origin or annotation
    return (base,) if base in _NAMES else None


@functools.cache
def _rules(method: Callable[..., Any]) -> tuple[dict[str, tuple[type, ...] | None], frozenset[str], bool]:
    """Each parameter's allowed types, the required names, and whether it takes any other name."""
    hints = typing.get_type_hints(method)
    params, required, extra = {}, set(), False
    for p in inspect.signature(method).parameters.values():
        if p.name == "self" or p.kind is p.VAR_POSITIONAL:
            continue
        if p.kind is p.VAR_KEYWORD:
            extra = True
            continue
        params[p.name] = _kinds(hints.get(p.name, Any))
        if p.default is p.empty:
            required.add(p.name)
    return params, frozenset(required), extra


def _fits(value: Any, kinds: tuple[type, ...]) -> bool:
    if isinstance(value, bool):  # bool is an int in Python; JSON keeps them apart
        return bool in kinds
    if isinstance(value, int) and float in kinds:
        return True
    return any(isinstance(value, k) for k in kinds if k is not bool)


def _json_name(value: Any) -> str:
    return next((name for kind, name in _NAMES.items() if type(value) is kind), type(value).__name__)


def check(name: str, method: Callable[..., Any], params: Any) -> None:
    """Raise ``TypeError`` when ``params`` can't be the arguments of ``method``."""
    if not isinstance(params, dict):
        raise TypeError(f"{name}: params must be an object, not {_json_name(params)}")
    try:
        allowed, required, extra = _rules(getattr(method, "__func__", method))
    except (TypeError, ValueError, NameError):  # no signature to check against: the call decides
        return
    unknown = sorted(k for k in params if k not in allowed)
    if unknown and not extra:
        raise TypeError(f"{name}: unknown parameter {', '.join(unknown)}")
    missing = sorted(required - params.keys())
    if missing:
        raise TypeError(f"{name}: missing parameter {', '.join(missing)}")
    for key, value in params.items():
        kinds = allowed.get(key)
        if kinds is not None and not _fits(value, kinds):
            want = " or ".join(dict.fromkeys(_NAMES[k] for k in kinds))
            raise TypeError(f"{name}: {key} must be {want}, not {_json_name(value)}")
