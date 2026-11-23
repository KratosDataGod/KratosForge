"""Spark's types for values of more than one type in one call, for Spark SQL on this PC.

Spark (ANSI off, as Fabric runs it) finds one type for the values of ``array(...)``, the keys and the
values of ``map(...)``, ``IN`` and ``concat`` of arrays, and of ``coalesce`` (and ``nvl``, ``ifnull``),
``CASE`` and ``if`` (TypeCoercion.findWiderCommonType). A string next to a number, a date or a timestamp
makes them all strings: ``array(1, 'a')`` is ``['1', 'a']``, an ``array<string>``. A boolean next to a
number, a binary next to a string, or a date next to a number has no such type, so Spark stops
([DATATYPE_MISMATCH]). DuckDB cast 'a' to a number and stopped, or answered ``array(true, 1)`` and
``true IN (1)`` where Spark stops. ``coalesce``, ``CASE``, ``if``, ``greatest`` and ``least`` are typed here
only when an array or a map is among their values: plain values there are spark_branch_types' (WR-259).

``greatest``, ``least`` and ``array_contains`` take values Spark can widen to one type without making
strings (findWiderTypeWithoutStringPromotion); ``array_position``, ``array_remove``, ``array_union``,
``array_intersect``, ``array_except`` and ``arrays_overlap`` only the tightest one
(findTightestCommonType): ``array_union(array(1), array(1.5))`` stops, since an int doesn't fit in a
decimal(2,1). ``array_union(array(1), array(1.5D))`` is an ``array<double>``, as is ``array_except`` of
the same, where DuckDB kept the first array's type.

sql.spark_fixes marks the calls the query writes (``mark``) before any rewrite, with the types of the values
that are known as written (literals, casts), since a rewrite may write a value anew (-1, a cast);
``spark_coercions`` types the columns once the statement's tables are bound to the connection's views, as
spark_text_compare does. Only values whose types are known here are used: literals, casts, columns,
comparisons and arrays of them; a call with a value of any other type is left as it was.
"""
from __future__ import annotations

import itertools
import logging
from typing import Any, cast

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from .spark_decimals import Refused

log = logging.getLogger(__name__)

_KEY = "kf_coerce"  # (group, index, kind, what, code, types as written, arrays only) on each value of a marked call
_CALL = "kf_coerce_call"  # [(slot, group)] on a marked call: its groups, by their place in _groups
_T = exp.DataType.Type
_INT_RANKS = {_T.TINYINT: 1, _T.SMALLINT: 2, _T.INT: 3, _T.BIGINT: 4}
_INT_DIGITS = {1: 3, 2: 5, 3: 10, 4: 20}
_INT_NAMES = {1: ("tinyint", "TINYINT"), 2: ("smallint", "SMALLINT"), 3: ("int", "INTEGER"), 4: ("bigint", "BIGINT")}
_SIMPLE = {_T.BOOLEAN: ("bool",), _T.FLOAT: ("float",), _T.DOUBLE: ("double",), _T.VARCHAR: ("string",),
           _T.TEXT: ("string",), _T.CHAR: ("string",), _T.NCHAR: ("string",), _T.NVARCHAR: ("string",),
           _T.DATE: ("date",), _T.TIMESTAMPTZ: ("ts",), _T.TIMESTAMPLTZ: ("ts",), _T.TIMESTAMP: ("ntz",),
           _T.TIMESTAMPNTZ: ("ntz",), _T.BINARY: ("binary",), _T.VARBINARY: ("binary",), _T.BLOB: ("binary",),
           _T.NULL: ("null",)}
_NAMES = {"bool": ("boolean", "BOOLEAN"), "float": ("float", "FLOAT"), "double": ("double", "DOUBLE"),
          "string": ("string", "VARCHAR"), "date": ("date", "DATE"), "ts": ("timestamp", "TIMESTAMPTZ"),
          "ntz": ("timestamp_ntz", "TIMESTAMP"), "binary": ("binary", "BLOB"), "null": ("void", "INTEGER")}
_ATOMIC = {"int", "dec", "float", "double", "string", "date", "ts", "ntz"}  # what a string widens with
# The rule each kind of call's values follow.
_WIDER, _NO_STRING, _TIGHTEST, _TIGHTEST_ARRAYS, _IN, _CONCAT = ("wider", "no_string", "tightest",
                                                               "tightest_arrays", "in", "concat")
_ONE_TYPE = "one_type"  # greatest and least: refused where Spark stops, as DuckDB widens the rest the same way
_ARRAY_PAIRS = {"ARRAY_UNION": "array_union", "ARRAYS_OVERLAP": "arrays_overlap"}

Type = tuple[Any, ...]


def _group(node: exp.Expr) -> tuple[list[exp.Expr], str, str, str, bool] | None:
    """The values of a call whose values Spark gives one type, with the rule they follow, the call's name and
    Spark's error code, and whether only arrays and maps among them are typed here (spark_branch_types types
    CASE, if, coalesce, greatest and least of plain values); None for any other node."""
    if isinstance(node, exp.Array):
        if not isinstance(node.parent, (exp.VarMap, exp.Map)) and node.expressions:
            return list(node.expressions), _WIDER, "array()", "DATA_DIFF_TYPES", False
    elif isinstance(node, exp.Coalesce):
        return [node.this, *node.expressions], _WIDER, "coalesce()", "DATA_DIFF_TYPES", True
    elif isinstance(node, exp.Case):
        values = [i.args.get("true") for i in node.args.get("ifs") or []] + [node.args.get("default")]
        return [v for v in values if v is not None], _WIDER, "CASE", "DATA_DIFF_TYPES", True
    elif isinstance(node, exp.If):
        if not isinstance(node.parent, exp.Case):  # a WHEN of a CASE is typed with the CASE
            values = [node.args.get("true"), node.args.get("false")]
            return [v for v in values if v is not None], _WIDER, "if()", "DATA_DIFF_TYPES", True
    elif isinstance(node, (exp.Greatest, exp.Least)):
        name = "greatest()" if isinstance(node, exp.Greatest) else "least()"
        return [node.this, *node.expressions], _ONE_TYPE, name, "DATA_DIFF_TYPES", True
    elif isinstance(node, exp.In):
        if node.args.get("query") is None and node.args.get("unnest") is None and node.expressions:
            return [node.this, *node.expressions], _IN, "IN", "DATA_DIFF_TYPES", False
    elif isinstance(node, exp.Concat):
        if not isinstance(node, exp.ConcatWs) and len(node.expressions) >= 2:
            return list(node.expressions), _CONCAT, "concat()", "DATA_DIFF_TYPES", False
    elif isinstance(node, exp.ArrayContains):
        return [node.this, node.expression], _NO_STRING, "array_contains()", "ARRAY_FUNCTION_DIFF_TYPES", False
    elif isinstance(node, (exp.ArrayPosition, exp.ArrayRemove)):
        name = "array_position()" if isinstance(node, exp.ArrayPosition) else "array_remove()"
        return [node.this, node.expression], _TIGHTEST, name, "ARRAY_FUNCTION_DIFF_TYPES", False
    elif isinstance(node, (exp.ArrayIntersect, exp.ArrayExcept)):
        pair = list(node.expressions) if isinstance(node, exp.ArrayIntersect) else [node.this, node.expression]
        if len(pair) == 2:
            name = "array_intersect()" if isinstance(node, exp.ArrayIntersect) else "array_except()"
            return pair, _TIGHTEST_ARRAYS, name, "BINARY_ARRAY_DIFF_TYPES", False
    elif isinstance(node, exp.Anonymous) and str(node.this).upper() in _ARRAY_PAIRS and len(node.expressions) == 2:
        return (list(node.expressions), _TIGHTEST_ARRAYS, _ARRAY_PAIRS[str(node.this).upper()] + "()",
                "BINARY_ARRAY_DIFF_TYPES", False)
    return None


def _map_groups(node: exp.Expr) -> list[tuple[list[exp.Expr], str, str, str, bool]]:
    keys, values = node.args.get("keys"), node.args.get("values")
    if not (isinstance(keys, exp.Array) and isinstance(values, exp.Array)):
        return []
    return [(list(keys.expressions), _WIDER, "map()'s keys", "CREATE_MAP_KEY_DIFF_TYPES", False),
            (list(values.expressions), _WIDER, "map()'s values", "CREATE_MAP_VALUE_DIFF_TYPES", False)]


def _groups(node: exp.Expr) -> list[tuple[list[exp.Expr], str, str, str, bool]]:
    if isinstance(node, (exp.VarMap, exp.Map)):
        return _map_groups(node)
    found = _group(node)
    return [found] if found is not None else []


def mark(statement: exp.Expression) -> exp.Expression:
    """Mark the values of the calls the query writes, before other rewrites copy or rewrite them. Each mark
    holds the types of the call's values that are known as written (literals, casts), since a rewrite can
    replace a value (a negative number, a cast) with SQL of its own; a column is typed when the query runs."""
    ids = itertools.count()
    for node in list(statement.walk()):
        for slot, (values, kind, what, code, arrays_only) in enumerate(_groups(node)):
            gid = next(ids)
            node.meta.setdefault(_CALL, []).append((slot, gid))
            known = tuple(_spark_type(v) for v in values)
            for i, value in enumerate(values):
                value.meta[_KEY] = (gid, i, kind, what, code, known, arrays_only)
    return statement


def carry(old: exp.Expr, new: exp.Expr, name: str) -> None:
    """The marks on ``old``'s two values, put on the two arguments of the first call named ``name`` in
    ``new``, which a rewrite wrote from their text (spark_complex)."""
    found = next((n for n in new.walk() if (isinstance(n, exp.Anonymous) and str(n.this).upper() == name)
                  or (isinstance(n, exp.Func) and n.sql_name().upper() == name)), None)
    if found is None:
        return
    args = [found.this, found.expression] if not isinstance(found, exp.Anonymous) else found.expressions[:2]
    for before, after in zip((old.this, old.expression), args, strict=False):
        if isinstance(before, exp.Expression) and isinstance(after, exp.Expression) and _KEY in before.meta:
            after.meta[_KEY] = before.meta[_KEY]


# ---- Spark's types, as tuples: ("int", rank), ("dec", p, s), ("array", elem), ("map", k, v), ("string",) ...

def _from_datatype(dtype: Any) -> Type | None:
    if not isinstance(dtype, exp.DataType):
        return None
    if dtype.this in _INT_RANKS:
        return ("int", _INT_RANKS[dtype.this])
    if dtype.this == _T.DECIMAL:
        params = [p.this for p in dtype.expressions]
        try:
            precision = int(params[0].name) if params else 10
            scale = int(params[1].name) if len(params) > 1 else 0
        except (ValueError, AttributeError):
            return None
        return ("dec", precision, scale)
    if dtype.this == _T.ARRAY:
        inner = _from_datatype(dtype.expressions[0]) if dtype.expressions else None
        return ("array", inner) if inner is not None else None
    if dtype.this == _T.MAP:
        if len(dtype.expressions) != 2:
            return None
        k, v = _from_datatype(dtype.expressions[0]), _from_datatype(dtype.expressions[1])
        return ("map", k, v) if k is not None and v is not None else None
    if dtype.this in _SIMPLE:
        return _SIMPLE[dtype.this]
    if dtype.this == _T.UNKNOWN:
        return None
    return ("other", dtype.sql("duckdb"))


def _literal(node: exp.Literal) -> Type:
    if node.is_string:
        return ("string",)
    text = node.this.lstrip("+-").lower()
    if "e" in text:
        return ("double",)
    if "." in text:
        whole, _, fraction = text.partition(".")
        digits = len((whole + fraction).lstrip("0")) or 1
        return ("dec", min(max(digits, len(fraction)), 38), len(fraction))
    if int(text) >= 2 ** 63:
        return ("dec", min(len(text.lstrip("0")), 38), 0)  # past a bigint: a decimal, as Spark reads it
    return ("int", 3 if int(text) < 2 ** 31 else 4)


def _spark_type(node: exp.Expr) -> Type | None:
    """Spark's type for ``node`` (in the typed copy), or None when it isn't known here."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        node = node.this
    if isinstance(node, exp.Literal):
        return _literal(node)
    if isinstance(node, exp.Boolean):
        return ("bool",)
    if isinstance(node, exp.Null):
        return ("null",)
    if isinstance(node, (exp.HexString, exp.ByteString)):
        return ("binary",)
    if isinstance(node, (exp.Cast, exp.TryCast)):
        return _from_datatype(node.to)
    if isinstance(node, exp.Array):
        if not node.expressions:
            return ("array", ("null",))
        found = [_spark_type(e) for e in node.expressions]
        if any(t is None for t in found):
            return None
        common = _common(cast(list[Type], found), _wider)
        return ("array", common) if common is not None else None
    if isinstance(node, (exp.Column, exp.Dot)):
        return _from_datatype(node.type)
    if isinstance(node, (exp.Predicate, exp.Connector, exp.Not)):
        return ("bool",)
    return None


def _has_string(t: Type) -> bool:
    return t[0] == "string" or (t[0] == "array" and _has_string(t[1])) or (
        t[0] == "map" and (_has_string(t[1]) or _has_string(t[2])))


def _has_decimal(t: Type) -> bool:
    return t[0] == "dec" or (t[0] == "array" and _has_decimal(t[1])) or (
        t[0] == "map" and (_has_decimal(t[1]) or _has_decimal(t[2])))


def _as_decimal(t: Type) -> Type | None:
    return ("dec", _INT_DIGITS[t[1]], 0) if t[0] == "int" else t if t[0] == "dec" else None


def _tightest(a: Type, b: Type) -> Type | None:
    """TypeCoercion.findTightestCommonType."""
    if a == b:
        return a
    if a[0] == "null":
        return b
    if b[0] == "null":
        return a
    order = {"float": 5, "double": 6}
    if a[0] in ("int", "float", "double") and b[0] in ("int", "float", "double"):
        ra, rb = (a[1] if a[0] == "int" else order[a[0]]), (b[1] if b[0] == "int" else order[b[0]])
        return a if ra >= rb else b
    if {a[0], b[0]} == {"int", "dec"}:
        whole, dec = (a, b) if a[0] == "int" else (b, a)
        return dec if dec[1] - dec[2] >= _INT_DIGITS[whole[1]] else None
    pair = {a[0], b[0]}
    if pair in ({"date", "ts"}, {"ts", "ntz"}):
        return ("ts",)
    if pair == {"date", "ntz"}:
        return ("ntz",)
    return _complex(a, b, _tightest)


def _complex(a: Type, b: Type, rule: Any) -> Type | None:
    if a[0] == "array" and b[0] == "array":
        inner = rule(a[1], b[1])
        return ("array", inner) if inner is not None else None
    if a[0] == "map" and b[0] == "map":
        k, v = rule(a[1], b[1]), rule(a[2], b[2])
        return ("map", k, v) if k is not None and v is not None else None
    return None


def _wider_decimal(a: Type, b: Type) -> Type | None:
    if a[0] in ("dec", "int") and b[0] in ("dec", "int") and "dec" in (a[0], b[0]):
        da, db = cast(Type, _as_decimal(a)), cast(Type, _as_decimal(b))
        scale = max(da[2], db[2])
        whole = max(da[1] - da[2], db[1] - db[2])
        return ("dec", min(whole + scale, 38), min(scale, 38))
    if {a[0], b[0]} in ({"dec", "double"}, {"dec", "float"}):
        return ("double",)
    return None


def _no_string(a: Type, b: Type) -> Type | None:
    """TypeCoercion.findWiderTypeWithoutStringPromotionForTwo."""
    return _tightest(a, b) or _wider_decimal(a, b) or _complex(a, b, _no_string)


def _wider(a: Type, b: Type) -> Type | None:
    """TypeCoercion.findWiderTypeForTwo: a string widens with a number, a date or a timestamp."""
    found = _tightest(a, b) or _wider_decimal(a, b)
    if found is not None:
        return found
    if (a[0] == "string" and b[0] in _ATOMIC) or (b[0] == "string" and a[0] in _ATOMIC):
        return ("string",)
    return _complex(a, b, _wider)


def _common(types: list[Type], rule: Any) -> Type | None:
    """TypeCoercion.findWiderCommonType: types holding a string first, so (int, date, string) is a string."""
    ordered = list(dict.fromkeys(t for t in types if _has_string(t))) + [t for t in types if not _has_string(t)]
    found: Type | None = ("null",)
    for t in ordered:
        found = rule(found, t) if found is not None else None
    return found


def spark_name(t: Type) -> str:
    if t[0] == "int":
        return _INT_NAMES[t[1]][0]
    if t[0] == "dec":
        return f"decimal({t[1]},{t[2]})"
    if t[0] == "array":
        return f"array<{spark_name(t[1])}>"
    if t[0] == "map":
        return f"map<{spark_name(t[1])},{spark_name(t[2])}>"
    if t[0] == "other":
        return str(t[1]).lower()
    return _NAMES[t[0]][0]


def _duck_name(t: Type) -> str:
    if t[0] == "int":
        return _INT_NAMES[t[1]][1]
    if t[0] == "dec":
        return f"DECIMAL({t[1]}, {t[2]})"
    if t[0] == "array":
        return f"{_duck_name(t[1])}[]"
    if t[0] == "map":
        return f"MAP({_duck_name(t[1])}, {_duck_name(t[2])})"
    return _NAMES[t[0]][1]


# ---- the rewrite

def _text_sql(value: str, t: Type, depth: int) -> str:
    """DuckDB SQL for ``value`` (of Spark type ``t``) cast to a string as Spark's CAST to STRING writes it.
    ``value`` is written once."""
    from .spark_builtins_sql import java_double_text
    if t[0] in ("string", "null"):
        return f"CAST({value} AS VARCHAR)"
    if t[0] in ("double", "float"):
        # A float keeps its own shortest digits (0.1, where as a double it is 0.10000000149011612)
        name = f"kf_coerce_d{depth}"
        return (f"(LIST_TRANSFORM([TRY_CAST({value} AS {_duck_name(t)})], lambda {name}: "
                f"{java_double_text(name)}))[1]")
    if t[0] == "ts":
        return f"regexp_replace(CAST({value} AS VARCHAR), '[+-][0-9][0-9](:[0-9][0-9])?$', '')"
    return f"CAST({value} AS VARCHAR)"


def _to_sql(value: str, t: Type, to: Type, depth: int = 0) -> str:
    """DuckDB SQL for ``value`` (of Spark type ``t``) as Spark's type ``to``: a string as Spark writes it,
    an array or a map element by element, anything else a plain cast. ``value`` is written once."""
    if t == to and t[0] != "dec":
        return value
    if to[0] == "string":
        return _text_sql(value, t, depth)
    if to[0] == "array" and t[0] == "array" and _has_string(to):
        name = f"kf_coerce_e{depth}"
        return f"LIST_TRANSFORM({value}, lambda {name}: {_to_sql(name, t[1], to[1], depth + 1)})"
    if to[0] == "map" and t[0] == "map" and _has_string(to):
        name = f"kf_coerce_m{depth}"
        key = _to_sql(f"{name}.key", t[1], to[1], depth + 1)
        val = _to_sql(f"{name}.value", t[2], to[2], depth + 1)
        return f"MAP_FROM_ENTRIES(LIST_TRANSFORM(MAP_ENTRIES({value}), lambda {name}: {{'key': {key}, 'value': {val}}}))"
    if t[0] == "null":
        return value
    return f"CAST({value} AS {_duck_name(to)})"


def _retype(node: exp.Expr, t: Type, to: Type) -> None:
    """``node`` (in the statement itself) as Spark's type ``to``, in place; the node itself is kept inside."""
    if (t == to and t[0] != "dec") or t[0] == "null":
        return
    new = sqlglot.parse_one(_to_sql("fj_arg0", t, to), read="duckdb")
    node.replace(new)
    next(c for c in new.find_all(exp.Column) if c.name == "fj_arg0").replace(node)


def _refuse(what: str, code: str, types: list[Type]) -> Refused:
    names = list(dict.fromkeys(spark_name(t) for t in types if t[0] != "null")) or ["void"]
    listed = ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]
    return Refused(f"{what} can't take {listed} values together, because Spark finds no one type for them, so "
                   f"nothing ran. Spark stops on it too ([DATATYPE_MISMATCH.{code}]). Cast the values to one "
                   "type first, such as CAST(value AS STRING).")


def spark_coercions(statement: exp.Expr, conn: Any, schema: MappingSchema | None = None) -> exp.Expr:
    """``statement`` with the marked calls' values given Spark's one type, or refused (Refused) where
    Spark stops. Its tables must be bound to the connection's views (or typed by ``schema``)."""
    marked = [n for n in statement.walk() if _KEY in n.meta]
    if not marked:
        return statement
    try:
        groups: dict[int, dict[int, Type | None]] = {}
        about: dict[int, tuple[str, str, str, bool]] = {}
        for node in marked:
            gid, _index, kind, what, code, known, arrays_only = node.meta[_KEY]
            about[gid] = (kind, what, code, arrays_only)
            groups[gid] = dict(enumerate(known))
        if any(t is None for values in groups.values() for t in values.values()):
            _type_columns(statement, conn, schema, groups)
        plans: dict[tuple[int, int], tuple[Type, Type]] = {}
        for gid, values in groups.items():
            kind, what, code, arrays_only = about[gid]
            if any(t is None for t in values.values()):
                continue
            ordered = [cast(Type, values[i]) for i in sorted(values)]
            if arrays_only and not any(t[0] in ("array", "map") for t in ordered):
                continue  # plain values: spark_branch_types' to type
            for index, (t, to) in _plan(kind, what, code, ordered).items():
                plans[(gid, index)] = (t, to)
        if plans:
            for key, nodes in _value_nodes(statement).items():
                if key in plans:
                    t, to = plans[key]
                    for node in nodes:
                        _retype(node, t, to)
    finally:
        for node in statement.walk():
            node.meta.pop(_KEY, None)
            node.meta.pop(_CALL, None)
    return statement


def _type_columns(statement: exp.Expr, conn: Any, schema: MappingSchema | None,
                  groups: dict[int, dict[int, Type | None]]) -> None:
    """The types ``groups`` doesn't know from the query as written, from a typed copy of ``statement``."""
    copy = statement.copy()
    try:
        if schema is None:
            from .spark_decimals import _schema
            schema = _schema(conn)
        copy = qualify(copy, schema=schema, dialect="spark", validate_qualify_columns=False,
                       quote_identifiers=False, identify=False)
        annotate_types(copy, schema=schema, dialect="spark")
    except Exception as exc:  # noqa: BLE001 - columns go untyped; literals and casts are still typed; logged
        log.info("Spark coercions: columns not typed: %s: %s", type(exc).__name__, exc)
        return
    for node in copy.walk():
        if _KEY not in node.meta:
            continue
        gid, index = node.meta[_KEY][:2]
        if groups[gid].get(index) is None:
            groups[gid][index] = _spark_type(node)


def _value_nodes(statement: exp.Expr) -> dict[tuple[int, int], list[exp.Expr]]:
    """Each marked value in ``statement`` by (group, index): the nodes that carry its mark (a rewrite that
    copies a value, as typeof does, copies its mark), or the value in its call's place when a rewrite wrote
    a new node for it (-1 as a cast of 1)."""
    marked: dict[tuple[int, int], list[exp.Expr]] = {}
    placed: dict[tuple[int, int], list[exp.Expr]] = {}
    for node in list(statement.walk()):
        if _KEY in node.meta:
            marked.setdefault(node.meta[_KEY][:2], []).append(node)
        for slot, gid in node.meta.get(_CALL, []):
            groups = _groups(node)
            if slot < len(groups):
                for i, value in enumerate(groups[slot][0]):
                    placed.setdefault((gid, i), []).append(value)
    return {key: marked.get(key) or nodes for key, nodes in {**placed, **marked}.items()}


def _plan(kind: str, what: str, code: str, types: list[Type]) -> dict[int, tuple[Type, Type]]:
    """For one call's values of Spark ``types``: those to cast, by index, as (type, Spark's type for it).
    Raises Refused where Spark stops."""
    if any(t[0] == "other" for t in types):
        return {}
    if kind == _ONE_TYPE:
        if _common(types, _no_string) is None:
            raise _refuse(what, code, types)
        return {}
    if kind in (_WIDER, _IN, _CONCAT):
        if kind == _CONCAT:
            arrays = [t[0] == "array" for t in types if t[0] != "null"]
            if not any(arrays):
                return {}
            if not all(arrays) or len(arrays) < len(types):  # a bare NULL is a string to concat
                raise _refuse(what, code, [("string",) if t[0] == "null" else t for t in types])
        common = _common(types, _wider)
        if common is None:
            raise _refuse(what, code, types)
        if kind in (_IN, _ONE_TYPE) or not (_has_string(common) or _has_decimal(common)):
            return {}  # whole numbers, doubles, dates and timestamps widen in DuckDB as in Spark
        # A decimal is cast to its own type too: DuckDB reads a whole number past a bigint as a HUGEINT
        return {i: (t, common) for i, t in enumerate(types) if t != common or t[0] == "dec"}
    array, other = types
    if array[0] == "null":
        return {}
    if array[0] != "array":
        return {}
    if kind == _TIGHTEST_ARRAYS:
        if other[0] == "null":
            return {}
        if other[0] != "array":
            return {}
        element = _tightest(array[1], other[1])
        if element is None:
            raise _refuse(what, code, [array[1], other[1]])
        return {i: (t, ("array", element)) for i, t in enumerate(types) if t[1] != element and t[1][0] != "null"}
    if other[0] == "null":
        return {}
    rule = _no_string if kind == _NO_STRING else _tightest
    element = rule(array[1], other)
    if element is None:
        raise _refuse(what, code, [array[1], other])
    if kind == _TIGHTEST and what == "array_remove()" and array[1] != element:
        return {0: (array, ("array", element))}  # array_remove gives an array of the wider type
    return {}
