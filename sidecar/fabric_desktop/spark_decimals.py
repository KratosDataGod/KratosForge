"""Spark's decimal result types for Spark SQL and the DataFrame API on this PC (EA-004, parts 2 and 3).

DuckDB types decimal arithmetic by its own rules: ``decimal(10,2) / decimal(10,2)`` and ``avg`` of a
decimal give DOUBLE, ``decimal(10,2) * decimal(10,2)`` is DECIMAL(18,4) and can overflow where Spark
answers, and ``sum`` is DECIMAL(38,s). A table written from such a query has a different schema from
the one Fabric writes. Spark's DecimalPrecision rules (spark.sql.decimalOperations.allowPrecisionLoss,
on by default) are applied here instead, once the statement's tables are bound to DuckDB views, so the
operands' types are known:

- ``+`` and ``-``: scale max(s1, s2), precision max(p1 - s1, p2 - s2) + scale + 1;
- ``*``: precision p1 + p2 + 1, scale s1 + s2;
- ``/``: scale max(6, s1 + p2 + 1), precision p1 - s1 + s2 + scale;
- a precision over 38 keeps the whole digits and drops scale down to 6 (DecimalType.adjustPrecisionScale);
- ``sum``: decimal(min(38, p + 10), s), NULL when it overflows (ANSI off);
- ``avg``: decimal(min(38, p + 4), min(38, s + 4)).

An integer operand counts as decimal(3,0), (5,0), (10,0) or (20,0) for TINYINT to BIGINT, and an integer
literal as decimal(digits, 0); a number literal with a point is a decimal, as in Spark. Division and avg
are exact, rounded half up as Spark rounds, whenever the scaled dividend fits in 38 digits; a larger
one is computed through DOUBLE and cast to Spark's type. ``+``, ``-`` and ``*`` are exact when Spark's
precision fits in 38 digits; otherwise they keep DuckDB's own type. An operand of any other type, or one
whose type can't be worked out, leaves the expression as it was.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlglot import exp
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

log = logging.getLogger(__name__)

MAX = 38
_INTEGERS = {exp.DataType.Type.TINYINT: 3, exp.DataType.Type.SMALLINT: 5, exp.DataType.Type.INT: 10,
             exp.DataType.Type.BIGINT: 20}
_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div)
_AGGREGATES = (exp.Sum, exp.Avg)
_KEY = "kf_decimal"
# The rewrite's lambda parameters. DuckDB takes a column of the same name before a lambda parameter, so a
# statement that can see a column of either name is left as it was.
_LAMBDAS = ("kf_decv", "kf_decn")


def _adjust(precision: int, scale: int) -> tuple[int, int]:
    if precision <= MAX:
        return precision, scale
    whole = precision - scale
    return MAX, max(MAX - whole, min(scale, 6))


def _literal_type(node: exp.Literal) -> tuple[int, int] | None:
    if node.is_string:
        return None
    text = node.this.lstrip("+-")
    if "e" in text.lower():
        return None  # 1e3 is a DOUBLE in Spark
    whole, _, fraction = text.partition(".")
    digits = len((whole + fraction).lstrip("0")) or 1
    return max(digits, len(fraction)), len(fraction)


def _decimal(node: exp.Expr, typed: dict[int, tuple[int, int] | tuple[str, int]], annotated: dict[int, exp.Expr]
             ) -> tuple[int, int] | tuple[str, int] | None:
    """Spark's (precision, scale) for ``node`` as a decimal operand, "int" for a whole-number column,
    or None when it is anything else."""
    while isinstance(node, exp.Paren):
        node = node.this
    if id(node) in typed:
        return typed[id(node)]
    if isinstance(node, exp.Literal):
        found = _literal_type(node)
        if found is None:
            return None
        return found if found[1] else ("int", found[0])
    if isinstance(node, exp.Neg):
        return _decimal(node.this, typed, annotated)
    copy = annotated.get(id(node))
    kind = copy.type if copy is not None else None
    if not isinstance(kind, exp.DataType):
        return None
    if kind.this in _INTEGERS:
        return ("int", _INTEGERS[kind.this])
    if kind.this == exp.DataType.Type.DECIMAL:
        params = [str(p.this.this) for p in kind.expressions if isinstance(p.this, exp.Literal)]
        if len(params) == 2 == len(kind.expressions) and all(p.isdigit() for p in params):
            return int(params[0]), int(params[1])
    return None


def _operand(found: tuple[int, int] | tuple[str, int]) -> tuple[int, int]:
    """An integer operand as Spark widens it to a decimal: a literal by its digits, a column by its type."""
    if found[0] == "int":
        return int(found[1]), 0
    return int(found[0]), int(found[1])


def _with_args(template: str, args: list[exp.Expr]) -> exp.Expr:
    from .sql import _with_args as put
    return put(template, args)  # type: ignore[arg-type]  # sql's helper is typed for Expression, the same nodes


def _unscaled(value: str, scale: int) -> str:
    """``value`` (a decimal) as the HUGEINT of its digits at ``scale``: exact for any 38 digits."""
    return f"CAST(REPLACE(CAST(CAST({value} AS DECIMAL(38, {scale})) AS VARCHAR), '.', '') AS HUGEINT)"


def _divide(dividend: str, divisor: str, s1: int, s2: int, precision: int, scale: int) -> str | None:
    """DuckDB SQL for Spark's decimal division of ``dividend`` (scale s1) by ``divisor`` (scale s2),
    as decimal(precision, scale), rounded half up; NULL for a zero divisor (ANSI off)."""
    shift = scale + s2 - s1
    if shift < 0 or shift > MAX or scale >= MAX:
        return None
    power = "1" + "0" * shift
    unit = "0." + "0" * (scale - 1) + "1" if scale else "1"
    exact = (f"CAST(CAST(SIGN(kf_decn.n) * SIGN(kf_decn.d) * ((ABS(kf_decn.n) * {power}::HUGEINT) // ABS(kf_decn.d) + "
             f"CASE WHEN (ABS(kf_decn.n) * {power}::HUGEINT) % ABS(kf_decn.d) >= "
             f"ABS(kf_decn.d) - (ABS(kf_decn.n) * {power}::HUGEINT) % ABS(kf_decn.d) THEN 1 ELSE 0 END) AS DECIMAL(38, 0)) "
             f"* CAST('{unit}' AS DECIMAL({scale + 1}, {scale})) AS DECIMAL({precision}, {scale}))")
    limit = "1" + "0" * (MAX - shift)
    rough = f"CAST(CAST(kf_decn.a AS DOUBLE) / CAST(kf_decn.b AS DOUBLE) AS DECIMAL({precision}, {scale}))"
    return (f"(LIST_TRANSFORM([{{'a': {dividend}, 'b': {divisor}}}], kf_decv -> "
            f"(LIST_TRANSFORM([{{'a': kf_decv.a, 'b': kf_decv.b, 'n': {_unscaled('kf_decv.a', s1)}, "
            f"'d': {_unscaled('kf_decv.b', s2)}}}], kf_decn -> CASE WHEN kf_decn.n IS NULL OR kf_decn.d IS NULL OR kf_decn.d = 0 "
            f"THEN NULL WHEN ABS(kf_decn.n) < {limit}::HUGEINT THEN {exact} ELSE {rough} END))[1]))[1]")


def _wrapper(node: exp.Expr) -> exp.Expr:
    """The aggregate with the FILTER and OVER clauses around it, which a rewrite keeps."""
    top = node
    while isinstance(top.parent, (exp.Filter, exp.Window)) and top.parent.this is top:
        top = top.parent
    return top


def _rewrap(top: exp.Expr, inner: exp.Expr, replacement: exp.Expr) -> exp.Expr:
    copy = top.copy()
    if top is inner:
        return replacement
    target = copy
    while target.this is not None and not isinstance(target.this, type(inner)):
        target = target.this
    target.set("this", replacement)
    return copy


def _schema(conn: Any) -> MappingSchema:
    """The connection's tables and views by name; a temp view wins over a table of the same name."""
    tables: dict[str, dict[str, str]] = {}
    rows = conn.execute("SELECT table_name, column_name, data_type FROM duckdb_columns() "
                        "WHERE NOT internal ORDER BY database_name = 'temp' DESC, column_index").fetchall()
    for table, column, kind in rows:
        tables.setdefault(table, {}).setdefault(column, kind)
    return MappingSchema(cast(dict[str, object], tables), dialect="duckdb", normalize=False)


def spark_decimal_types(statement: exp.Expr, conn: Any) -> exp.Expr:
    """``statement`` with Spark's decimal result types (see the module docstring). Its tables must be
    bound to the connection's views already. A statement whose types can't be worked out is returned
    as it was."""
    targets = [n for n in statement.find_all(*_ARITHMETIC, *_AGGREGATES)]
    if not targets:
        return statement
    try:
        schema = _schema(conn)
        if _sees_lambda_name(statement, schema):
            log.info("Spark decimal types not applied: a column is named like the rewrite's lambdas %s", _LAMBDAS)
            return statement
        names = _output_names(statement, conn, targets)
        if names is None:
            return statement
        root = _retype(statement, targets, schema)
    except Exception as exc:
        # Only a type fix: a statement sqlglot can't type runs with DuckDB's types, as before, and says so.
        log.info("Spark decimal types not applied: %s: %s", type(exc).__name__, exc)
        return _unmark(statement)
    # An unnamed column keeps the name it had before the rewrite, not the rewritten SQL.
    for select, index, name in names:
        projection = select.expressions[index]
        select.expressions[index].replace(exp.alias_(projection, name, quoted=True))
    return root


_FRAME = "kf_frame"


def spark_decimal_columns(expressions: list[str], columns: list[tuple[str, str]]
                          ) -> list[tuple[str, str | None] | None]:
    """The DataFrame API's side of EA-004 (#1682): for each DuckDB SQL expression over a frame whose
    columns are ``columns`` (name, DuckDB type), the same expression with Spark's decimal result types
    as (DuckDB SQL, its ``AS`` name or None), or None when that changes nothing. Expressions whose
    types can't be worked out are None too, and keep DuckDB's types (logged)."""
    unchanged: list[tuple[str, str | None] | None] = [None] * len(expressions)
    if not expressions or not any(t.upper().startswith("DECIMAL") for _, t in columns):
        return unchanged  # Spark's rules only change an expression with a decimal operand
    from sqlglot import parse_one
    try:
        statement = parse_one(f"SELECT {', '.join(expressions)} FROM {_FRAME}", read="duckdb")
        if not isinstance(statement, exp.Select) or len(statement.expressions) != len(expressions):
            return unchanged
        before = [p.sql(dialect="duckdb") for p in statement.expressions]
        targets = [n for n in statement.find_all(*_ARITHMETIC, *_AGGREGATES)]
        if not targets:
            return unchanged
        frame: dict[str, str] = {}
        for name, kind in columns:
            frame.setdefault(name, kind)
        schema = MappingSchema(cast(dict[str, object], {_FRAME: frame}), dialect="duckdb", normalize=False)
        if _sees_lambda_name(statement, schema):
            log.info("Spark decimal types not applied: a column is named like the rewrite's lambdas %s", _LAMBDAS)
            return unchanged
        root = _retype(statement, targets, schema)
    except Exception as exc:
        log.info("Spark decimal types not applied: %s: %s", type(exc).__name__, exc)
        return unchanged
    if not isinstance(root, exp.Select):
        return unchanged
    from .sql import duckdb_sql
    out = unchanged.copy()
    for i, (projection, was) in enumerate(zip(root.expressions, before, strict=True)):
        if projection.sql(dialect="duckdb") != was:
            named = isinstance(projection, exp.Alias)
            out[i] = (duckdb_sql(projection.this if named else projection), projection.alias if named else None)
    return out


def _retype(statement: exp.Expr, targets: list[Any], schema: MappingSchema) -> exp.Expr:
    """``statement`` with each of ``targets`` (its arithmetic and sums) given Spark's decimal type, typed
    against ``schema``. Raises when sqlglot can't qualify or type it."""
    marked = 0
    for node in targets:
        for operand in (node.this, node.args.get("expression")):
            while isinstance(operand, (exp.Paren, exp.Neg, exp.Distinct)):
                operand = operand.this if not isinstance(operand, exp.Distinct) else (
                    operand.expressions[0] if len(operand.expressions) == 1 else None)
            if isinstance(operand, exp.Expr):
                operand.meta[_KEY] = marked
                marked += 1
    try:
        copy = qualify(statement.copy(), schema=schema, dialect="spark", validate_qualify_columns=False,
                       quote_identifiers=False, identify=False)
        annotate_types(copy, schema=schema, dialect="spark")
    except Exception:
        _unmark(statement)
        raise
    by_mark = {n.meta[_KEY]: n for n in copy.walk() if _KEY in n.meta}
    annotated: dict[int, exp.Expr] = {}
    for original in statement.walk():
        mark = original.meta.pop(_KEY, None)
        if mark is not None and mark in by_mark:
            annotated[id(original)] = by_mark[mark]
    typed: dict[int, tuple[int, int] | tuple[str, int]] = {}
    root = statement
    # Innermost first, so an operand that is itself rewritten has its Spark type.
    for node in reversed(targets):
        replacement = _rewrite(node, typed, annotated)
        if replacement is None:
            continue
        top = replacement[1]
        new = exp.paren(replacement[0])
        typed[id(new)] = replacement[2]
        typed[id(new.this)] = replacement[2]
        if top is root:
            root = new
        else:
            top.replace(new)
    return root


def _unmark(statement: exp.Expr) -> exp.Expr:
    for node in statement.walk():
        node.meta.pop(_KEY, None)
    return statement


def _sees_lambda_name(statement: exp.Expr, schema: MappingSchema) -> bool:
    names = {i.name.lower() for i in statement.find_all(exp.Identifier)}
    for columns in schema.mapping.values():
        names.update(str(c).lower() for c in cast(dict[str, str], columns))
    return any(n in names for n in _LAMBDAS)


def _output_names(statement: exp.Expr, conn: Any, targets: list[Any]
                  ) -> list[tuple[exp.Select, int, str]] | None:
    """For each unnamed output column that the rewrite would change, the name DuckDB gives it as it
    stands; None when those names can't be read (the statement is then left as it was)."""
    query = statement.expression if isinstance(statement, (exp.Create, exp.Insert)) else statement
    select = query
    while isinstance(select, exp.SetOperation):
        select = select.this  # a UNION's columns are named by its first SELECT
    if not isinstance(select, exp.Select) or not isinstance(query, exp.Query):
        return []
    ids = {id(t) for t in targets}
    changed = [i for i, p in enumerate(select.expressions)
               if not isinstance(p, exp.Alias) and any(id(n) in ids for n in p.walk())]
    if not changed:
        return []
    from .sql import duckdb_sql
    try:
        columns = conn.sql(duckdb_sql(query.copy())).columns  # binds the query; runs nothing
    except Exception as exc:
        log.info("Spark decimal types not applied: column names unknown: %s: %s", type(exc).__name__, exc)
        return None
    # A star stands for any number of columns: count from the start before the first one and from
    # the end after the last one.
    stars = [i for i, p in enumerate(select.expressions) if p.is_star]
    named = []
    for i in changed:
        if not stars or i < stars[0]:
            named.append((select, i, columns[i]))
        elif i > stars[-1]:
            named.append((select, i, columns[len(columns) - (len(select.expressions) - i)]))
        else:
            return None
    return named


def _rewrite(node: exp.Expr, typed: dict[int, tuple[int, int] | tuple[str, int]], annotated: dict[int, exp.Expr]
             ) -> tuple[exp.Expr, exp.Expr, tuple[int, int]] | None:
    if isinstance(node, _AGGREGATES):
        argument = node.this
        bare = argument.expressions[0] if isinstance(argument, exp.Distinct) and len(argument.expressions) == 1 \
            else argument
        found = _decimal(bare, typed, annotated)
        if found is None or found[0] == "int":
            return None
        p, s = _operand(found)
        top = _wrapper(node)
        total = (min(MAX, p + 10), s)
        if isinstance(node, exp.Sum):
            text = f"TRY_CAST(fj_arg0 AS DECIMAL({total[0]}, {total[1]}))"
            return _with_args(text, [top]), top, total
        result = (min(MAX, p + 4), min(MAX, s + 4))
        summed = _rewrap(top, node, exp.Sum(this=argument.copy()))
        counted = _rewrap(top, node, exp.Count(this=argument.copy()))
        mean = _divide(f"TRY_CAST(fj_arg0 AS DECIMAL({total[0]}, {total[1]}))", "fj_arg1", s, 0, *result)
        if mean is None:
            return None
        return _with_args(mean, [summed, counted]), top, result
    left, right = node.this, node.expression
    a, b = _decimal(left, typed, annotated), _decimal(right, typed, annotated)
    if a is None or b is None or (a[0] == "int" and b[0] == "int"):
        return None
    (p1, s1), (p2, s2) = _operand(a), _operand(b)
    if isinstance(node, exp.Div):
        scale = max(6, s1 + p2 + 1)
        precision, scale = _adjust(p1 - s1 + s2 + scale, scale)
        quotient = _divide("fj_arg0", "fj_arg1", s1, s2, precision, scale)
        if quotient is None:
            return None
        return _with_args(quotient, [left, right]), node, (precision, scale)
    if isinstance(node, exp.Mul):
        precision, scale = p1 + p2 + 1, s1 + s2
    else:
        scale = max(s1, s2)
        precision = max(p1 - s1, p2 - s2) + scale + 1
    if precision > MAX:
        return None  # Spark rounds to a narrower scale here; DuckDB's own type and value are kept
    op = "*" if isinstance(node, exp.Mul) else "+" if isinstance(node, exp.Add) else "-"
    text = (f"CAST(CAST(fj_arg0 AS DECIMAL(38, {s1})) {op} CAST(fj_arg1 AS DECIMAL(38, {s2})) "
            f"AS DECIMAL({precision}, {scale}))")
    return _with_args(text, [left, right]), node, (precision, scale)


