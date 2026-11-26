"""Spark's result types for CASE, if, coalesce, nvl, nvl2, greatest, least and set operations, for Spark SQL on
this PC (WR-259).

Spark (ANSI off, as Fabric runs) finds the values' wider type (TypeCoercion.findWiderCommonType): text with a
number, a date or a timestamp is STRING, so ``CASE WHEN c THEN 1 ELSE 'a' END`` is the string '1' and
``coalesce(1.0D, 'x')`` is '1.0'; an integer with a decimal is the decimal that holds both, with a BIGINT taken
as decimal(20,0), so ``coalesce(1L, 2.5)`` is a decimal(21,1); and a boolean with a number or text, or binary
with text, is an error. greatest and least widen the same way but never turn a number into text, so
``greatest(1, '2')`` is an error too. DuckDB casts the text to the number instead (1, or a stop on 'a'), types a
BIGINT as decimal(19,0), and turns a boolean into a number. A UNION, INTERSECT or EXCEPT widens each column the
same way, pair by pair from the left (WidenSetOperationTypes).

Only the query's own calls and set operations are typed (``mark`` marks them before other rewrites write their
own), and only when sqlglot can type every value; anything else runs as before. A value turned to text is
written as Spark's CAST to STRING writes it (sql.spark_text_cast), read once through a lambda. A mix Spark stops
on is refused by name (spark_decimals.Refused).
"""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlglot import exp
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from .spark_decimals import MAX, Refused, _literal_type

log = logging.getLogger(__name__)

_T = exp.DataType.Type
_MINE = "kf_branches_mine"
_KEY = "kf_branches"
_LAMBDA = "kf_branch_text"
_CALLS = (exp.Case, exp.If, exp.Coalesce, exp.Nvl2, exp.Greatest, exp.Least)
_SETS = (exp.Union, exp.Intersect, exp.Except)
_WHOLE = {_T.TINYINT: 3, _T.SMALLINT: 5, _T.INT: 10, _T.BIGINT: 20}
_TEXT = {_T.VARCHAR, _T.TEXT, _T.CHAR, _T.NCHAR, _T.NVARCHAR}
_SPARK_NAMES = {"text": "STRING", "bool": "BOOLEAN", "binary": "BINARY", "date": "DATE", "timestamp": "TIMESTAMP",
                "double": "DOUBLE", "float": "FLOAT"}

# A value's kind: ("null",), ("int", digits), ("decimal", p, s), ("double",), ("float",), ("text",), ("bool",),
# ("binary",), ("date",), ("timestamp",). None: one this module can't tell, which leaves the call as it was.
Kind = tuple[Any, ...]


def mark(statement: exp.Expression) -> exp.Expression:
    """Mark the query's own CASE, if, coalesce and nvl2 calls and set operations, before other rewrites write
    theirs (a pmod's CASE, a template's coalesce), so only these are typed."""
    for node in statement.find_all(*_CALLS, *_SETS):
        node.meta[_MINE] = True
    return statement


def _spark_name(kind: Kind) -> str:
    if kind[0] == "int":
        return {3: "TINYINT", 5: "SMALLINT", 10: "INT", 20: "BIGINT"}[kind[1]]
    if kind[0] == "decimal":
        return f"DECIMAL({kind[1]},{kind[2]})"
    return _SPARK_NAMES[kind[0]]


def _as_decimal(kind: Kind) -> tuple[int, int]:
    return (kind[1], 0) if kind[0] == "int" else (kind[1], kind[2])


def wider(a: Kind, b: Kind) -> Kind | None:
    """Spark's findWiderTypeForTwo (ANSI off) for two kinds; None when Spark has no common type (an error)."""
    if a[0] == "null":
        return b
    if b[0] == "null" or a == b:
        return a
    numbers = ("int", "decimal", "double", "float")
    if a[0] in numbers and b[0] in numbers:
        if a[0] == b[0] == "int":
            return a if a[1] >= b[1] else b
        if "double" in (a[0], b[0]) or ("float" in (a[0], b[0]) and "decimal" in (a[0], b[0])):
            return ("double",)
        if "float" in (a[0], b[0]):
            return ("float",)
        (p1, s1), (p2, s2) = _as_decimal(a), _as_decimal(b)
        scale = max(s1, s2)
        return ("decimal", min(max(p1 - s1, p2 - s2) + scale, MAX), min(scale, MAX))  # DecimalType.bounded
    if {a[0], b[0]} == {"date", "timestamp"}:
        return ("timestamp",)
    if "text" in (a[0], b[0]):
        other = b if a[0] == "text" else a
        return None if other[0] in ("bool", "binary") else ("text",)
    return None


def _wider_all(kinds: list[Kind]) -> Kind | None:
    """Spark's findWiderCommonType: the text kinds first, then the rest, folded from the left."""
    ordered = [k for k in kinds if k[0] == "text"][:1] + [k for k in kinds if k[0] != "text"]
    out: Kind | None = ("null",)
    for kind in ordered:
        out = wider(out, kind) if out is not None else None
    return out


def _kind(node: exp.Expr, copies: dict[int, exp.Expr]) -> Kind | None:
    while isinstance(node, exp.Paren):
        node = node.this
    if _KEY in node.meta:  # a call typed here already: its Spark type, not the one sqlglot gave it
        return cast(Kind, node.meta[_KEY])
    if isinstance(node, exp.Null):
        return ("null",)
    literal = node.this if isinstance(node, exp.Neg) else node
    if isinstance(literal, exp.Literal) and not literal.is_string:
        text = literal.this.lower()
        if "e" in text:
            return ("double",)
        found = _literal_type(literal)
        if found is None:
            return None
        if "." in text:
            return ("decimal", *found)
        return ("int", 10 if abs(int(text)) < 2 ** 31 else 20)
    copy = copies.get(id(node))
    kind = copy.type if copy is not None else None
    if not isinstance(kind, exp.DataType):
        return None
    if kind.this in _WHOLE:
        return ("int", _WHOLE[kind.this])
    if kind.this == _T.DECIMAL:
        params = [str(p.this.this) for p in kind.expressions if isinstance(p.this, exp.Literal)]
        if len(params) == 2 == len(kind.expressions) and all(p.isdigit() for p in params):
            return ("decimal", int(params[0]), int(params[1]))
        return None
    simple = {_T.DOUBLE: "double", _T.FLOAT: "float", _T.BOOLEAN: "bool", _T.DATE: "date", _T.TIMESTAMP: "timestamp",
              _T.TIMESTAMPTZ: "timestamp", _T.TIMESTAMPLTZ: "timestamp", _T.DATETIME: "timestamp", _T.BINARY: "binary",
              _T.VARBINARY: "binary", _T.BLOB: "binary", _T.NULL: "null"}
    if kind.this in _TEXT:
        return ("text",)
    return (simple[kind.this],) if kind.this in simple else None


def _values(node: exp.Expr) -> list[exp.Expr]:
    """The values a call can give, in order."""
    if isinstance(node, exp.Case):
        values = [i.args["true"] for i in node.args.get("ifs") or []]
        return values + ([node.args["default"]] if node.args.get("default") is not None else [])
    if isinstance(node, exp.If):
        return [node.args["true"]] + ([node.args["false"]] if node.args.get("false") is not None else [])
    if isinstance(node, exp.Nvl2):
        return [node.args["true"]] + ([node.args["false"]] if node.args.get("false") is not None else [])
    return [node.this, *node.expressions]


def _converted(value: exp.Expr, have: Kind, want: Kind) -> exp.Expr:
    """``value`` (of kind ``have``) as Spark casts it to ``want``."""
    from .sql import _with_args, spark_text_cast
    if want[0] == "text":
        if have[0] in ("double", "float", "timestamp"):
            # spark_text_cast names its value several times: it is read once, through a lambda.
            return exp.paren(_with_args(f"(LIST_TRANSFORM([fj_arg0], {_LAMBDA} -> {spark_text_cast(_LAMBDA)}))[1]",
                                        [cast(Any, value)]))
        return exp.cast(value, exp.DataType.build("VARCHAR"))
    if want[0] == "decimal":
        # A value past the decimal (bounded at 38 digits) is NULL with ANSI off, as Spark's cast gives it.
        return exp.TryCast(this=value, to=exp.DataType.build(f"DECIMAL({want[1]}, {want[2]})"))
    if want[0] in ("int", "float", "timestamp"):  # an inner set operation's column, cast as DuckDB widens it
        return exp.cast(value, exp.DataType.build(_spark_name(want)))
    return exp.cast(value, exp.DataType.build("DOUBLE"))


def _needs(have: Kind, want: Kind) -> bool:
    """Whether DuckDB's own type for a value of kind ``have`` among ``want`` differs from Spark's."""
    if have[0] == "null" or have == want:
        return False
    if want[0] == "text":
        return True
    if want[0] == "decimal":
        return have[0] in ("int", "decimal")
    return want[0] == "double" and have[0] in ("decimal", "float")


def _refuse(what: str, kinds: list[Kind]) -> Refused:
    names = ", ".join(dict.fromkeys(_spark_name(k) for k in kinds if k[0] != "null"))
    return Refused(f"{what} mixes values of types Spark has no common type for ({names}), so nothing ran. Spark stops "
                   "on it too ([DATATYPE_MISMATCH]). Cast the values to one type, for example CAST(x AS STRING) "
                   "or CAST(flag AS INT).")


def spark_branch_types(statement: exp.Expr, conn: Any, schema: MappingSchema | None = None) -> exp.Expr:
    """``statement`` with Spark's result types for the query's own CASE, if, coalesce, nvl2, UNION, INTERSECT
    and EXCEPT (see the module docstring), typed against the connection's tables (or ``schema``). A mix
    Spark stops on is refused (spark_decimals.Refused)."""
    calls = [n for n in statement.walk() if isinstance(n, _CALLS) and n.meta.pop(_MINE, False)]
    sets = [n for n in statement.walk() if isinstance(n, _SETS) and n.meta.pop(_MINE, False)]
    if not calls and not sets:
        return statement
    values = [v for node in calls for v in _values(node)]
    for node in sets:
        for select in (node.left, node.right):
            if isinstance(select, exp.Select):
                values += [p.this if isinstance(p, exp.Alias) else p for p in select.expressions]
    try:
        if schema is None:
            from .spark_decimals import _schema
            schema = _schema(conn)
        for i, value in enumerate(values):
            value.meta[_KEY] = i
        copy = qualify(statement.copy(), schema=schema, dialect="spark", validate_qualify_columns=False,
                       quote_identifiers=False, identify=False, expand_stars=False)
        annotate_types(copy, schema=schema, dialect="spark")
        by_mark = {n.meta[_KEY]: n for n in copy.walk() if _KEY in n.meta}
        copies = {id(v): by_mark[i] for i, v in enumerate(values) if i in by_mark}
    except Exception as exc:  # noqa: BLE001 - a type fix only: an untyped statement runs as it was; logged
        log.info("Spark result types of CASE and UNION not applied: %s: %s", type(exc).__name__, exc)
        return statement
    finally:
        for value in values:
            value.meta.pop(_KEY, None)
    if any(i.name.lower() == _LAMBDA for i in statement.find_all(exp.Identifier)) or any(
            _LAMBDA in cast(dict[str, str], columns) for columns in schema.mapping.values()):
        log.info("Spark result types of CASE and UNION not applied: a column is named like the lambda %s", _LAMBDA)
        return statement
    try:
        for call in reversed(calls):  # inner calls first: an outer one reads their values
            _call_types(call, copies)
        for operation in sets:
            if operation.meta.get(_KEY) is None:
                _set_types(operation, copies)
    finally:
        for done in [*calls, *sets]:
            done.meta.pop(_KEY, None)
    return statement


_CALL_NAMES: dict[type, str] = {exp.Case: "A CASE", exp.If: "An if()", exp.Coalesce: "A coalesce()",
                                exp.Nvl2: "An nvl2()", exp.Greatest: "A greatest()", exp.Least: "A least()"}
_SET_NAMES: dict[type, str] = {exp.Union: "A UNION", exp.Intersect: "An INTERSECT", exp.Except: "An EXCEPT"}


def _call_types(node: exp.Expr, copies: dict[int, exp.Expr]) -> None:
    """One call's values cast to Spark's wider type of them all; a mix Spark stops on is refused."""
    kinds = [_kind(v, copies) for v in _values(node)]
    if any(k is None for k in kinds):
        return
    known = cast(list[Kind], kinds)
    want = _wider_all(known)
    if isinstance(node, (exp.Greatest, exp.Least)) and want is not None and want[0] == "text" and any(
            k[0] not in ("text", "null") for k in known):
        want = None  # greatest and least widen without turning a number into text (no string promotion)
    if want is None:
        raise _refuse(_CALL_NAMES[type(node)], known)
    for value, have in zip(_values(node), known, strict=True):
        if _needs(have, want):
            value.replace(_converted(value.copy(), have, want))
    node.meta[_KEY] = want


def _columns(query: exp.Expr, copies: dict[int, exp.Expr]) -> list[Kind | None] | None:
    """The kinds of a set operation's side, column by column; None when they can't be told."""
    if isinstance(query, exp.Subquery):
        query = query.this
    if isinstance(query, _SETS):
        found = query.meta.get(_KEY)
        return cast(list[Kind | None], found) if found is not None else None
    if not isinstance(query, exp.Select) or any(p.is_star for p in query.expressions):
        return None
    return [_kind(p.this if isinstance(p, exp.Alias) else p, copies) for p in query.expressions]


def _leaves(query: exp.Expr) -> list[exp.Select]:
    if isinstance(query, exp.Subquery):
        query = query.this
    if isinstance(query, _SETS):
        return _leaves(query.left) + _leaves(query.right)
    return [query] if isinstance(query, exp.Select) else []


def _set_types(node: exp.SetOperation, copies: dict[int, exp.Expr], nested: bool = False) -> None:
    """Spark's widened type for each column of a UNION, INTERSECT or EXCEPT (WidenSetOperationTypes): each
    side's column is cast to it, as Spark casts it, before the two sides meet. Innermost first (``sets`` is
    in walk order, outer first, so the inner ones are typed by the time ``_columns`` reads them).
    ``nested``: ``node`` is a side of another set operation, whose casts go into each SELECT of it as if
    every one had ``node``'s widened type. So a value DuckDB would widen by itself (an int among doubles, a
    date among timestamps) is cast here too: 1 among doubles is 1.0 before a later cast writes it as text."""
    for inner in [n for n in (node.left, node.right) if isinstance(n, _SETS)]:
        if inner.meta.get(_KEY) is None:
            _set_types(inner, copies, nested=True)
    left, right = _columns(node.left, copies), _columns(node.right, copies)
    if left is None or right is None or len(left) != len(right):
        return
    widened: list[Kind | None] = []
    word = _SET_NAMES[type(node)]
    for i, (a, b) in enumerate(zip(left, right, strict=True)):
        if a is None or b is None:
            widened.append(None)
            continue
        want = wider(a, b)
        if want is None:
            raise _refuse(f"{word}'s column {i + 1}", [a, b])
        widened.append(want)
        for side, have in ((node.left, a), (node.right, b)):
            if _needs(have, want) or (nested and have != want and have[0] != "null"
                                      and want[0] in ("int", "double", "float", "timestamp")):
                for select in _leaves(side):
                    _cast_column(select, i, have, want)
    node.meta[_KEY] = widened


def _cast_column(select: exp.Select, i: int, have: Kind, want: Kind) -> None:
    projection = select.expressions[i]
    if isinstance(projection, exp.Alias):
        projection.set("this", _converted(projection.this, have, want))
    elif isinstance(projection, exp.Column):
        projection.replace(exp.alias_(_converted(projection.copy(), have, want), projection.name, quoted=True))
    else:
        # The first SELECT names the columns: an unnamed one keeps the name DuckDB gives it as written.
        from .sql import duckdb_sql
        projection.replace(exp.alias_(_converted(projection.copy(), have, want), duckdb_sql(projection.copy()),
                                      quoted=True))
