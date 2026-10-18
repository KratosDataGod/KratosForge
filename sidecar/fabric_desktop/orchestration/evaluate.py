"""Evaluate pipeline expressions for a local run (ORCH-003), with a fixed list of functions.

This is not a general-purpose evaluator: it parses the tokens ``expressions.tokens`` reads, and
calls only the functions in ``FUNCTIONS`` (each written here, none from Python's ``eval``). An
expression that calls anything else stops the activity with ``ExpressionError`` naming the
function, instead of guessing. The functions follow Microsoft's Data Factory expression
documentation; they have not been compared with Fabric's results (docs/orchestration/local-runs.md
lists the differences known so far).
"""

from __future__ import annotations

import json
import math
import uuid
from collections import ChainMap
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .expressions import tokens


class ExpressionError(Exception):
    """An expression that can't be evaluated here; the message says why."""


@dataclass
class Scope:
    """What an expression can see while a run is going."""
    parameters: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    activities: dict[str, dict[str, Any]] = field(default_factory=dict)   # name -> {output, status, error}
    system: dict[str, Any] = field(default_factory=dict)                  # RunId, Pipeline, TriggerTime ...
    items: list[Any] = field(default_factory=list)                        # innermost ForEach item last
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def child(self, item: Any) -> "Scope":
        """The scope inside one ForEach iteration: one more item, and activity results of its own
        (each iteration's activities have the same names), over the enclosing ones."""
        return Scope(self.parameters, self.variables, ChainMap({}, self.activities), self.system,  # type: ignore[arg-type]
                     self.items + [item], self.now)

    def layer(self) -> "Scope":
        """The scope of one Until iteration: activity results of its own, the same items."""
        return Scope(self.parameters, self.variables, ChainMap({}, self.activities), self.system,  # type: ignore[arg-type]
                     list(self.items), self.now)


def evaluate(value: Any, scope: Scope) -> Any:
    """A property value with its expressions evaluated: an Expression object, a string starting
    with @, a string with @{...} parts, or a list or object of those. Other values come back as is."""
    if isinstance(value, dict):
        if value.get("type") == "Expression" and isinstance(value.get("value"), str):
            return _string(value["value"], scope)
        return {k: evaluate(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [evaluate(v, scope) for v in value]
    if isinstance(value, str):
        return _string(value, scope)
    return value


def _string(text: str, scope: Scope) -> Any:
    if text.startswith("@@"):
        return text[1:]
    if text.startswith("@") and not text.startswith("@{"):
        return expression(text[1:], scope)
    if "@{" not in text:
        return text
    out, i = [], 0
    while True:
        start = text.find("@{", i)
        if start < 0:
            out.append(text[i:])
            break
        if start > 0 and text[start - 1] == "@":
            out.append(text[i:start - 1] + "@{")
            i = start + 2
            continue
        out.append(text[i:start])
        end = _closing(text, start + 2)
        out.append(_text(expression(text[start + 2:end], scope)))
        i = end + 1
    return "".join(out)


def _closing(text: str, i: int) -> int:
    depth, quoted = 1, False
    while i < len(text):
        ch = text[i]
        if quoted:
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 1
                else:
                    quoted = False
        elif ch == "'":
            quoted = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if not depth:
                return i
        i += 1
    raise ExpressionError("an @{ isn't closed with }")


def expression(text: str, scope: Scope) -> Any:
    """One expression without its leading @."""
    toks, problem = tokens(text)
    if problem:
        raise ExpressionError(problem)
    parser = _Parser(toks, scope)
    value = parser.value()
    if parser.i != len(toks):
        raise ExpressionError(f"unexpected {toks[parser.i][1]!r} after the expression")
    return value


class _Parser:
    def __init__(self, toks: list[tuple[str, str]], scope: Scope):
        self.toks, self.i, self.scope = toks, 0, scope

    def peek(self, offset: int = 0) -> str:
        j = self.i + offset
        return self.toks[j][0] if j < len(self.toks) else ""

    def take(self, kind: str) -> str:
        if self.peek() != kind:
            got = self.toks[self.i][1] if self.i < len(self.toks) else "the end"
            raise ExpressionError(f"expected {kind!r} but found {got!r}")
        value = self.toks[self.i][1]
        self.i += 1
        return value

    def value(self) -> Any:
        kind = self.peek()
        if kind == "string":
            out: Any = self.take("string")
        elif kind == "number":
            raw = self.take("number")
            out = float(raw) if "." in raw else int(raw)
        elif kind == "ident":
            name = self.take("ident")
            if self.peek() != "(":
                literal = {"true": True, "false": False, "null": None}.get(name.lower(), ...)
                if literal is ...:
                    raise ExpressionError(f"{name} isn't a value; did you mean '{name}'?")
                out = literal
            else:
                self.take("(")
                args: list[Any] = []
                while self.peek() != ")":
                    args.append(self.value())
                    if self.peek() == ",":
                        self.take(",")
                    elif self.peek() != ")":
                        raise ExpressionError(f"expected , or ) in {name}()")
                self.take(")")
                out = call(name, args, self.scope)
        else:
            raise ExpressionError("an expression is missing" if not kind else f"unexpected {kind!r}")
        return self.members(out)

    def members(self, out: Any) -> Any:
        while True:
            safe = False
            if self.peek() == "?" and self.peek(1) in (".", "["):
                self.take("?")
                safe = True
            if self.peek() == "." and self.peek(1) == "ident":
                self.take(".")
                out = _member(out, self.take("ident"), safe)
            elif self.peek() == "[":
                self.take("[")
                key = self.value()
                self.take("]")
                out = _member(out, key, safe)
            else:
                return out


def _member(value: Any, key: Any, safe: bool) -> Any:
    if value is None:
        if safe:
            return None
        raise ExpressionError(f"can't read {key!r} of null")
    if isinstance(value, list):
        if isinstance(key, bool) or not isinstance(key, int):
            raise ExpressionError(f"an array index must be a whole number, not {key!r}")
        if 0 <= key < len(value):
            return value[key]
        if safe:
            return None
        raise ExpressionError(f"index {key} is outside an array of {len(value)}")
    if isinstance(value, dict):
        if key in value:
            return value[key]
        lowered = {str(k).lower(): k for k in value}   # property names are case-insensitive
        if str(key).lower() in lowered:
            return value[lowered[str(key).lower()]]
        if safe:
            return None
        raise ExpressionError(f"there is no property {key!r}")
    if safe:
        return None
    raise ExpressionError(f"can't read {key!r} of a {type(value).__name__}")


# ---- functions

def _text(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "True" if v else "False"
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"))
    return str(v)


def _num(v: Any, fn: str) -> int | float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ExpressionError(f"{fn}() needs numbers, not {v!r}")
    return v


def _int(v: Any) -> int:
    if isinstance(v, bool):
        raise ExpressionError("int() can't convert a boolean")
    try:
        return int(v) if not isinstance(v, str) else int(v.strip())
    except (TypeError, ValueError):
        raise ExpressionError(f"int() can't convert {v!r}") from None


def _float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ExpressionError(f"float() can't convert {v!r}") from None


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    if isinstance(v, (int, float)):
        return v != 0
    raise ExpressionError(f"bool() can't convert {v!r}")


def _length(v: Any) -> int:
    if isinstance(v, (str, list, dict)):
        return len(v)
    raise ExpressionError(f"length() needs a string or an array, not {v!r}")


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, (str, list, dict)) and len(v) == 0)


def _contains(c: Any, v: Any) -> bool:
    if isinstance(c, str):
        return _text(v) in c
    if isinstance(c, list):
        return v in c
    if isinstance(c, dict):
        return v in c
    raise ExpressionError("contains() needs a string, an array or an object")


def _div(a: Any, b: Any) -> int | float:
    a, b = _num(a, "div"), _num(b, "div")
    if b == 0:
        raise ExpressionError("div() can't divide by zero")
    if isinstance(a, int) and isinstance(b, int):
        return int(a / b)   # whole numbers divide to a whole number, rounded toward zero
    return a / b


def _mod(a: Any, b: Any) -> int | float:
    a, b = _num(a, "mod"), _num(b, "mod")
    if b == 0:
        raise ExpressionError("mod() can't divide by zero")
    return math.fmod(a, b) if isinstance(a, float) or isinstance(b, float) else int(math.fmod(a, b))


def _compare(fn: str, a: Any, b: Any) -> tuple[Any, Any]:
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return a, b
    if isinstance(a, str) and isinstance(b, str):
        return a, b
    raise ExpressionError(f"{fn}() compares two numbers or two strings, not {a!r} and {b!r}")


def _substring(s: Any, start: Any, length: Any = None) -> str:
    s, start = _text(s), _int(start)
    if start < 0 or start > len(s):
        raise ExpressionError("substring() start is outside the string")
    if length is None:
        return s[start:]
    length = _int(length)
    if length < 0 or start + length > len(s):
        raise ExpressionError("substring() length goes past the end of the string")
    return s[start:start + length]


def _array(v: Any, fn: str) -> list:
    if not isinstance(v, list):
        raise ExpressionError(f"{fn}() needs an array, not {v!r}")
    return v


def _first(v: Any) -> Any:
    if isinstance(v, (str, list)):
        return v[0] if v else None
    raise ExpressionError("first() needs a string or an array")


def _last(v: Any) -> Any:
    if isinstance(v, (str, list)):
        return v[-1] if v else None
    raise ExpressionError("last() needs a string or an array")


def _union(*vs: Any) -> Any:
    if all(isinstance(v, dict) for v in vs):
        out: dict = {}
        for v in vs:
            out.update(v)
        return out
    out_list: list = []
    for v in vs:
        for x in _array(v, "union"):
            if x not in out_list:
                out_list.append(x)
    return out_list


def _intersection(*vs: Any) -> list:
    first = _array(vs[0], "intersection")
    out = []
    for x in first:
        if all(x in _array(v, "intersection") for v in vs[1:]) and x not in out:
            out.append(x)
    return out


def _json(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    try:
        return json.loads(v)
    except ValueError:
        raise ExpressionError("json() was given text that isn't JSON") from None


# .NET date format tokens, longest first, mapped to strftime (the common ones only).
_DATE_TOKENS = [("yyyy", "%Y"), ("yy", "%y"), ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"), ("dddd", "%A"),
                ("ddd", "%a"), ("dd", "%d"), ("HH", "%H"), ("hh", "%I"), ("mm", "%M"), ("ss", "%S"),
                ("tt", "%p"), ("fffffff", "%f0"), ("ffffff", "%f"), ("fff", "%f3")]
_STANDARD = {"o": "yyyy-MM-ddTHH:mm:ss.fffffffK", "s": "yyyy-MM-ddTHH:mm:ss", "u": "yyyy-MM-dd HH:mm:ssZ",
             "d": "MM/dd/yyyy"}


def _parse_time(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    text = _text(v).strip()
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ExpressionError(f"{text!r} isn't a date and time this PC can read (use ISO 8601)") from None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _format_time(t: datetime, fmt: str | None) -> str:
    t = t.astimezone(timezone.utc)
    if not fmt or fmt == "o":
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond:06d}0Z"
    fmt = _STANDARD.get(fmt, fmt)
    out, i = [], 0
    while i < len(fmt):
        if fmt[i] == "'":
            j = fmt.find("'", i + 1)
            j = len(fmt) if j < 0 else j
            out.append(fmt[i + 1:j].replace("%", "%%"))
            i = j + 1
            continue
        for tok, code in _DATE_TOKENS:
            if fmt.startswith(tok, i):
                if code == "%f3":
                    out.append(f"{t.microsecond // 1000:03d}")
                elif code == "%f0":
                    out.append(f"{t.microsecond:06d}0")
                else:
                    out.append(code)
                i += len(tok)
                break
        else:
            ch = fmt[i]
            if ch == "K":
                out.append("Z")
            elif ch.isalpha() and ch not in "TZ":
                raise ExpressionError(f"date format {fmt!r} uses {ch!r}, which this PC doesn't support")
            else:
                out.append(ch.replace("%", "%%"))
            i += 1
    return t.strftime("".join(out))


def _add_time(unit: str) -> Callable[..., str]:
    def add(ts: Any, n: Any, fmt: Any = None) -> str:
        return _format_time(_parse_time(ts) + timedelta(**{unit: _num(n, "add" + unit)}), fmt)
    return add


def _start_of_day(ts: Any, fmt: Any = None) -> str:
    t = _parse_time(ts).astimezone(timezone.utc)
    return _format_time(t.replace(hour=0, minute=0, second=0, microsecond=0), fmt)


def _variable(scope: Scope, name: Any) -> Any:
    if name not in scope.variables:
        raise ExpressionError(f"variable {name!r} isn't declared")
    return scope.variables[name]


def _activity(scope: Scope, name: Any) -> dict[str, Any]:
    if name not in scope.activities:
        raise ExpressionError(f"activity {name!r} hasn't run yet in this run")
    return scope.activities[name]


def _item(scope: Scope) -> Any:
    if not scope.items:
        raise ExpressionError("item() is only available inside a ForEach")
    return scope.items[-1]


def _pipeline(scope: Scope) -> dict[str, Any]:
    return {**scope.system, "parameters": scope.parameters}


def _if(c: Any, a: Any, b: Any) -> Any:
    if not isinstance(c, bool):
        raise ExpressionError(f"if() needs true or false as its first argument, not {c!r}")
    return a if c else b


_PURE: dict[str, Callable[..., Any]] = {
    # logic and comparison
    "equals": lambda a, b: a == b, "not": lambda a: not _bool(a),
    "and": lambda *a: all(_bool(x) for x in a), "or": lambda *a: any(_bool(x) for x in a),
    "if": _if,
    "greater": lambda a, b: (lambda x, y: x > y)(*_compare("greater", a, b)),
    "greaterorequals": lambda a, b: (lambda x, y: x >= y)(*_compare("greaterOrEquals", a, b)),
    "less": lambda a, b: (lambda x, y: x < y)(*_compare("less", a, b)),
    "lessorequals": lambda a, b: (lambda x, y: x <= y)(*_compare("lessOrEquals", a, b)),
    # math
    "add": lambda a, b: _num(a, "add") + _num(b, "add"), "sub": lambda a, b: _num(a, "sub") - _num(b, "sub"),
    "mul": lambda a, b: _num(a, "mul") * _num(b, "mul"), "div": _div, "mod": _mod,
    "min": lambda *a: min(_num(x, "min") for x in (a[0] if len(a) == 1 and isinstance(a[0], list) else a)),
    "max": lambda *a: max(_num(x, "max") for x in (a[0] if len(a) == 1 and isinstance(a[0], list) else a)),
    "range": lambda start, count: list(range(_int(start), _int(start) + _int(count))),
    # strings
    "concat": lambda *a: "".join(_text(x) for x in a),
    "substring": _substring, "replace": lambda s, a, b: _text(s).replace(_text(a), _text(b)),
    "tolower": lambda s: _text(s).lower(), "toupper": lambda s: _text(s).upper(), "trim": lambda s: _text(s).strip(),
    "indexof": lambda s, x: _text(s).lower().find(_text(x).lower()),
    "lastindexof": lambda s, x: _text(s).lower().rfind(_text(x).lower()),
    "startswith": lambda s, x: _text(s).lower().startswith(_text(x).lower()),
    "endswith": lambda s, x: _text(s).lower().endswith(_text(x).lower()),
    "split": lambda s, d: _text(s).split(_text(d)),
    "guid": lambda *a: str(uuid.uuid4()),
    # collections
    "length": _length, "empty": _empty, "contains": _contains, "first": _first, "last": _last,
    "take": lambda c, n: c[:_int(n)], "skip": lambda c, n: c[_int(n):],
    "union": _union, "intersection": _intersection, "createarray": lambda *a: list(a),
    "join": lambda c, d: _text(d).join(_text(x) for x in _array(c, "join")),
    "coalesce": lambda *a: next((x for x in a if x is not None), None),
    # conversion
    "string": _text, "int": _int, "float": _float, "bool": _bool, "json": _json,
    "array": lambda v: [v],
    # dates
    "formatdatetime": lambda ts, fmt=None: _format_time(_parse_time(ts), fmt),
    "adddays": _add_time("days"), "addhours": _add_time("hours"), "addminutes": _add_time("minutes"),
    "addseconds": _add_time("seconds"), "startofday": _start_of_day,
}

_SCOPED: dict[str, Callable[..., Any]] = {
    "variables": _variable, "activity": _activity, "item": _item, "pipeline": _pipeline,
    "utcnow": lambda scope, fmt=None: _format_time(scope.now(), fmt),
}

FUNCTIONS = tuple(sorted(set(_PURE) | set(_SCOPED)))


def call(name: str, args: list[Any], scope: Scope) -> Any:
    fn = name.lower()
    try:
        if fn in _SCOPED:
            return _SCOPED[fn](scope, *args)
        if fn in _PURE:
            return _PURE[fn](*args)
    except TypeError:
        raise ExpressionError(f"{name}() was given {len(args)} argument(s), which it doesn't take") from None
    raise ExpressionError(f"{name}() isn't supported in local runs yet")

