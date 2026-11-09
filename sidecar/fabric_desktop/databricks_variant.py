"""Databricks SQL's VARIANT on this PC: a VARIANT is DuckDB JSON text in the form Databricks shows it.

parse_json reads JSON the way Databricks' VariantBuilder does and writes it back the way a VARIANT prints:
no spaces, object keys sorted, an integer as itself, a decimal without trailing zeros (0.01000 is 0.01)
and a number with an exponent as a DOUBLE (1e2 is 100.0). DuckDB's JSON functions then read it, so
``v:key`` and ``v:key::double`` work as they already did. ``register`` adds the functions below to a DuckDB
connection, and databricks_builtins_sql writes each Databricks call with them:

    __kf_parse_json(text, safe)               parse_json, try_parse_json
    __kf_variant_get(json, kind, steps, target, safe)  variant_get, try_variant_get
    __kf_variant_items(json, kind)            variant_explode's rows
    __kf_to_variant(json, kind)               to_variant_object, from DuckDB's to_json

They walk the text themselves: DuckDB's json_extract writes a number back as a DOUBLE, so 0.10000000000000000001
would come out 0.1 and 1.0E7 as 10000000.0.

The text keeps the value, not every type: a DECIMAL 1.0 and a BIGINT 1 both print 1. So a cast here runs
only where both readings give the same answer, and stops by name otherwise. Bad input raises Databricks'
error class by name, or gives NULL for the try_ ones. They read nothing and write nothing.
"""

from __future__ import annotations

import json
import math
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

_LONG = 2**63
_DEEPEST = 256  # nesting this PC reads; past it a cell stops by name, so no try_ guesses NULL
_SHOWN = 100  # characters of a value an error message repeats
_MAX_DIGITS = 38  # a VARIANT decimal has at most 38 digits and scale 38; anything else is a DOUBLE
_PLAIN_DECIMAL = re.compile(r"-?[0-9.]+")  # VariantBuilder.tryParseDecimal: no exponent
_INTEGER_BITS = {"tinyint": 8, "smallint": 16, "int": 32, "bigint": 64}
# DuckDB types to_json writes the same as a VARIANT prints them; a DOUBLE 100.0 and a DECIMAL 100.0 print
# differently in Databricks and alike in to_json, and dates and timestamps print another way
_PLAIN_TYPES = re.compile(r"\b(DOUBLE|FLOAT|DECIMAL|DATE|TIME|TIMESTAMP|INTERVAL|BLOB|UUID|BIT|HUGEINT|UHUGEINT|"
                          r"VARIANT|UNION|ENUM)\b")


class _Bad(ValueError):
    pass


class _Number:
    """A JSON number as Databricks keeps it: ``text`` is how it prints, ``kind`` long, decimal or double."""

    def __init__(self, text: str, kind: str) -> None:
        self.text, self.kind = text, kind


def _java_double(value: float) -> str:
    """``value`` as Java's Double.toString writes it, which is how a VARIANT DOUBLE prints: plain from 1e-3 up
    to 1e7, otherwise d.dddE<n>, with the shortest digits that read back as ``value`` (Python's repr)."""
    if value == 0:
        return "-0.0" if math.copysign(1, value) < 0 else "0.0"
    if 1e-3 <= abs(value) < 1e7:
        return repr(value)
    exact = Decimal(repr(abs(value)))
    digits = "".join(map(str, exact.as_tuple().digits)).rstrip("0") or "0"
    return f"{'-' if value < 0 else ''}{digits[0]}.{digits[1:] or '0'}E{exact.adjusted()}"


def _shown(text: str) -> str:
    """``text`` as an error message repeats it: at most _SHOWN characters."""
    return text if len(text) <= _SHOWN else text[:_SHOWN] + "…"


def _beyond(why: str) -> ValueError:
    return ValueError(f"parse_json() can't run on this PC the way it runs in Databricks, so nothing ran: {why}. "
                      "Run the cell on a Databricks SQL warehouse.")


def _number(text: str) -> _Number:
    """A JSON number token read as Databricks' VariantBuilder reads it."""
    if len(text) <= 20 and re.fullmatch(r"-?[0-9]+", text) and -_LONG <= int(text) < _LONG:  # 19 digits and a sign
        return _Number(str(int(text)), "long")
    if _PLAIN_DECIMAL.fullmatch(text):
        exact = Decimal(text)
        exponent = int(exact.as_tuple().exponent)
        scale, precision = max(-exponent, 0), len(exact.as_tuple().digits)
        if scale <= _MAX_DIGITS and precision <= _MAX_DIGITS:
            return _Number(_plain(exact), "decimal")
    value = float(text)
    if math.isinf(value):  # Jackson reads it as Infinity; how Databricks keeps that isn't documented
        raise _beyond(f"the number {_shown(text)} is past DOUBLE's range")
    return _Number(_java_double(value), "double")


def _plain(value: Decimal) -> str:
    """A decimal as BigDecimal.stripTrailingZeros().toPlainString() writes it."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"[VARIANT_DUPLICATE_KEY] parse_json() found the key {_shown(key)!r} twice in one object.")
        out[key] = value
    return out


def _constant(name: str) -> Any:
    raise _Bad(name)  # NaN and Infinity aren't JSON


_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _string(text: str) -> str:
    """A JSON string as Jackson writes it: the short escapes, other control characters as \\u00XX."""
    return '"' + "".join(_ESCAPES.get(c) or (f"\\u{ord(c):04X}" if ord(c) < 0x20 else c) for c in text) + '"'


def _write(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, _Number):
        return value.text
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ",".join(_write(v) for v in value) + "]"
    keys = sorted(value, key=lambda k: k.encode("utf-16-be"))  # Java's String order
    return "{" + ",".join(f"{_string(k)}:{_write(value[k])}" for k in keys) + "}"


def _depth(text: str) -> int:
    """How deep ``text``'s arrays and objects nest, counted outside strings, in one pass."""
    deepest = depth = 0
    quoted = escaped = False
    for c in text:
        if quoted:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif c in "]}":
            depth -= 1
    return deepest


def _load(text: str) -> Any:
    if _depth(text) > _DEEPEST:
        raise _beyond(f"it nests arrays and objects more than {_DEEPEST} levels deep")
    try:
        return json.loads(text, parse_int=_number, parse_float=_number, parse_constant=_constant,
                          object_pairs_hook=_pairs)
    except (_Bad, json.JSONDecodeError, InvalidOperation, OverflowError, RecursionError) as exc:
        raise _Bad from exc


def parse_json(text: str | None, safe: bool) -> str | None:
    """``text`` as a VARIANT prints it; NULL for NULL, and for bad JSON when ``safe`` (try_parse_json)."""
    if text is None:
        return None
    try:
        return _write(_load(text))
    except _Bad:
        if safe:
            return None
        raise ValueError("[MALFORMED_RECORD_IN_PARSING] parse_json() can't read its argument as JSON; "
                         "try_parse_json gives NULL for it.") from None


def to_variant(json_text: str | None, kind: str) -> str | None:
    """to_variant_object of a DuckDB value of type ``kind``, given as DuckDB's to_json of it."""
    if not (kind.startswith(("STRUCT(", "MAP(")) or kind.endswith("]")):
        raise ValueError(f"[DATATYPE_MISMATCH] to_variant_object() takes a STRUCT, MAP or ARRAY, not {kind}.")
    if re.search(r"MAP\((?!VARCHAR,)", kind):
        raise ValueError("[DATATYPE_MISMATCH] to_variant_object() takes a MAP with STRING keys only.")
    found = _PLAIN_TYPES.search(kind)
    if found:
        raise ValueError(f"to_variant_object() can't run on this PC the way it runs in Databricks, so nothing ran: "
                         f"a {found.group(1)} inside it prints another way in a VARIANT, and this PC keeps a "
                         "VARIANT as JSON text. Run the cell on a Databricks SQL warehouse, or cast those fields "
                         "to STRING first.")
    return None if json_text is None else _write(_load(json_text))


def _cast_error(safe: bool, value: str, target: str) -> None:
    if not safe:
        raise ValueError(f"[INVALID_VARIANT_CAST] The VARIANT value {_shown(value)} can't be cast to {target}; "
                         "try_variant_get gives NULL for it.")


def _unknown(value: str, target: str) -> ValueError:
    return ValueError(f"variant_get() can't run on this PC the way it runs in Databricks, so nothing ran: this "
                      f"PC can't tell how Databricks casts the VARIANT value {_shown(value)} to {target}. Run the cell on "
                      "a Databricks SQL warehouse, or get it as STRING and cast that.")


_STEP = re.compile(r"""\.([^.\[\]]+)|\[\s*'((?:[^'\\]|\\.)*)'\s*\]|\[\s*"((?:[^"\\]|\\.)*)"\s*\]|\[\s*(\d+)\s*\]""")


def path(text: str) -> list[str | int]:
    """A VARIANT path ($.data[1].a, $['key']) as its steps, or INVALID_VARIANT_GET_PATH."""
    steps: list[str | int] = []
    at = 1
    if not text.startswith("$"):
        at = -1
    while 0 < at < len(text):
        match = _STEP.match(text, at)
        if match is None:
            break
        name, single, double, index = match.groups()
        steps.append(int(index) if index is not None else name if name is not None else
                     re.sub(r"\\(.)", r"\1", single if single is not None else double))
        at = match.end()
    if at != len(text):
        raise ValueError(f"[INVALID_VARIANT_GET_PATH] {_shown(text)!r} isn't a VARIANT path: write $ and then .name, "
                         "['name'] or [index] steps.")
    return steps


def _at(value: Any, steps: list[str | int]) -> tuple[bool, Any]:
    for step in steps:
        if isinstance(step, int) and isinstance(value, list) and step < len(value):
            value = value[step]
        elif isinstance(step, str) and isinstance(value, dict) and step in value:
            value = value[step]
        else:
            return False, None
    return True, value


def _variant(kind: str, name: str) -> None:
    if kind not in ("JSON", '"NULL"'):  # DuckDB's type of a bare NULL is "NULL"
        raise ValueError(f"[DATATYPE_MISMATCH] {name}() takes a VARIANT, not {kind}; write parse_json(...).")


def variant_get(json_text: str | None, kind: str, steps: str, target: str, safe: bool) -> str | None:
    """The VARIANT ``json_text`` (one of parse_json's) at ``steps`` (path's, as JSON), cast to ``target``, as
    text DuckDB casts to that type; NULL for a missing path or a VARIANT null, and for a bad cast when
    ``safe``. ``kind`` is its DuckDB type, which must be JSON (a VARIANT)."""
    _variant(kind, "try_variant_get" if safe else "variant_get")
    if json_text is None:
        return None
    found, value = _at(_load(json_text), json.loads(steps))
    if not found or value is None:
        return None
    json_text = _write(value)
    if target == "variant":
        return json_text
    if target == "string":
        return value if isinstance(value, str) else json_text  # an object, array or number as its JSON
    known = target in _INTEGER_BITS or target in ("double", "float", "boolean") or target.startswith("decimal(")
    if isinstance(value, (list, dict)):  # an object or array to a scalar type
        if not known:
            raise _unknown(json_text, target)
        _cast_error(safe, json_text, target)
        return None
    if target.startswith(("array", "struct", "map")):  # a scalar to a complex type
        _cast_error(safe, json_text, target)
        return None
    if target == "boolean" and not isinstance(value, str):
        if isinstance(value, bool):
            return "true" if value else "false"
        _cast_error(safe, json_text, target)  # a number isn't a BOOLEAN in a VARIANT cast
        return None
    if not isinstance(value, _Number) or not known:  # a string or BOOLEAN to a number, or another type
        raise _unknown(json_text, target)
    return _number_cast(value, json_text, target, safe)


def _number_cast(value: _Number, json_text: str, target: str, safe: bool) -> str | None:
    if target in ("double", "float"):
        return value.text
    if target in _INTEGER_BITS:
        if value.kind != "long" or "." in value.text:
            raise _unknown(json_text, target)
        bits = _INTEGER_BITS[target]
        if -(2 ** (bits - 1)) <= int(value.text) < 2 ** (bits - 1):
            return value.text
        _cast_error(safe, json_text, target)
        return None
    match = re.fullmatch(r"decimal\((\d+),\s*(\d+)\)", target)
    if match is None or value.kind == "double" and "E" in value.text:
        raise _unknown(json_text, target)
    precision, scale = int(match.group(1)), int(match.group(2))
    rounded = Decimal(value.text).quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
    if len(rounded.as_tuple().digits) - max(-int(rounded.as_tuple().exponent), 0) <= precision - scale:
        return format(rounded, "f")
    _cast_error(safe, json_text, target)
    return None


def items(json_text: str | None, kind: str) -> list[dict[str, Any]] | None:
    """variant_explode's rows of a VARIANT object (pos, key, value; keys in order) or array (pos, NULL,
    element); none for anything else."""
    _variant(kind, "variant_explode")
    value = None if json_text is None else _load(json_text)
    if isinstance(value, list):
        return [{"pos": i, "key": None, "value": _write(v)} for i, v in enumerate(value)]
    if isinstance(value, dict):
        keys = sorted(value, key=lambda k: k.encode("utf-16-be"))
        return [{"pos": i, "key": k, "value": _write(value[k])} for i, k in enumerate(keys)]
    return []


def register(con: Any) -> None:
    """Add the __kf_ VARIANT functions to DuckDB connection ``con``. They see NULLs themselves
    (null_handling special), since a try_ one gives NULL for bad input too."""
    import duckdb
    from duckdb.sqltypes import BOOLEAN, INTEGER, VARCHAR
    rows = duckdb.list_type(duckdb.struct_type({"pos": INTEGER, "key": VARCHAR, "value": VARCHAR}))
    pure: dict[str, Any] = {"null_handling": "special", "side_effects": False}
    con.create_function("__kf_parse_json", parse_json, [VARCHAR, BOOLEAN], VARCHAR, **pure)
    con.create_function("__kf_variant_get", variant_get, [VARCHAR, VARCHAR, VARCHAR, VARCHAR, BOOLEAN], VARCHAR,
                        **pure)
    con.create_function("__kf_variant_items", items, [VARCHAR, VARCHAR], rows, **pure)
    con.create_function("__kf_to_variant", to_variant, [VARCHAR, VARCHAR], VARCHAR, **pure)
