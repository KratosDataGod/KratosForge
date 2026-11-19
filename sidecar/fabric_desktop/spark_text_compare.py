"""Spark's comparisons of text with a number, for Spark SQL on this PC (WR-254).

Spark (ANSI off, as Fabric runs) compares a string with a number by casting the string to the number's type:
``'1.5' = 1`` is ``CAST('1.5' AS INT) = 1``, true, and ``0 = ''`` is NULL, as is ``'1e2' = 100``. A string and a
decimal are both compared as doubles, so ``'1.55' = 1.6`` is false. ``IN`` with a string and numbers compares
them all as strings, so ``'1.0' IN (1, 2)`` is false. DuckDB casts the string its own way: '1.5' rounds to 2,
'1e2' is 100, and '' or 'abc' stops the query, so the same query gave a different answer or none.

The statement's tables must be bound to the connection's views already, as for spark_decimals. Only
comparisons whose two sides sqlglot can type are rewritten; any other statement is returned as it was.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlglot import exp
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from .spark_decimals import Refused

log = logging.getLogger(__name__)

_T = exp.DataType.Type
_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NullSafeEQ, exp.NullSafeNEQ)
_TEXT = {_T.VARCHAR, _T.TEXT, _T.CHAR, _T.NCHAR, _T.NVARCHAR}
_WHOLE = {_T.TINYINT: "TINYINT", _T.SMALLINT: "SMALLINT", _T.INT: "INTEGER", _T.BIGINT: "BIGINT"}
_FLOATS = {_T.DOUBLE: "DOUBLE", _T.FLOAT: "FLOAT"}
_KEY = "kf_text_compare"
# Spark's text to a whole number (UTF8String.toInt, ANSI off): spaces trimmed, a sign, digits and a fraction
# that is dropped; '.5' and '-.' are 0, '+' and '1e2' are NULL, and a number past the type is NULL.
_WHOLE_TEXT = r"^\s*[+-]?([0-9]+\.?[0-9]*|\.[0-9]*)\s*$"
# Java's Double.parseDouble, which Spark's cast uses, takes a d or f after the number.
_DOUBLE_TEXT = r"^\s*([+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?)[dDfF]?\s*$"


def whole_text(value: str, duck_type: str) -> str:
    """DuckDB SQL for the text ``value`` cast to a whole-number type as Spark casts it with ANSI off. ``value``
    is written twice: give a column, a literal or a lambda's name (_text_as binds anything else once)."""
    text = f"CAST({value} AS VARCHAR)"
    digits = f"regexp_replace(regexp_extract({text}, '^\\s*([+-]?[0-9]*)', 1), '^[+-]?$', '0')"
    return f"CASE WHEN regexp_matches({text}, '{_WHOLE_TEXT}') THEN TRY_CAST({digits} AS {duck_type}) END"


def double_text(value: str, duck_type: str = "DOUBLE") -> str:
    """DuckDB SQL for the text ``value`` cast to a double (or float) as Spark casts it: spaces trimmed, a d
    or f after the number taken, and NaN and infinity read in any case. ``value`` is written three times, as
    in whole_text."""
    text = f"CAST({value} AS VARCHAR)"
    return (f"CASE WHEN regexp_matches({text}, '{_DOUBLE_TEXT}') THEN TRY_CAST(regexp_extract({text}, "
            f"'{_DOUBLE_TEXT}', 1) AS {duck_type}) ELSE TRY_CAST(TRIM({text}) AS {duck_type}) END")


# The lambda parameter the text side is bound to, so it is written once however deep comparisons nest. A
# statement that can see a column of this name is left as it was (DuckDB takes the column first).
_LAMBDA = "kf_text_cmp"
_LAMBDA_X = "kf_text_cmpx"  # BETWEEN's value, when its two ends are of two types


def _bound(template: str, side: exp.Expr) -> exp.Expr:
    """``template`` (DuckDB SQL over ``_LAMBDA``) applied to ``side``, which is read once: a column or a
    literal goes in as it is, anything else through a lambda over a one-element list."""
    from .sql import _with_args
    if isinstance(side, (exp.Column, exp.Literal)):
        return exp.paren(_with_args(template.replace(_LAMBDA, "fj_arg0"), [cast(Any, side)]))
    return exp.paren(_with_args(f"(LIST_TRANSFORM([fj_arg0], {_LAMBDA} -> {template}))[1]", [cast(Any, side)]))


def _kind(node: exp.Expr, copies: dict[int, exp.Expr]) -> exp.DataType | None:
    copy = copies.get(id(node))
    kind = copy.type if copy is not None else None
    return kind if isinstance(kind, exp.DataType) and kind.this != _T.UNKNOWN else None


def _text_as(side: exp.Expr, kind: exp.DataType) -> exp.Expr | None:
    """``side`` (text) cast to the number type ``kind`` as Spark's comparison casts it, or None when
    ``kind`` isn't a number type. ``side`` appears once in the result."""
    if kind.this in _WHOLE:
        return _bound(whole_text(_LAMBDA, _WHOLE[kind.this]), side)
    if kind.this in _FLOATS:
        return _bound(double_text(_LAMBDA, _FLOATS[kind.this]), side)
    if kind.this == _T.DECIMAL:
        return _bound(double_text(_LAMBDA), side)
    return None


def _number_of(side: exp.Expr, kind: exp.DataType) -> exp.Expr:
    """The number side of a comparison with text: a decimal is compared as a double, as Spark does."""
    if kind.this == _T.DECIMAL:
        return exp.cast(side, exp.DataType.build("DOUBLE"))
    return side


def _as_text(side: exp.Expr, kind: exp.DataType) -> exp.Expr:
    """A number as Spark's CAST to STRING writes it (IN with text compares as text)."""
    from .sql import spark_text_cast
    if kind.this in _FLOATS:
        return _bound(spark_text_cast(_LAMBDA), side)
    return exp.cast(side, exp.DataType.build("VARCHAR"))


def _numeric(kind: exp.DataType | None) -> bool:
    return kind is not None and (kind.this in _WHOLE or kind.this in _FLOATS or kind.this == _T.DECIMAL)


def _text(kind: exp.DataType | None) -> bool:
    return kind is not None and kind.this in _TEXT


def _literal_kind(node: exp.Expr) -> exp.DataType | None:
    """Spark's type for a number literal: a whole one is an INT (a BIGINT past it), one with a point a decimal;
    sqlglot types 1.5 as a DOUBLE."""
    literal = node.this if isinstance(node, exp.Neg) else node
    if not isinstance(literal, exp.Literal) or literal.is_string:
        return None
    text = literal.this.lower()
    if "e" in text:
        return exp.DataType.build("DOUBLE")
    if "." in text:
        return exp.DataType.build("DECIMAL(38, 18)")
    return exp.DataType.build("INT" if abs(int(text)) < 2 ** 31 else "BIGINT")


def spark_text_comparisons(statement: exp.Expr, conn: Any, schema: MappingSchema | None = None) -> exp.Expr:
    """``statement`` with each comparison of text with a number written as Spark compares them, typed
    against the connection's tables (or ``schema``)."""
    found = [n for n in statement.walk() if isinstance(n, (*_COMPARISONS, exp.In, exp.Between))
             or (isinstance(n, exp.Case) and n.this is not None)]
    if not found:
        return statement
    sides: list[exp.Expr] = []
    for node in found:
        if isinstance(node, exp.In):
            if node.args.get("query") is not None or node.args.get("unnest") is not None:
                continue
            sides += [node.this, *node.expressions]
        elif isinstance(node, exp.Between):
            sides += [node.this, node.args["low"], node.args["high"]]
        elif isinstance(node, exp.Case):
            sides += [node.this, *(i.this for i in node.args.get("ifs") or [])]
        else:
            sides += [node.this, node.expression]
    if not any(isinstance(s, exp.Literal) and s.is_string for s in sides) and not _has_text_column(sides, conn, schema):
        return statement
    try:
        if schema is None:
            from .spark_decimals import _schema
            schema = _schema(conn)
        for mark, side in enumerate(sides):
            side.meta[_KEY] = mark
        copy = qualify(statement.copy(), schema=schema, dialect="spark", validate_qualify_columns=False,
                       quote_identifiers=False, identify=False)
        annotate_types(copy, schema=schema, dialect="spark")
        by_mark = {n.meta[_KEY]: n for n in copy.walk() if _KEY in n.meta}
        copies = {id(s): by_mark[i] for i, s in enumerate(sides) if i in by_mark}
    except Exception as exc:  # noqa: BLE001 - a fix of comparisons only: an untyped statement runs as it was; logged
        log.info("Spark text comparisons not applied: %s: %s", type(exc).__name__, exc)
        return statement
    finally:
        for side in sides:
            side.meta.pop(_KEY, None)

    def kind(side: exp.Expr) -> exp.DataType | None:
        return _literal_kind(side) or _kind(side, copies)

    from .sql import duckdb_sql
    if any(i.name.lower() == _LAMBDA for i in statement.find_all(exp.Identifier)) or any(
            _LAMBDA in cast(dict[str, str], columns) for columns in schema.mapping.values()):
        log.info("Spark text comparisons not applied: a column is named like the rewrite's lambda %s", _LAMBDA)
        return statement
    before = statement.copy()
    try:
        size = len(duckdb_sql(before))
        root, rewrites = statement, 0
        for node in reversed(found):
            new = _rewritten(node, kind)
            if new is None:
                continue
            rewrites += 1
            if node is root:
                root = new
            else:
                node.replace(new)
        grown = len(duckdb_sql(root)) if rewrites else size
    except Exception as exc:  # noqa: BLE001 - a fix of comparisons only: the statement runs as it was; logged
        log.info("Spark text comparisons not applied: %s: %s", type(exc).__name__, exc)
        return before
    # Each rewrite reads its sides once, so the SQL grows by a bounded amount per comparison; past that, a
    # statement is refused by name rather than run as DuckDB compares (a different answer) or sent huge.
    if grown > 2 * size + _GROWTH * rewrites:
        raise Refused(f"This Spark SQL can't be run on this PC: its {rewrites} comparisons of text with numbers "
                      "would be written out too large here. Cast the text to the number's type in the query.")
    return root


_GROWTH = 20_000  # characters a comparison's rewrite may add: the casts' fixed text (a double written as Java writes it is the longest), with its sides read once


def _has_text_column(sides: list[exp.Expr], conn: Any, schema: MappingSchema | None) -> bool:
    """Whether a column on either side of a comparison could be text: a cheap check before typing."""
    if not any(isinstance(s, exp.Column) for side in sides for s in side.walk()):
        return False
    if schema is not None:
        return any(str(t).upper().startswith(("VARCHAR", "CHAR", "TEXT", "STRING"))
                   for columns in schema.mapping.values() for t in cast(dict[str, str], columns).values())
    rows = conn.execute("SELECT 1 FROM duckdb_columns() WHERE NOT internal AND (data_type LIKE 'VARCHAR%' "
                        "OR data_type LIKE 'CHAR%') LIMIT 1").fetchall()
    return bool(rows)


def _pair(left: exp.Expr, right: exp.Expr, kind: Any) -> tuple[exp.Expr, exp.Expr] | None:
    a, b = kind(left), kind(right)
    if _text(a) and _numeric(b):
        cast_ = _text_as(left, b)
        return (cast_, _number_of(right.copy(), b)) if cast_ is not None else None
    if _numeric(a) and _text(b):
        cast_ = _text_as(right, a)
        return (_number_of(left.copy(), a), cast_) if cast_ is not None else None
    return None


def _rewritten(node: exp.Expr, kind: Any) -> exp.Expr | None:
    if isinstance(node, _COMPARISONS):
        pair = _pair(node.this, node.expression, kind)
        if pair is None:
            return None
        return type(node)(this=pair[0], expression=pair[1])
    if isinstance(node, exp.Between):
        this, low, high = node.this, node.args["low"], node.args["high"]
        a, b, c = kind(this), kind(low), kind(high)
        if _text(a) and _numeric(b) and _numeric(c):
            if b.this == c.this:
                cast_ = _text_as(this, b)
                if cast_ is None:
                    return None
                return exp.Between(this=cast_, low=_number_of(low.copy(), b), high=_number_of(high.copy(), c))
            # Spark compares each end on its own (x >= low AND x <= high), each cast to its end's type: x is read
            # once, through a lambda.
            first, second = _text_as(exp.column(_LAMBDA_X), b), _text_as(exp.column(_LAMBDA_X), c)
            if first is None or second is None:
                return None
            test = exp.and_(exp.GTE(this=first, expression=_number_of(low.copy(), b)),
                            exp.LTE(this=second, expression=_number_of(high.copy(), c)))
            from .sql import _with_args
            return exp.paren(_with_args(f"(LIST_TRANSFORM([fj_arg0], {_LAMBDA_X} -> fj_arg1))[1]",
                                        [cast(Any, this.copy()), cast(Any, test)]))
        if _numeric(a) and (_text(b) or _text(c)) and all(_numeric(k) or _text(k) for k in (b, c)):
            ends = []
            for end, k in ((low, b), (high, c)):
                ends.append(_text_as(end, a) if _text(k) else end.copy())
            if any(e is None for e in ends):
                return None
            return exp.Between(this=_number_of(this.copy(), a), low=ends[0], high=ends[1])
        return None
    if isinstance(node, exp.Case):
        # CASE x WHEN v THEN ... is Spark's CASE WHEN x = v THEN ...: each pair is cast as a comparison casts it.
        ifs = node.args.get("ifs") or []
        operand, values = kind(node.this), [kind(i.this) for i in ifs]
        default = node.args.get("default")
        if _text(operand) and any(_numeric(v) for v in values):
            numbers = {v.this for v in values if _numeric(v)}
            if len(numbers) != 1 or any(not (_numeric(v) or _text(v)) for v in values):
                return None  # numbers of two types: Spark casts x to each; left to DuckDB, as before
            to = next(v for v in values if _numeric(v))
            cast_ = _text_as(node.this, to)  # x once, as the CASE's operand
            if cast_ is None or any(_text(v) for v in values):
                return None
            whens = [exp.If(this=_number_of(i.this.copy(), v), true=i.args["true"].copy()) for i, v in zip(ifs, values, strict=True)]
        elif _numeric(operand) and any(_text(v) for v in values):
            whens = []
            for i, v in zip(ifs, values, strict=True):
                value = _text_as(i.this, operand) if _text(v) else i.this.copy()
                if value is None:
                    return None
                whens.append(exp.If(this=value, true=i.args["true"].copy()))
            cast_ = _number_of(node.this.copy(), operand)
        else:
            return None
        return exp.Case(this=cast_, ifs=whens, default=default.copy() if default is not None else None)
    if isinstance(node, exp.In) and node.args.get("query") is None and node.args.get("unnest") is None:
        values = [node.this, *node.expressions]
        kinds = [kind(v) for v in values]
        if not any(_text(k) for k in kinds) or not any(_numeric(k) for k in kinds) or any(
                k is None or not (_text(k) or _numeric(k)) for k in kinds):
            return None
        # Spark's IN finds the values' wider type, which for text and numbers is STRING.
        texts = [_as_text(v.copy(), k) if _numeric(k) else v.copy() for v, k in zip(values, kinds, strict=True)]
        return exp.In(this=texts[0], expressions=texts[1:])
    return None


_FRAME = "kf_frame"


def spark_text_comparison_columns(expressions: list[str], columns: list[tuple[str, str]]
                                  ) -> list[tuple[str, str | None] | None]:
    """The DataFrame API's side: each DuckDB SQL expression over a frame whose columns are ``columns``
    (name, DuckDB type), with its comparisons of text with a number as Spark makes them, or None when that
    changes nothing (or its types can't be worked out), as (DuckDB SQL, its ``AS`` name or None)."""
    unchanged: list[tuple[str, str | None] | None] = [None] * len(expressions)
    if not expressions or not any(k in e.upper() for e in expressions for k in ("=", "<", ">", " IN ", "BETWEEN", "CASE")):
        return unchanged
    from sqlglot import parse_one

    from .sql import duckdb_sql
    try:
        statement = parse_one(f"SELECT {', '.join(expressions)} FROM {_FRAME}", read="duckdb")
        if not isinstance(statement, exp.Select) or len(statement.expressions) != len(expressions):
            return unchanged
        before = [duckdb_sql(p) for p in statement.expressions]
        frame: dict[str, str] = {}
        for name, kind in columns:
            frame.setdefault(name.lower(), kind)
        schema = MappingSchema({_FRAME: frame}, dialect="duckdb", normalize=False)
        root = spark_text_comparisons(statement, None, schema)
        if not isinstance(root, exp.Select):
            return unchanged
        out = unchanged.copy()
        for i, (projection, was) in enumerate(zip(root.expressions, before, strict=True)):
            if duckdb_sql(projection) != was:
                named = isinstance(projection, exp.Alias)
                out[i] = (duckdb_sql(projection.this if named else projection), projection.alias if named else None)
        return out
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - a fix of comparisons only: the column runs as it was; logged
        log.info("Spark text comparisons not applied: %s: %s", type(exc).__name__, exc)
        return unchanged
