"""Evaluate pipeline expressions for a local run (ORCH-003), with a fixed list of functions.

This is not a general-purpose evaluator: it parses the tokens ``expressions.tokens`` reads, and
calls only the functions in ``FUNCTIONS`` (each written here, none from Python's ``eval``). An
expression that calls anything else stops the activity with ``ExpressionError`` naming the
function, instead of guessing. The functions follow Microsoft's Data Factory expression
documentation; they have not been compared with Fabric's results (docs/orchestration/local-runs.md
lists the differences known so far).
"""

from __future__ import annotations

import base64
import binascii
import calendar
import json
import math
import random
import urllib.parse
import uuid
from collections import ChainMap
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

from . import expr_binary, expr_xml
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
    out, i, size = [], 0, 0
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
        part = _text(expression(text[start + 2:end], scope))
        size += len(part)
        if size > MAX_TEXT:   # "@{variables('s')}@{variables('s')}" in a loop doubles a value too
            raise ExpressionError(f"the text made a value with more than {MAX_TEXT:,} characters, the limit for one "
                                  "pipeline value")
        out.append(part)
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
    if expr_binary.is_binary(v):
        return expr_binary.binary_text(v)   # string() of a binary value is its UTF-8 text
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
                ("tt", "%p"), ("fffffff", "%f0"), ("ffffff", "%f"), ("fff", "%f3"),
                # one letter: the number without a leading zero
                ("d", "#day"), ("M", "#month"), ("H", "#hour"), ("h", "#hour12"), ("m", "#minute"), ("s", "#second")]
_STANDARD = {"o": "yyyy-MM-ddTHH:mm:ss.fffffffK", "s": "yyyy-MM-ddTHH:mm:ss", "u": "yyyy-MM-dd HH:mm:ssZ",
             "d": "MM/dd/yyyy", "D": "dddd, MMMM d, yyyy"}


def _parse_time(v: Any) -> datetime:
    t = _parse_raw(v)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _parse_raw(v: Any) -> datetime:
    """The date and time as written: with its offset when it has one, without one when it doesn't."""
    if isinstance(v, datetime):
        return v
    text = _text(v).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _parse_invariant(text)


# Besides ISO 8601, the month-first forms .NET's invariant culture reads, as in the reference's own
# examples (formatDateTime('03/15/2018 12:00:00', ...)).
_INVARIANT = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M", "%m/%d/%Y")


def _parse_invariant(text: str) -> datetime:
    for fmt in _INVARIANT:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ExpressionError(f"{text!r} isn't a date and time this PC can read (use ISO 8601 or MM/dd/yyyy HH:mm:ss)")


def _format_time(t: datetime, fmt: str | None, local: bool = False) -> str:
    """``t`` in .NET format ``fmt``, as UTC (ending in Z by default). ``local`` writes the wall-clock
    time of ``t``'s own zone with no zone marker, as Fabric writes convertFromUtc's result."""
    t = t if local else t.astimezone(timezone.utc)
    if not fmt or fmt == "o":
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond:06d}0" + ("" if local else "Z")
    if len(fmt) == 1 and fmt not in _STANDARD:
        # .NET reads any one-character format as a standard format ("M" is "March 15"), never as a token.
        raise ExpressionError(f"standard date format {fmt!r} isn't supported in local runs yet")
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
                if code.startswith("#"):
                    n = t.hour % 12 or 12 if code == "#hour12" else getattr(t, code[1:])
                    out.append(str(n))
                elif code == "%f3":
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
                out.append("" if local else "Z")
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


def _start_of_hour(ts: Any, fmt: Any = None) -> str:
    t = _parse_time(ts).astimezone(timezone.utc)
    return _format_time(t.replace(minute=0, second=0, microsecond=0), fmt)


def _start_of_month(ts: Any, fmt: Any = None) -> str:
    t = _parse_time(ts).astimezone(timezone.utc)
    return _format_time(t.replace(day=1, hour=0, minute=0, second=0, microsecond=0), fmt)


_UNITS = {"second": "seconds", "minute": "minutes", "hour": "hours", "day": "days", "week": "weeks"}


def _shift(t: datetime, n: Any, unit: Any, fn: str) -> datetime:
    """``t`` moved by ``n`` of ``unit`` (Second, Minute, Hour, Day, Week, Month or Year). A month or
    year keeps the day of the month, or the month's last day when it has fewer (as .NET's AddMonths)."""
    name = _text(unit).strip().lower()
    if name in _UNITS:
        try:
            return t + timedelta(**{_UNITS[name]: _num(n, fn)})
        except OverflowError:
            raise ExpressionError(f"{fn}() moved the time out of range") from None
    if name not in ("month", "year"):
        raise ExpressionError(f"{fn}() needs a time unit of Second, Minute, Hour, Day, Week, Month or Year, "
                              f"not {_text(unit)!r}")
    months = _int(n) * (12 if name == "year" else 1)
    total = t.year * 12 + (t.month - 1) + months
    year, month = divmod(total, 12)
    if not 1 <= year <= 9999:
        raise ExpressionError(f"{fn}() moved the time out of range")
    last = calendar.monthrange(year, month + 1)[1]
    return t.replace(year=year, month=month + 1, day=min(t.day, last))


def _add_to_time(ts: Any, n: Any, unit: Any, fmt: Any = None) -> str:
    return _format_time(_shift(_parse_time(ts), n, unit, "addToTime"), fmt)


def _subtract_from_time(ts: Any, n: Any, unit: Any, fmt: Any = None) -> str:
    return _format_time(_shift(_parse_time(ts), -_num(n, "subtractFromTime"), unit, "subtractFromTime"), fmt)


def _zone(name: Any, fn: str) -> tzinfo:
    """A Windows time zone id (as Fabric takes them: "Pacific Standard Time", "UTC") as a zone."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from .windows_zones import WINDOWS_TO_IANA
    key = _text(name).strip()
    iana = WINDOWS_TO_IANA.get(key) or next((v for k, v in WINDOWS_TO_IANA.items() if k.lower() == key.lower()), None)
    if iana is None:
        raise ExpressionError(f"{fn}() needs a Windows time zone id such as 'Pacific Standard Time', not {key!r}")
    try:
        return ZoneInfo(iana)
    except ZoneInfoNotFoundError:
        raise ExpressionError(f"{fn}(): this PC has no time zone data for {key!r}") from None


def _wall(ts: Any, zone: tzinfo) -> datetime:
    """A time as a wall-clock time in ``zone``: one written without an offset is taken to be in it."""
    t = _parse_raw(ts)
    return t.astimezone(zone) if t.tzinfo else t.replace(tzinfo=zone)


def _convert_from_utc(ts: Any, dest: Any, fmt: Any = None) -> str:
    return _format_time(_parse_time(ts).astimezone(_zone(dest, "convertFromUtc")), fmt, local=True)


def _convert_to_utc(ts: Any, src: Any, fmt: Any = None) -> str:
    return _format_time(_wall(ts, _zone(src, "convertToUtc")), fmt)


def _convert_time_zone(ts: Any, src: Any, dest: Any, fmt: Any = None) -> str:
    t = _wall(ts, _zone(src, "convertTimeZone")).astimezone(_zone(dest, "convertTimeZone"))
    return _format_time(t, fmt, local=True)


def _ticks(ts: Any) -> int:
    """100-nanosecond intervals since 0001-01-01T00:00:00 UTC, as .NET counts them."""
    t = _parse_time(ts).astimezone(timezone.utc)
    seconds = (t.toordinal() - 1) * 86400 + t.hour * 3600 + t.minute * 60 + t.second
    return seconds * 10_000_000 + t.microsecond * 10


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


# Fabric's ForEach takes at most 100,000 items; nothing a local run builds needs a bigger array.
MAX_ITEMS = 100_000


def _range(start: Any, count: Any) -> list[int]:
    n = _int(count)
    if not 0 <= n <= MAX_ITEMS:
        raise ExpressionError(f"range() count must be between 0 and {MAX_ITEMS:,}, not {n}")
    first = _int(start)
    return list(range(first, first + n))


def _if(c: Any, a: Any, b: Any) -> Any:
    if not isinstance(c, bool):
        raise ExpressionError(f"if() needs true or false as its first argument, not {c!r}")
    return a if c else b


def _base64(v: Any) -> str:
    if expr_binary.is_binary(v):
        return base64.b64encode(expr_binary.binary_bytes(v)).decode("ascii")
    return base64.b64encode(_text(v).encode("utf-8")).decode("ascii")


def _from_base64(v: Any, fn: str) -> str:
    try:
        return base64.b64decode(_text(v), validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        raise ExpressionError(f"{fn}() was given text that isn't base64-encoded UTF-8 text") from None


def _data_uri_text(v: Any) -> str:
    """The text in a data URI (data:[<media type>][;charset=...][;base64],<data>), read as UTF-8 unless
    it names another charset."""
    text = _text(v)
    head, sep, data = text.partition(",")
    if not head.lower().startswith("data:") or not sep:
        raise ExpressionError("dataUriToString() was given text that isn't a data URI")
    params = [p.strip() for p in head[5:].split(";")]
    charset = next((p.split("=", 1)[1] for p in params if p.lower().startswith("charset=")), "utf-8")
    try:
        raw = base64.b64decode(data, validate=True) if "base64" in (p.lower() for p in params) \
            else urllib.parse.unquote_to_bytes(data)
        return raw.decode(charset)
    except (binascii.Error, LookupError, ValueError):
        raise ExpressionError("dataUriToString() couldn't read the data URI's text") from None


def _rand(lo: Any, hi: Any) -> int:
    """rand(): a whole number from lo up to, but not including, hi."""
    if isinstance(lo, bool) or isinstance(hi, bool) or not isinstance(lo, int) or not isinstance(hi, int):
        raise ExpressionError(f"rand() needs two whole numbers, not {lo!r} and {hi!r}")
    if lo >= hi:
        raise ExpressionError(f"rand() needs its minimum ({lo}) to be less than its maximum ({hi})")
    return random.randrange(lo, hi)


def _uri_encode(v: Any) -> str:
    # As .NET's Uri.EscapeDataString: everything but letters, digits and - _ . ~ is escaped, as UTF-8.
    return urllib.parse.quote(_text(v), safe="")


def _uri_decode(v: Any) -> str:
    # As .NET's Uri.UnescapeDataString: %XX escapes become UTF-8 text; a + stays a +.
    try:
        return urllib.parse.unquote(_text(v), errors="strict")
    except UnicodeDecodeError:
        raise ExpressionError("decodeUriComponent() was given escapes that aren't UTF-8 text") from None


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
    "range": _range, "rand": _rand,
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
    "base64": _base64, "base64tostring": lambda v: _from_base64(v, "base64ToString"),
    "decodebase64": lambda v: _from_base64(v, "decodeBase64"),
    "datauri": lambda v: "data:text/plain;charset=utf-8;base64," + _base64(v), "datauritostring": _data_uri_text,
    "uricomponent": _uri_encode, "encodeuricomponent": _uri_encode,
    "uricomponenttostring": _uri_decode, "decodeuricomponent": _uri_decode,
    "binary": lambda v: expr_binary.to_binary(_text(v)),
    "base64tobinary": lambda v: expr_binary.base64_to_binary(_text(v)),
    "datauritobinary": lambda v: expr_binary.data_uri_to_binary(_text(v), "dataUriToBinary"),
    "decodedatauri": lambda v: expr_binary.data_uri_to_binary(_text(v), "decodeDataUri"),
    "uricomponenttobinary": lambda v: expr_binary.uri_component_to_binary(_text(v)),
    "xml": expr_xml.to_xml, "xpath": expr_xml.xpath,
    # dates
    "formatdatetime": lambda ts, fmt=None: _format_time(_parse_time(ts), fmt),
    "adddays": _add_time("days"), "addhours": _add_time("hours"), "addminutes": _add_time("minutes"),
    "addseconds": _add_time("seconds"), "startofday": _start_of_day,
    "startofhour": _start_of_hour, "startofmonth": _start_of_month,
    "addtotime": _add_to_time, "subtractfromtime": _subtract_from_time,
    "dayofmonth": lambda ts: _parse_time(ts).astimezone(timezone.utc).day,
    "dayofweek": lambda ts: (_parse_time(ts).astimezone(timezone.utc).weekday() + 1) % 7,   # Sunday is 0
    "dayofyear": lambda ts: _parse_time(ts).astimezone(timezone.utc).timetuple().tm_yday,
    "ticks": _ticks,
    "convertfromutc": _convert_from_utc, "converttoutc": _convert_to_utc, "converttimezone": _convert_time_zone,
}

_SCOPED: dict[str, Callable[..., Any]] = {
    "variables": _variable, "activity": _activity, "item": _item, "pipeline": _pipeline,
    "utcnow": lambda scope, fmt=None: _format_time(scope.now(), fmt),
    "getfuturetime": lambda scope, n, unit, fmt=None: _format_time(_shift(scope.now(), n, unit, "getFutureTime"), fmt),
    "getpasttime": lambda scope, n, unit, fmt=None: _format_time(
        _shift(scope.now(), -_num(n, "getPastTime"), unit, "getPastTime"), fmt),
}

FUNCTIONS = tuple(sorted(set(_PURE) | set(_SCOPED)))


MAX_TEXT = 10 * 1024 * 1024    # characters in one value: a pipeline value is small; a loop that doubles one isn't


def weight(value: Any) -> tuple[int, int]:
    """(items, characters) in ``value``, counted through arrays and objects; stops counting once past
    the limits, so it costs at most MAX_ITEMS steps."""
    nodes, chars, stack = 0, 0, [value]
    while stack and nodes <= MAX_ITEMS and chars <= MAX_TEXT:
        v = stack.pop()
        nodes += 1
        if isinstance(v, str):
            chars += len(v)
        elif isinstance(v, dict):
            chars += sum(len(str(k)) for k in v)
            stack.extend(v.values())
        elif isinstance(v, (list, tuple)):
            stack.extend(v)
    return nodes, chars


def over(nodes: int, chars: int) -> str:
    """Why a value of this weight is too big for one pipeline value, or ""."""
    if chars > MAX_TEXT:
        return f"more than {MAX_TEXT:,} characters"
    if nodes > MAX_ITEMS:
        return f"more than {MAX_ITEMS:,} items"
    return ""


def _too_big(value: Any) -> str:
    """Why ``value`` is too big for a pipeline value, or "". Shared parts count each time they appear,
    as they would in its JSON."""
    return over(*weight(value))


def _bounded(name: str, value: Any) -> Any:
    """A function's result, refused when it is too big for a pipeline value, so an expression in a loop
    (concat(s, s) in an Until) stops instead of using all the engine's memory."""
    if isinstance(value, str):
        why = f"more than {MAX_TEXT:,} characters" if len(value) > MAX_TEXT else ""
    elif isinstance(value, (list, dict)):
        why = _too_big(value)
    else:
        why = ""
    if why:
        raise ExpressionError(f"{name}() made a value with {why}, the limit for one pipeline value")
    return value


def call(name: str, args: list[Any], scope: Scope) -> Any:
    fn = name.lower()
    try:
        if fn in _SCOPED:
            return _SCOPED[fn](scope, *args)   # reads a value the run already holds
        # These can multiply a value's size in one call, so they are checked before they run.
        if fn == "replace" and len(args) == 3:
            text, old, new = (_text(a) for a in args)
            if old and len(text) + text.count(old) * (len(new) - len(old)) > MAX_TEXT:
                raise ExpressionError(f"replace() would make a value over {MAX_TEXT:,} characters, the limit")
        if fn == "join" and len(args) == 2 and isinstance(args[0], list):
            if (len(args[0]) - 1) * len(_text(args[1])) > MAX_TEXT or _too_big(args[0]):
                raise ExpressionError(f"join() would make a value over {MAX_TEXT:,} characters, the limit")
        if fn in _PURE:
            return _bounded(name, _PURE[fn](*args))
    except TypeError:
        raise ExpressionError(f"{name}() was given {len(args)} argument(s), which it doesn't take") from None
    except (expr_xml.XmlError, expr_binary.BinaryError) as e:
        raise ExpressionError(str(e) if str(e).lower().startswith(fn + "(") else f"{name}(): {e}") from None
    raise ExpressionError(f"{name}() isn't supported in local runs yet")

