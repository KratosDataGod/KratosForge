"""Spark's comparisons and ordering of arrays, structs and maps, for Spark SQL on this PC (WR-271).

Spark orders an array element by element and a struct field by field, with a NULL element or field before
any value: ``array(NULL, 5) < array(1)``, and ``named_struct('a', NULL)`` sorts before ``named_struct('a', 1)``.
DuckDB puts a NULL inside an array or struct after every value, so ``<``, ``BETWEEN``, ``ORDER BY`` (and a
window's), ``min`` and ``max``, ``greatest`` and ``least``, ``array_max`` and ``array_min``, ``sort_array`` and
``array_sort`` answered in the other order. Each is written here over a key DuckDB orders as Spark does: an
array's elements and a struct's fields become ``{'n': value IS NOT NULL, 'v': key of value}``, so a NULL
compares lower. The key reads its value once (a struct through a lambda over a one-element list), so the SQL
grows with the type's size, never with the nesting of the query.

Spark compares two arrays or structs only when their element (field) types meet in one type without losing
digits (TypeCoercion.findTightestCommonType): ``array(1, 1.5) = array(1.0, 1.5)`` stops
([DATATYPE_MISMATCH.BINARY_OP_DIFF_TYPES]), as ``decimal(11,1)`` and ``decimal(2,1)`` are two types, and so
do an int with a decimal(2,1), a string, a boolean or a binary, structs with other field names, and
decimals of two sizes. DuckDB cast them and answered; such a comparison is refused here. Only values whose
types are known as written are checked: literals, casts, arrays and structs of them, ``VALUES`` and the
columns of the bound tables.

Spark can't compare, sort, group or deduplicate a map, or anything holding one, and stops; DuckDB answered.
Each of those is refused by name. ``GROUP BY``, ``DISTINCT``, ``UNION`` and ``EXCEPT`` give 0.0 for a -0.0
they group (Spark's NormalizeFloatingNumbers); DuckDB kept the first value it read. A double (or float)
group key, and a double inside an array or struct, is written as ``x + 0.0`` there.

sql.spark_fixes marks the values (``mark``) before any rewrite, once the names the person wrote are checked
(its lambdas are kf_ names); ``spark_orderings`` types them once the statement's tables are bound
(spark_complex_types calls it), as spark_text_compare does.
"""

from __future__ import annotations

import itertools
import logging
from typing import Any, cast

from sqlglot import exp
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from .spark_decimals import Refused

log = logging.getLogger(__name__)

_KEY = "kf_order"      # (kind, group, detail, has a kf_ name) on each value a marked call compares
_ID = "kf_order_id"    # a mark's place, for finding it again in the typed copy
_OUT = "kf_order_out"  # on a select's output of a DISTINCT, UNION or EXCEPT, or a GROUP BY key
_AT = "kf_order_at"    # on an ORDER BY item
_CALL = "kf_order_call"  # on array_contains and array_position, which spark_complex writes out as text
_T = exp.DataType.Type
_COMPARE: dict[type, str] = {exp.EQ: "=", exp.NEQ: "!=", exp.NullSafeEQ: "<=>", exp.NullSafeNEQ: "<=>"}
_ORDER: dict[type, str] = {exp.LT: "<", exp.LTE: "<=", exp.GT: ">", exp.GTE: ">="}
_ELEMENTS: dict[type, str] = {exp.ArrayPosition: "array_position", exp.ArrayContains: "array_contains",
             exp.ArrayRemove: "array_remove", exp.ArrayDistinct: "array_distinct", exp.ArrayExcept: "array_except",
             exp.ArrayIntersect: "array_intersect"}
_ANON_ELEMENTS = {"ARRAYS_OVERLAP": "arrays_overlap", "ARRAY_UNION": "array_union"}
_INTS = {_T.TINYINT: 1, _T.SMALLINT: 2, _T.INT: 3, _T.BIGINT: 4}
_INT_DIGITS = {1: 3, 2: 5, 3: 10, 4: 20}
_INT_NAMES = {1: "tinyint", 2: "smallint", 3: "int", 4: "bigint"}
_TEXT = {_T.VARCHAR, _T.TEXT, _T.CHAR, _T.NCHAR, _T.NVARCHAR}
_TIMES = {_T.TIMESTAMP, _T.TIMESTAMPTZ, _T.TIMESTAMPLTZ, _T.TIMESTAMPNTZ, _T.DATETIME}
_BINARY = {_T.BINARY, _T.VARBINARY, _T.BLOB}
_FAIL = ("fail",)  # two types with no one type to compare in

Type = tuple[Any, ...]


# ---- marking, before any rewrite


def _has_kf(nodes: list[exp.Expr]) -> bool:
    return any(i.name.lower().startswith("kf_") for n in nodes for i in n.find_all(exp.Identifier))


def _put(nodes: list[Any], kind: str, group: int, detail: Any = None) -> None:
    nodes = [n for n in nodes if isinstance(n, exp.Expr)]
    flag = _has_kf(nodes)
    for role, node in enumerate(nodes):
        if isinstance(node, exp.Expr):
            node.meta[_KEY] = (kind, group, detail, role, flag)


def _sort_detail(node: Any) -> tuple[bool, bool] | None:
    from .sql import _array_sort_by, _sort_nulls_first
    if _array_sort_by(node) is not None:
        return None
    nulls_first = _sort_nulls_first(node)
    if nulls_first is None:
        return None
    asc = node.args.get("asc")
    return (asc is None or bool(asc.this)), nulls_first


def mark(statement: exp.Expression) -> exp.Expression:
    """``statement`` with the values Spark compares or orders marked for ``spark_orderings``. sql.spark_fixes
    calls it before any rewrite, once _no_reserved_names has checked the names the person wrote."""
    from .sql import _SORTS
    groups = itertools.count()
    for node in list(statement.walk()):
        g = next(groups)
        if type(node) in _COMPARE:
            _put([node.this, node.expression], "eq", g, _COMPARE[type(node)])
        elif type(node) in _ORDER:
            _put([node.this, node.expression], "order", g, _ORDER[type(node)])
        elif isinstance(node, exp.Between):
            _put([node.this, node.args.get("low"), node.args.get("high")], "order", g, "BETWEEN")
        elif isinstance(node, exp.In):
            _put([node.this, *node.expressions], "in", g, "IN")
        elif isinstance(node, exp.Ordered):
            _put([node.this], "sort", g, "ORDER BY")
            node.meta[_AT] = True  # ORDER BY 1 is read from the typed copy's select, which names it
        elif isinstance(node, exp.Window):
            _put(list(node.args.get("partition_by") or []), "group", g, "PARTITION BY")
        elif isinstance(node, exp.Group):
            keys = [k for k in node.expressions if not isinstance(k, (exp.Rollup, exp.Cube, exp.GroupingSets))]
            nested = [e for k in node.expressions if isinstance(k, (exp.Rollup, exp.Cube, exp.GroupingSets))
                      for e in k.find_all(exp.Column)]
            _put(keys + nested, "group", g, "GROUP BY")
            node.meta[_AT] = True  # a key qualify writes anew (s.a) is found in the copy by its place
            for key in keys:
                key.meta[_OUT] = True
            for key in node.find_all(exp.Rollup, exp.Cube, exp.GroupingSets):
                for part in key.expressions:
                    part.meta[_OUT] = True
        elif isinstance(node, (exp.Max, exp.Min)) and not node.expressions and not isinstance(node.this, exp.Distinct):
            _put([node.this], "minmax", g, "max" if isinstance(node, exp.Max) else "min")
        elif isinstance(node, (exp.Greatest, exp.Least)):
            _put([node.this, *node.expressions], "greatest", g, "greatest" if isinstance(node, exp.Greatest) else "least")
        elif isinstance(node, (exp.ArrayMax, exp.ArrayMin)):
            _put([node.this], "array_max", g, "array_max" if isinstance(node, exp.ArrayMax) else "array_min")
        elif isinstance(node, _SORTS):
            detail = _sort_detail(node)
            name = "array_sort" if isinstance(node, exp.ArraySort) and "asc" not in node.args else "sort_array"
            if isinstance(node, exp.ArraySort) and "nulls_first" in node.args:
                flag = node.args.get("nulls_first")
                name = "sort_array" if isinstance(flag, exp.Boolean) and flag.this else name
            _put([node.this], "sort_array" if detail is not None else "elements", g, (name, detail))
        elif isinstance(node, exp.Case) and node.this is not None:
            _put([node.this, *(i.this for i in node.args.get("ifs") or [])], "eq", g, "=")
        elif type(node) in _ELEMENTS:
            _put([*node.expressions] if isinstance(node, exp.ArrayIntersect) else
                 [node.this, node.args.get("expression")], "elements", g, (_ELEMENTS[type(node)], None))
        elif isinstance(node, exp.Anonymous) and str(node.this).upper() in _ANON_ELEMENTS:
            _put(list(node.expressions), "elements", g, (_ANON_ELEMENTS[str(node.this).upper()], None))
        elif isinstance(node, exp.ArrayUniqueAgg):
            _put([node.this], "elements", g, ("collect_set", None))
        elif isinstance(node, (exp.ArgMax, exp.ArgMin)) and node.expression is not None:
            _put([node.expression], "in", g, "max_by" if isinstance(node, exp.ArgMax) else "min_by")
        if isinstance(node, (exp.ArrayContains, exp.ArrayPosition)):
            # spark_complex writes these out as text: the call's mark is carried to what it writes
            node.meta[_CALL] = ("call", g, (_ELEMENTS[type(node)], None), 0, False)
        elif isinstance(node, exp.Distinct) and isinstance(node.parent, exp.AggFunc):
            _put(list(node.expressions), "in", g, "DISTINCT")
        if isinstance(node, exp.Select) and node.args.get("distinct") is not None \
                and not node.args["distinct"].args.get("on"):
            for out in node.expressions:
                out.meta[_OUT] = True
        if isinstance(node, (exp.Union, exp.Except)) and node.args.get("distinct"):
            for select in _leaves(node):
                for out in select.expressions:
                    out.meta[_OUT] = True
    return statement


def _leaves(node: exp.Expr) -> list[exp.Select]:
    """The selects a set operation is made of."""
    if isinstance(node, exp.Select):
        return [node]
    if isinstance(node, exp.Subquery):
        return _leaves(node.this)
    if isinstance(node, exp.SetOperation):
        return _leaves(node.this) + _leaves(node.expression)
    return []


# ---- types


def _decimal(dt: exp.DataType) -> Type:
    params = [int(p.this.name) for p in dt.expressions if isinstance(p, exp.DataTypeParam) and p.this.is_int]
    return ("dec", params[0] if params else 10, params[1] if len(params) > 1 else 0)


def _canon(dt: Any) -> Type | None:
    """A type as compared here, from a sqlglot type; None when part of it isn't known."""
    if not isinstance(dt, exp.DataType):
        return None
    t = dt.this
    if t in _INTS:
        return ("int", _INTS[t])
    simple = {_T.FLOAT: ("float",), _T.DOUBLE: ("double",), _T.BOOLEAN: ("bool",), _T.DATE: ("date",),
              _T.NULL: ("null",)}
    if t in simple:
        return simple[t]
    if t == _T.DECIMAL:
        return _decimal(dt)
    if t in _TEXT:
        return ("string",)
    if t in _TIMES:
        return ("ts",)
    if t in _BINARY:
        return ("binary",)
    if t == _T.ARRAY:
        if not dt.expressions:
            return ("array", ("null",))
        inner = _canon(dt.expressions[0])
        return None if inner is None else ("array", inner)
    if t == _T.STRUCT:
        fields = []
        for field in dt.expressions:
            kind = _canon(field.args.get("kind")) if isinstance(field, exp.ColumnDef) else None
            if kind is None:
                return None
            fields.append((field.name, kind))
        return ("struct", tuple(fields))
    if t == _T.MAP and len(dt.expressions) == 2:
        k, v = _canon(dt.expressions[0]), _canon(dt.expressions[1])
        return None if k is None or v is None else ("map", k, v)
    return None


def _has_map(dt: Any) -> bool:
    return isinstance(dt, exp.DataType) and any(d.this == _T.MAP for d in dt.find_all(exp.DataType))


def _has_float(t: Type) -> bool:
    if t[0] in ("float", "double"):
        return True
    if t[0] == "array":
        return _has_float(t[1])
    if t[0] == "struct":
        return any(_has_float(f) for _, f in t[1])
    return False


def spark_name(t: Type) -> str:
    """Spark's name for a type, as its errors write it."""
    kind = t[0]
    if kind == "int":
        return _INT_NAMES[t[1]]
    if kind == "dec":
        return f"decimal({t[1]},{t[2]})"
    if kind == "array":
        return f"array<{spark_name(t[1])}>"
    if kind == "struct":
        return "struct<" + ",".join(f"{n}:{spark_name(f)}" for n, f in t[1]) + ">"
    if kind == "map":
        return f"map<{spark_name(t[1])},{spark_name(t[2])}>"
    return str({"ts": "timestamp", "bool": "boolean", "null": "void"}.get(kind, kind))


def _numeric(t: Type) -> int | None:
    """Spark's numeric precedence for the types findTightestCommonType widens: tinyint to double."""
    if t[0] == "int":
        return t[1]
    return {"float": 5, "double": 6}.get(t[0])


def tightest(a: Type, b: Type) -> Type:
    """Spark's findTightestCommonType of two types, or _FAIL when they have none."""
    if a == b:
        return a
    if a == ("null",):
        return b
    if b == ("null",):
        return a
    ra, rb = _numeric(a), _numeric(b)
    if ra is not None and rb is not None:
        return a if ra >= rb else b
    for whole, dec in ((a, b), (b, a)):
        if whole[0] == "int" and dec[0] == "dec":
            return dec if dec[1] - dec[2] >= _INT_DIGITS[whole[1]] and dec[2] >= 0 else _FAIL
    if {a[0], b[0]} <= {"date", "ts"}:
        return ("ts",)
    if a[0] == b[0] == "array":
        inner = tightest(a[1], b[1])
        return _FAIL if inner == _FAIL else ("array", inner)
    if a[0] == b[0] == "map":
        k, v = tightest(a[1], b[1]), tightest(a[2], b[2])
        return _FAIL if _FAIL in (k, v) else ("map", k, v)
    if a[0] == b[0] == "struct" and len(a[1]) == len(b[1]):
        fields = []
        for (na, fa), (nb, fb) in zip(a[1], b[1], strict=True):
            f = tightest(fa, fb)
            if na.lower() != nb.lower() or f == _FAIL:
                return _FAIL
            fields.append((na, f))
        return ("struct", tuple(fields))
    return _FAIL


def _wider(a: Type, b: Type) -> Type | None:
    """Spark's findWiderCommonType, for the elements of an array written in the query; None when unknown."""
    t = tightest(a, b)
    if t != _FAIL:
        return t
    as_dec = {k: ("dec", _INT_DIGITS[k[1]], 0) for k in (a, b) if k[0] == "int"}
    da, db = as_dec.get(a, a), as_dec.get(b, b)
    if da[0] == db[0] == "dec":
        scale = max(da[2], db[2])
        whole = max(da[1] - da[2], db[1] - db[2])
        return ("dec", min(38, whole + scale), min(38, scale))
    if {da[0], db[0]} <= {"dec", "float", "double"}:
        return ("double",)
    if a[0] == b[0] == "array":
        inner = _wider(a[1], b[1])
        return None if inner is None else ("array", inner)
    if "string" in (a[0], b[0]) and {a[0], b[0]} <= {"string", "int", "dec", "float", "double", "date", "ts"}:
        return ("string",)
    return None


def _literal(node: exp.Literal) -> Type | None:
    if node.is_string:
        return ("string",)
    text = node.this.lower()
    if "e" in text:
        return ("double",)
    if "." in text:
        whole, _, places = text.partition(".")
        digits = len((whole + places).lstrip("0")) or 1
        scale = len(places)
        precision = max(digits, scale)
        return ("dec", precision, scale) if precision <= 38 else ("double",)
    if not text.isdigit():
        return None
    value = int(text)
    return ("int", 3) if value <= 2 ** 31 else ("int", 4) if value <= 2 ** 63 else (
        ("dec", len(text), 0) if len(text) <= 38 else ("double",))


class _Exact:
    """Spark's type of a value in the typed copy, when it is known as written; None otherwise."""

    def __init__(self, schema: MappingSchema) -> None:
        self.schema = schema
        self.mapping = cast(dict[str, dict[str, str]], schema.mapping)

    def of(self, node: Any, depth: int = 0) -> Type | None:
        if depth > 50 or not isinstance(node, exp.Expr):
            return None
        if isinstance(node, (exp.Paren, exp.Alias)):
            return self.of(node.this, depth + 1)
        if isinstance(node, exp.Literal):
            return _literal(node)
        if isinstance(node, exp.Neg):
            inner = self.of(node.this, depth + 1)
            return inner if inner is not None and inner[0] in ("int", "dec", "float", "double") else None
        if isinstance(node, exp.Boolean):
            return ("bool",)
        if isinstance(node, exp.Null):
            return ("null",)
        if isinstance(node, (exp.Cast, exp.TryCast)):
            return _canon(node.args.get("to"))
        if isinstance(node, exp.Array):
            return self._array(node.expressions, depth)
        if isinstance(node, exp.Struct):
            fields = []
            for field in node.expressions:
                if not isinstance(field, exp.PropertyEQ):
                    return None
                kind = self.of(field.expression, depth + 1)
                if kind is None:
                    return None
                fields.append((field.this.name, kind))
            return ("struct", tuple(fields))
        if isinstance(node, exp.Column):
            return self._column(node, depth)
        if isinstance(node, exp.Dot):  # a struct's field
            whole = self.of(node.this, depth + 1)
            name = node.expression.name.lower()
            named = {n.lower(): f for n, f in whole[1]} if whole is not None and whole[0] == "struct" else {}
            return named.get(name)
        if isinstance(node, exp.Bracket) and len(node.expressions) == 1:  # an array's element
            whole = self.of(node.this, depth + 1)
            return whole[1] if whole is not None and whole[0] == "array" else None
        return None

    def _array(self, values: list[exp.Expr], depth: int) -> Type | None:
        out: Type = ("null",)
        for value in values:
            kind = self.of(value, depth + 1)
            if kind is None:
                return None
            wider = _wider(out, kind)
            if wider is None:
                return None
            out = wider
        return ("array", out)

    def _column(self, column: exp.Column, depth: int) -> Type | None:
        select = column.find_ancestor(exp.Select)
        if select is None or not column.table:
            return None
        sources = [select.args.get("from_") or select.args.get("from")] + list(select.args.get("joins") or [])
        for holder in sources:
            source = holder.this if holder is not None else None
            if source is None or source.alias_or_name != column.table:
                continue
            return self._source(source, column.name, depth)
        return None

    def _source(self, source: exp.Expr, name: str, depth: int) -> Type | None:
        if isinstance(source, exp.Subquery):
            inner = source.this
            if isinstance(inner, exp.Values):
                return self._values(inner, source, name, depth)
            if isinstance(inner, exp.Select):
                return self._output(inner, name, depth)
            if isinstance(inner, exp.SetOperation):  # the type the selects' outputs at that place widen to
                leaves = _leaves(inner)
                at = next((i for i, o in enumerate(leaves[0].expressions) if o.alias_or_name == name), None) \
                    if leaves else None
                if at is None or any(len(leaf.expressions) <= at for leaf in leaves):
                    return None
                widened = self._array([leaf.expressions[at] for leaf in leaves], depth)
                return None if widened is None else widened[1]
            return None
        if isinstance(source, exp.Values):
            return self._values(source, source, name, depth)
        if isinstance(source, exp.Table) and not source.args.get("db"):
            with_ = source.find_ancestor(exp.Select)
            while with_ is not None:
                ctes = with_.args.get("with_") or with_.args.get("with")
                for cte in (ctes.expressions if ctes is not None else []):
                    if cte.alias_or_name == source.name and isinstance(cte.this, exp.Select):
                        return self._output(cte.this, name, depth)
                with_ = with_.find_ancestor(exp.Select)
            columns = self.mapping.get(source.name.lower())
            if isinstance(columns, dict) and name.lower() in columns:
                try:
                    return _canon(exp.DataType.build(columns[name.lower()], dialect="duckdb"))
                except Exception:  # noqa: BLE001 - silent-ok: a type this module doesn't read is unknown
                    return None
        return None

    def _values(self, values: exp.Values, aliased: exp.Expr, name: str, depth: int) -> Type | None:
        alias = aliased.args.get("alias")
        columns = [c.name for c in (alias.columns if alias is not None else [])]
        if name not in columns:
            return None
        at = columns.index(name)
        rows = [row.expressions[at] for row in values.expressions
                if isinstance(row, exp.Tuple) and len(row.expressions) > at]
        if len(rows) != len(values.expressions):
            return None
        kind = self._array(rows, depth)
        return None if kind is None else kind[1]

    def _output(self, select: exp.Select, name: str, depth: int) -> Type | None:
        for out in select.expressions:
            if out.alias_or_name == name:
                return self.of(out, depth + 1)
        return None


# ---- what is written


def _shape(dt: Any) -> Type:
    """The parts of a type a key is written for: arrays and structs; anything else compares as it is."""
    if isinstance(dt, exp.DataType) and dt.this == _T.ARRAY and dt.expressions:
        return ("array", _shape(dt.expressions[0]))
    if isinstance(dt, exp.DataType) and dt.this == _T.STRUCT and dt.expressions:
        return ("struct", tuple(_shape(f.args.get("kind")) if isinstance(f, exp.ColumnDef) else ("leaf",)
                                for f in dt.expressions))
    return ("leaf",)


def _key(shape: Type, value: str, depth: int = 0) -> str:
    """DuckDB SQL that DuckDB orders as Spark orders ``value`` (of ``shape``): each element or field becomes
    {'n': it IS NOT NULL, 'v': its key}, so a NULL compares lower than any value. ``value`` is read once."""
    if shape[0] == "array":
        e = f"kf_oe{depth}"
        return f"LIST_TRANSFORM({value}, {e} -> {{'n': {e} IS NOT NULL, 'v': {_key(shape[1], e, depth + 1)}}})"
    if shape[0] == "struct":
        s = f"kf_os{depth}"
        fields = ", ".join(
            f"'f{i}': {{'n': STRUCT_EXTRACT_AT({s}, {i}) IS NOT NULL, "
            f"'v': {_key(f, f'STRUCT_EXTRACT_AT({s}, {i})', depth + 1)}}}" for i, f in enumerate(shape[1], 1))
        return f"(LIST_TRANSFORM([{value}], {s} -> CASE WHEN {s} IS NULL THEN NULL ELSE {{{fields}}} END))[1]"
    return value


def _norm(t: Type, value: str, depth: int = 0) -> str:
    """DuckDB SQL for ``value`` (of type ``t``, which holds a double or float) with each -0.0 made 0.0, as
    Spark's NormalizeFloatingNumbers gives a grouped value. ``value`` is read once."""
    if t[0] == "double":
        return f"({value} + CAST(0 AS DOUBLE))"
    if t[0] == "float":
        return f"({value} + CAST(0 AS FLOAT))"
    if t[0] == "array":
        e = f"kf_on{depth}"
        return f"LIST_TRANSFORM({value}, {e} -> {_norm(t[1], e, depth + 1)})"
    if t[0] == "struct":
        s = f"kf_ot{depth}"
        fields = ", ".join(f"'{name.replace(chr(39), chr(39) * 2)}': "
                           + (_norm(f, f'STRUCT_EXTRACT_AT({s}, {i})', depth + 1) if _has_float(f)
                              else f"STRUCT_EXTRACT_AT({s}, {i})")
                           for i, (name, f) in enumerate(t[1], 1))
        return f"(LIST_TRANSFORM([{value}], {s} -> CASE WHEN {s} IS NULL THEN NULL ELSE {{{fields}}} END))[1]"
    return value


def _template(text: str, args: list[exp.Expr]) -> exp.Expr:
    from .sql import _with_args
    return _with_args(text, cast(list[exp.Expression], args))


def _keyed(node: exp.Expr, shape: Type) -> exp.Expr:
    return _template(_key(shape, "fj_arg0"), [node])


def _pick(shape: Type, which: str, values: str) -> str:
    """The largest (``which`` LIST_MAX) or smallest (LIST_MIN) of the DuckDB list ``values`` in Spark's order,
    its NULLs left out; NULL when there is none."""
    return (f"STRUCT_EXTRACT({which}(LIST_TRANSFORM(LIST_FILTER({values}, kf_og -> kf_og IS NOT NULL), "
            f"kf_og -> {{'k': {_key(shape, 'kf_og', 1)}, 'v': kf_og}})), 'v')")


def _sorted(shape: Type, up: bool, nulls_first: bool) -> str:
    flag = "IS NOT NULL" if nulls_first == up else "IS NULL"
    return (f"LIST_TRANSFORM(LIST_SORT(LIST_TRANSFORM(fj_arg0, kf_oa -> {{'n': kf_oa {flag}, "
            f"'k': {_key(shape, 'kf_oa', 1)}, 'v': kf_oa}}), '{'ASC' if up else 'DESC'}', 'NULLS FIRST'), "
            "kf_oa -> STRUCT_EXTRACT(kf_oa, 'v'))")


# ---- refusals


_RESERVED = ("{what} starts with kf_, which can't be used where arrays, structs or maps are compared or sorted on this "
             "PC: names that start with kf_ are reserved here, as Spark's order is written with them, so nothing ran. "
             "Rename it (AS ... or another lambda argument name) and run it again.")
_MAP_WHAT = {"eq": "compare", "order": "compare", "in": "compare", "call": "compare", "elements": "compare",
             "sort": "order", "sort_array": "order", "greatest": "order", "array_max": "order", "minmax": "order",
             "group": "group by"}


def _map_refusal(kind: str, detail: Any, dt: exp.DataType) -> Refused:
    name = detail[0] if isinstance(detail, tuple) else detail
    where = f"`{name}`" if name in ("=", "!=", "<=>", "<", "<=", ">", ">=") else name
    return Refused(f"Spark stops on {where} with a {dt.sql('spark').lower()}: it can't {_MAP_WHAT[kind]} a map "
                   "([DATATYPE_MISMATCH.INVALID_ORDERING_TYPE]), so nothing ran. Use the map's entries instead, which "
                   "Spark can order: array_sort(map_entries(m)), or map_keys(m) and map_values(m).")


def _type_refusal(op: str, a: Type, b: Type) -> Refused:
    return Refused(f"Spark stops on {op} between a {spark_name(a)} and a {spark_name(b)}: their elements or fields have "
                   "no one type to compare in ([DATATYPE_MISMATCH.BINARY_OP_DIFF_TYPES]), so nothing ran. Cast one side "
                   f"to the other's type first, for example CAST(... AS {spark_name(a).upper()}).")


# ---- typed, once the tables are bound


def spark_orderings(statement: exp.Expr, conn: Any, schema: MappingSchema | None = None) -> exp.Expr:
    """``statement`` with Spark's comparisons and order of arrays, structs and maps (see the module docstring),
    typed against the connection's tables (or ``schema``). A refusal is raised; a statement whose types can't
    be worked out is returned as it was."""
    marked = [n for n in statement.walk() if any(k in n.meta for k in (_KEY, _OUT, _AT, _CALL))]
    sets = any(isinstance(n, (exp.Union, exp.Except, exp.Intersect)) or
               (isinstance(n, exp.Select) and n.args.get("distinct") is not None) or
               (isinstance(n, exp.Group) and n.args.get("all")) for n in statement.walk())
    if not marked and not sets:
        return statement
    try:
        if schema is None:
            from .spark_decimals import _schema
            schema = _schema(conn)
        for i, node in enumerate(marked):
            node.meta[_ID] = i
        copy = qualify(statement.copy(), schema=schema, dialect="spark", validate_qualify_columns=False,
                       quote_identifiers=False, identify=False)
        annotate_types(copy, schema=schema, dialect="spark")
        by_id = {n.meta[_ID]: n for n in copy.walk() if _ID in n.meta}
    except Exception as exc:  # noqa: BLE001 - a fix of comparisons only: an untyped statement runs as it was; logged
        log.info("Spark order of complex values not applied: %s: %s", type(exc).__name__, exc)
        return _unmark(statement)
    finally:
        for node in marked:
            node.meta.pop(_ID, None)
    try:
        _refuse_set_maps(copy)
        exact = _Exact(schema)
        jobs = _jobs(marked, by_id, exact)
        holder = exp.Tuple(expressions=[statement])
        if jobs and any(c.lower().startswith("kf_") for cols in schema.mapping.values() if isinstance(cols, dict)
                        for c in cols):
            raise Refused(_RESERVED.format(what="A column of a table or view"))
        for _depth, job in sorted(jobs, key=lambda j: -j[0]):
            job()
        _normalize(marked, by_id, exact)
        statement = cast(exp.Expr, holder.expressions[0])
        statement.pop()
    finally:
        _unmark(statement)
    return statement


def _unmark(statement: exp.Expr) -> exp.Expr:
    for node in statement.walk():
        node.meta.pop(_KEY, None)
        node.meta.pop(_OUT, None)
        node.meta.pop(_AT, None)
        node.meta.pop(_CALL, None)
    return statement


def _refuse_set_maps(copy: exp.Expr) -> None:
    """DISTINCT, UNION, INTERSECT and EXCEPT of a map stop in Spark ([UNSUPPORTED_FEATURE.SET_OPERATION_ON_MAP_TYPE])."""
    for select in copy.find_all(exp.Select):
        group = select.args.get("group")
        for out in select.expressions if group is not None and group.args.get("all") else []:
            if _has_map(out.type) and not out.find(exp.AggFunc) and _trusted(out):
                raise _map_refusal("group", "GROUP BY ALL", out.type)
    selects = [s for s in copy.find_all(exp.Select) if s.args.get("distinct") is not None]
    for op in copy.find_all(exp.Union, exp.Except, exp.Intersect):
        if not isinstance(op, exp.Union) or op.args.get("distinct"):
            selects += _leaves(op)
    for select in selects:
        for out in select.expressions:
            if _has_map(out.type) and _trusted(out):
                raise Refused(f"Spark can't deduplicate a map, and this query does (DISTINCT, UNION, INTERSECT or "
                              f"EXCEPT) with the column {out.alias_or_name}, a {out.type.sql('spark').lower()}, so "
                              "nothing ran: Spark stops on it too ([UNSUPPORTED_FEATURE.SET_OPERATION_ON_MAP_TYPE]). "
                              "Use UNION ALL, or deduplicate on the map's entries: array_sort(map_entries(m)).")


def _depth(node: exp.Expr) -> int:
    depth, parent = 0, node.parent
    while parent is not None:
        depth, parent = depth + 1, parent.parent
    return depth


def _jobs(marked: list[exp.Expr], by_id: dict[int, exp.Expr], exact: _Exact) -> list[tuple[int, Any]]:
    """Refuses what Spark stops on, and lists the rewrites as (depth, write), to run deepest first."""
    groups: dict[tuple[str, int], list[tuple[exp.Expr, exp.Expr | None]]] = {}
    marks: dict[tuple[str, int], tuple[Any, ...]] = {}
    copies: dict[int, exp.Expr | None] = {}
    for i, node in enumerate(marked):
        copies[id(node)] = by_id.get(i)
        for key in (_KEY, _CALL):
            if key in node.meta:
                marks[(key, node.meta[key][1])] = node.meta[key]
                groups.setdefault((key, node.meta[key][1]), []).append((node, by_id.get(i)))
    jobs: list[tuple[int, Any]] = []
    for group, members in groups.items():
        kind, _g, detail, _role, flag = marks[group]
        typed = [_type_of(c) if c is not None else None for _, c in members]
        if kind == "call":  # a call spark_complex wrote out: the array and value its own call reads
            calls = [d for _, c in members if c is not None for d in c.find_all(exp.ArrayContains, exp.Anonymous)
                     if not isinstance(d, exp.Anonymous) or str(d.this).upper() == "LIST_POSITION"]
            typed = [_type_of(v) for v in calls[0].iter_expressions()] if calls else []
        for dt in typed:
            if _has_map(dt):
                raise _map_refusal(kind, detail, cast(exp.DataType, dt))
        if kind in ("eq", "order"):
            kinds = [exact.of(c) if c is not None else None for _, c in members]
            for other in kinds[1:]:
                if kinds[0] is not None and other is not None and _FAIL == tightest(kinds[0], other) \
                        and {kinds[0][0], other[0]} & {"array", "struct", "map"}:
                    raise _type_refusal("=" if detail == "!=" else detail, kinds[0], other)
        job = _job(kind, detail, members, typed, copies)
        if job is not None:
            if flag:
                raise Refused(_RESERVED.format(what="A name in this comparison"))
            jobs.append(job)
    return jobs


def _sourced(column: exp.Column, depth: int = 0) -> bool:
    """Whether sqlglot's type of a column in the typed copy can be trusted: it comes from a table, a view, a
    subquery or VALUES of its own select, or names one of that select's outputs that does. A column of a
    LATERAL VIEW, a lambda's argument or an outer query's column isn't (sqlglot types an exploded column as
    the array itself)."""
    holder = column.find_ancestor(exp.Select, exp.SetOperation)
    select = _leaves(holder)[0] if isinstance(holder, exp.SetOperation) and _leaves(holder) else holder
    if not isinstance(select, exp.Select) or depth > 20:
        return False
    if not column.table or select is not holder:  # an output's name (a UNION's ORDER BY names the first select's)
        outs = [o for o in select.expressions if o.alias_or_name == column.name and o is not column.parent]
        return bool(outs) and _trusted(outs[0], depth + 1)
    sources = [select.args.get("from_") or select.args.get("from")] + list(select.args.get("joins") or [])
    return any(h is not None and isinstance(h.this, (exp.Table, exp.Subquery, exp.Values))
               and h.this.alias_or_name == column.table for h in sources)


def _trusted(node: Any, depth: int = 0) -> bool:
    return isinstance(node, exp.Expr) and all(_sourced(c, depth) for c in node.find_all(exp.Column)) \
        and not node.find(exp.Lambda, exp.Explode, exp.Unnest)


def _type_of(node: Any, depth: int = 0) -> Any:
    """``node``'s type in the typed copy, when it can be trusted (else None); an array of values sqlglot
    couldn't type all of takes the type of one it could, and an array's element the array's element type."""
    if depth == 0 and not _trusted(node):
        return None
    dt = node.type if isinstance(node, exp.Expr) else None
    if isinstance(node, exp.Anonymous) and str(node.this).upper() == "LIST_SORT" and node.expressions:
        return _type_of(node.expressions[0], depth + 1)  # sort_array and array_sort, as spark_fixes wrote them
    if depth > 50 or _shape(dt)[0] != "leaf" or not isinstance(node, (exp.Array, exp.Bracket, exp.Paren)):
        return dt
    if isinstance(node, exp.Paren):
        return _type_of(node.this, depth + 1)
    if isinstance(node, exp.Bracket):
        whole = _type_of(node.this, depth + 1)
        return whole.expressions[0] if isinstance(whole, exp.DataType) and whole.this == _T.ARRAY \
            and whole.expressions else dt
    for value in node.expressions:
        inner = _type_of(value, depth + 1)
        if _shape(inner)[0] != "leaf":
            return exp.DataType(this=_T.ARRAY, expressions=[inner], nested=True)
    return dt


def _nested(dt: Any) -> Type | None:
    shape = _shape(dt)
    return shape if shape[0] != "leaf" else None


def _job(kind: str, detail: Any, members: list[tuple[exp.Expr, exp.Expr | None]], typed: list[Any],
         copies: dict[int, exp.Expr | None]) -> tuple[int, Any] | None:
    first = members[0][0]
    op = first.parent
    shapes = [_nested(dt) for dt in typed]
    shape = next((s for s in shapes if s is not None), None)
    if op is None:
        return None
    if kind == "order" and shape is not None and isinstance(op, (*_ORDER, exp.Between)):
        keys = ("this", "expression") if not isinstance(op, exp.Between) else ("this", "low", "high")
        others = [s for s in shapes]

        def compare() -> None:
            for key, own in zip(keys, others, strict=False):
                value = op.args.get(key)
                if value is not None and not isinstance(value, exp.Null):
                    op.set(key, _keyed(value, own or shape))
        return _depth(op), compare
    if kind == "sort" and isinstance(op, exp.Ordered):
        return _order_job(op, copies.get(id(op)), shape)
    if shape is None and kind not in ("array_max", "sort_array"):
        return None
    if kind == "minmax" and isinstance(op, (exp.Max, exp.Min)) and shape is not None:
        return _depth(op), lambda: _minmax(op, shape)
    if kind == "greatest" and isinstance(op, (exp.Greatest, exp.Least)) and shape is not None:
        which = "LIST_MAX" if detail == "greatest" else "LIST_MIN"
        def pick() -> None:
            values = [op.this, *op.expressions]  # as the deeper rewrites left them
            listed = "[" + ", ".join(f"fj_arg{i}" for i in range(len(values))) + "]"
            op.replace(_template(_pick(shape, which, listed), values))
        return _depth(op), pick
    element = _shape(typed[0]) if typed else ("leaf",)
    inner = element[1] if element[0] == "array" else None
    if inner is None or inner[0] == "leaf":
        return None
    if kind == "array_max" and (isinstance(op, (exp.ArrayMax, exp.ArrayMin)) or str(op.this).upper() in
                                ("LIST_MAX", "LIST_MIN")):
        which = "LIST_MAX" if detail == "array_max" else "LIST_MIN"
        return _depth(op), lambda: op.replace(_template(_pick(inner, which, "fj_arg0"), [
            op.expressions[0] if isinstance(op, exp.Anonymous) else op.this]))
    if kind == "sort_array" and isinstance(op, exp.Anonymous) and str(op.this).upper() == "LIST_SORT":
        up, nulls_first = detail[1]
        return _depth(op), lambda: op.replace(_template(_sorted(inner, up, nulls_first), [op.expressions[0]]))
    return None


def _minmax(op: exp.Expr, shape: Type) -> None:
    """max(x) of an array or struct as the x with the largest key: MAX of {'k': key, 'v': x}, NULL x left out."""
    op.set("this", _template(f"(LIST_TRANSFORM([fj_arg0], kf_om -> CASE WHEN kf_om IS NULL THEN NULL ELSE "
                             f"{{'k': {_key(shape, 'kf_om', 1)}, 'v': kf_om}} END))[1]", [op.this]))
    target: exp.Expr = op
    while isinstance(target.parent, (exp.Filter, exp.Window)) and target.arg_key == "this":
        target = target.parent
    holder = exp.Anonymous(this="STRUCT_EXTRACT", expressions=[exp.Null(), exp.Literal.string("v")])
    target.replace(holder)
    holder.set("expressions", [target, exp.Literal.string("v")])


def _order_job(ordered: exp.Ordered, typed: exp.Expr | None, shape: Type | None) -> tuple[int, Any] | None:
    """ORDER BY a key. ``typed``: the ORDER BY item in the typed copy."""
    order = ordered.parent
    holder = order.parent if order is not None else None
    target: exp.Expr | None = None
    if isinstance(ordered.this, exp.Literal) and ordered.this.is_int and isinstance(holder, exp.Query):
        # ORDER BY 1: the select's output, read by its name (a star is expanded in the typed copy)
        if typed is None or typed.parent is None:
            return None
        query: Any = typed.parent.parent
        while isinstance(query, exp.SetOperation):
            query = query.this
        at = int(ordered.this.name) - 1
        if not isinstance(query, exp.Select) or not 0 <= at < len(query.expressions):
            return None
        out = query.expressions[at]
        shape = _nested(_type_of(out))
        target = exp.column(out.alias_or_name, quoted=True)
    if shape is None:
        return None

    def write() -> None:
        if isinstance(holder, exp.SetOperation) and holder.args.get("order") is order:
            _order_outside(holder)
        ordered.set("this", _keyed(target if target is not None else ordered.this, shape))
    return _depth(ordered), write


def _order_outside(op: exp.SetOperation) -> None:
    """A UNION's ORDER BY can only name its columns in DuckDB: the UNION is read as a subquery, ordered outside."""
    outer = exp.Select(expressions=[exp.Star()])
    for arg in ("order", "limit", "offset"):
        if op.args.get(arg) is not None:
            outer.set(arg, op.args[arg])
            op.set(arg, None)
    op.replace(outer)
    outer.set("from_" if "from_" in exp.Select.arg_types else "from",
              exp.From(this=exp.Subquery(this=op, alias=exp.TableAlias(this=exp.to_identifier("kf_ou")))))


# ---- -0.0 grouped as 0.0


def _same(a: exp.Expr, b: exp.Expr) -> bool:
    if isinstance(a, exp.Column) and isinstance(b, exp.Column):
        return a.name.lower() == b.name.lower() and (not a.table or not b.table or a.table == b.table)
    return a == b


def _normalize(marked: list[exp.Expr], by_id: dict[int, exp.Expr], exact: _Exact) -> None:
    copies = {id(n): by_id.get(i) for i, n in enumerate(marked)}
    outs = []
    for n in marked:
        typed = copies.get(id(n))
        group = copies.get(id(n.parent)) if isinstance(n.parent, exp.Group) else None
        if typed is None and group is not None and n.arg_key == "expressions" and n.index is not None \
                and n.index < len(group.expressions):
            typed = group.expressions[n.index]
        if n.meta.get(_OUT):
            outs.append((n, exact.of(typed) if typed is not None else None))
    for node, kind in outs:
        if kind is None or not _has_float(kind) or node.parent is None:
            continue
        group = node.parent if isinstance(node.parent, exp.Group) else node.find_ancestor(exp.Group)
        if group is not None and isinstance(group.parent, exp.Select):
            _normalize_key(group.parent, node, kind)
        elif isinstance(node.parent, exp.Select) and node.arg_key == "expressions":
            _normalize_output(node, kind)


def _normalized(node: exp.Expr, kind: Type) -> exp.Expr:
    return _template(_norm(kind, "fj_arg0"), [node])


def _normalize_output(node: exp.Expr, kind: Type) -> None:
    if isinstance(node, exp.Alias):
        node.set("this", _normalized(node.this, kind))
    elif isinstance(node, exp.Column):
        node.replace(exp.alias_(_normalized(node.copy(), kind), node.name, quoted=True))


def _under(node: exp.Expr, nodes: list[exp.Expr]) -> bool:
    parent: exp.Expr | None = node
    while parent is not None:
        if any(parent is n for n in nodes):
            return True
        parent = parent.parent
    return False


def _normalize_key(select: exp.Select, key: exp.Expr, kind: Type) -> None:
    """GROUP BY key: the key and each use of it outside an aggregate, read as the normalized key, which DuckDB
    still matches to the GROUP BY. Left as it was when a column of the key is used another way there."""
    if isinstance(key, exp.Literal) and key.is_int:
        at = int(key.name) - 1
        if 0 <= at < len(select.expressions):
            _normalize_output(select.expressions[at], kind)
        return
    trial = select.copy()
    items: list[exp.Expr] = []
    for name in ("expressions", "having", "qualify", "order"):
        part = trial.args.get(name)
        items += part if isinstance(part, list) else [part] if part is not None else []
    columns = {c.name.lower() for c in key.find_all(exp.Column)}
    found: list[exp.Expr] = []
    for item in items:
        for n in item.walk():
            if _same(n, key) and not n.find_ancestor(exp.AggFunc, exp.Subquery) and not _under(n, found):
                found.append(n)
    for item in items:
        for c in item.find_all(exp.Column):
            if c.name.lower() in columns and not c.find_ancestor(exp.AggFunc) and not _under(c, found):
                return  # used another way: DuckDB couldn't match it to the normalized key
    for n in found:
        if isinstance(n.parent, exp.Select) and n.arg_key == "expressions" and isinstance(n, exp.Column):
            n.replace(exp.alias_(_normalized(n.copy(), kind), n.name, quoted=True))
        else:
            n.replace(_normalized(n.copy(), kind))
    for name in ("expressions", "having", "qualify", "order"):
        select.set(name, trial.args.get(name))
    key.replace(_normalized(key.copy(), kind))
