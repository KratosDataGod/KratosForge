"""Query parameters on the SQL page (editor parity S17, Preview).

A query names a parameter as ``:name`` or ``{{name}}``. The page asks for each value and its type once,
and sends them with the run. The values never become SQL text: on this PC each is bound to DuckDB as a
typed parameter (``$name``), and on a warehouse each is sent to the driver as a ``?`` parameter. A name
inside a string, a comment or a quoted name isn't a parameter, and ``::`` (a cast) never starts one. A backslash
escapes a quote only in Spark SQL: in DuckDB ``'C:\\'`` is a whole string, and T-SQL has no escapes. DuckDB's
list slice ``l[:n]`` reads as the parameter ``n``: write it ``l[1:n]`` or ``list_slice(l, 1, n)``.

Types: ``text``, ``number``, ``date`` (2026-10-01), ``datetime`` (2026-10-01 14:30) and ``boolean``.
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
from typing import Any

TYPES = ("text", "number", "date", "datetime", "boolean")
MAX_PARAMS = 50
MAX_VALUE = 4000  # characters of one value
_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_COLON = re.compile(rf"(?<![:\w]):({_NAME})(?![\w:])")
_BRACES = re.compile(rf"\{{\{{\s*({_NAME})\s*\}}\}}")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def _inert(text: str, brackets: bool, backslash: bool = True) -> list[tuple[int, int]]:
    """Where strings, comments and quoted names are: (start, end) spans no parameter is looked for in."""
    spans, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("--", i):
            j = text.find("\n", i)
            end = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            end = n if j < 0 else j + 2
        elif ch in "'\"`" or (brackets and ch == "["):
            close = "]" if ch == "[" else ch
            j = i + 1
            while j < n:
                if text[j] == close:
                    if j + 1 < n and text[j + 1] == close:  # '' "" `` ]] stand for one
                        j += 2
                        continue
                    break
                if text[j] == "\\" and ch in "'\"" and backslash and not brackets:  # Spark strings take \' too
                    j += 2
                    continue
                j += 1
            end = min(j + 1, n)
        else:
            i += 1
            continue
        spans.append((i, end))
        i = end
    return spans


def find(text: str, tsql: bool = False, backslash: bool | None = None) -> list[tuple[int, int, str]]:
    """Each parameter in ``text``, in order: (start, end, name), outside strings, comments and quoted names.
    ``backslash``: whether a backslash escapes a quote (Spark SQL); by default, unless ``tsql``."""
    spans = _inert(text, brackets=tsql, backslash=not tsql if backslash is None else backslash)

    def free(at: int) -> bool:
        return not any(a <= at < b for a, b in spans)

    found = [(m.start(), m.end(), m.group(1)) for rx in (_COLON, _BRACES) for m in rx.finditer(text) if free(m.start())]
    return sorted(found)


def names(text: str, tsql: bool = False, backslash: bool | None = None) -> list[str]:
    return list(dict.fromkeys(name for _a, _b, name in find(text, tsql, backslash)))


def as_colons(text: str, tsql: bool = False, backslash: bool | None = None, dollar: bool = False) -> str:
    """``text`` with each ``{{name}}`` written ``:name``, which sqlglot reads as a placeholder.

    ``dollar`` (DuckDB SQL) writes ``$name `` instead: sqlglot reads DuckDB's ``SELECT :a`` as the prefix alias
    ``alias: expr``, so a parameter first in the select list failed to parse. The space after it keeps the next
    ``$`` from reading as the end of a ``$tag$`` string, as in ``{{a}},{{b}}``."""
    out, at = [], 0
    for a, b, name in find(text, tsql, backslash):
        out += [text[at:a], f"${name} " if dollar else f":{name}"]
        at = b
    return "".join(out) + text[at:]


def as_positional(text: str, values: dict[str, Any], tsql: bool = True) -> tuple[str, list[Any]]:
    """``text`` with each parameter a ``?``, and the values in that order, for a driver that binds by position."""
    out, ordered, at = [], [], 0
    for a, b, name in find(text, tsql):
        if name not in values:
            raise ValueError(f"Give a value for :{name} before running the query.")
        out += [text[at:a], "?"]
        ordered.append(values[name])
        at = b
    return "".join(out) + text[at:], ordered


def typed(params: Any) -> dict[str, Any]:
    """The page's ``[{name, type, value}]`` as {name: Python value of that type}. Raises ValueError naming the
    parameter whose value doesn't fit its type."""
    if params is None:
        return {}
    if not isinstance(params, list) or len(params) > MAX_PARAMS:
        raise ValueError(f"params must be a list of at most {MAX_PARAMS} parameters.")
    out: dict[str, Any] = {}
    for p in params:
        if not isinstance(p, dict):
            raise ValueError("Each parameter is {name, type, value}.")
        name, kind, value = p.get("name"), p.get("type", "text"), p.get("value")
        if not isinstance(name, str) or not re.fullmatch(_NAME, name):
            raise ValueError(f"{name!r} isn't a parameter name: use letters, digits and _.")
        if kind not in TYPES:
            raise ValueError(f"Parameter {name}'s type must be one of {', '.join(TYPES)}.")
        if not isinstance(value, str) or len(value) > MAX_VALUE:
            raise ValueError(f"Parameter {name}'s value must be text of at most {MAX_VALUE} characters.")
        out[name] = _value(name, kind, value)
    return out


def _value(name: str, kind: str, text: str) -> Any:
    v = text.strip()
    if kind == "text":
        return text
    if kind == "number":
        if not _NUMBER.fullmatch(v):
            raise ValueError(f"Parameter {name} is a number: write it as 42 or 3.5.")
        return int(v) if re.fullmatch(r"[+-]?\d+", v) else decimal.Decimal(v)
    if kind == "date":
        try:
            return dt.date.fromisoformat(v) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else _bad()
        except ValueError:
            raise ValueError(f"Parameter {name} is a date: write it as 2026-10-01.") from None
    if kind == "datetime":
        try:
            return dt.datetime.fromisoformat(v) if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?", v) else _bad()
        except ValueError:
            raise ValueError(f"Parameter {name} is a date and time: write it as 2026-10-01 14:30.") from None
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    raise ValueError(f"Parameter {name} is true or false: write true or false.")


def _bad() -> Any:
    raise ValueError("doesn't fit")
