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
- ``avg``: decimal(min(38, p + 4), min(38, s + 4));
- ``%`` (and ``pmod``): scale max(s1, s2), precision min(p1 - s1, p2 - s2) + scale;
- ``div``: BIGINT, the quotient truncated toward zero, NULL for a zero divisor;
- ``round``, ``ceil`` and ``floor`` to ``d`` places (0 when not given): scale min(s, d) (0 for
  a negative ``d``), precision p - s + 1 + scale (at least 1 - d for a negative ``d``); ``ceil`` and
  ``floor`` of a decimal(p, 0) keep its type.

An integer operand counts as decimal(3,0), (5,0), (10,0) or (20,0) for TINYINT to BIGINT, and an integer
literal as decimal(digits, 0); a number literal with a point is a decimal, as in Spark. Division and avg
are exact, rounded half up as Spark rounds, whenever the scaled dividend fits in 38 digits; a larger
one is computed through DOUBLE and cast to Spark's type. ``+``, ``-`` and ``*`` are exact when Spark's
precision fits in 38 digits; otherwise they keep DuckDB's own type. An operand of any other type, or one
whose type can't be worked out, leaves the expression as it was. What still differs is listed in
docs/sql-compatibility.md (LOC-005).
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
_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.IntDiv)
_AGGREGATES = (exp.Sum, exp.Avg)
_ROUNDING = (exp.Round, exp.Ceil, exp.Floor)
# sql.parse writes pmod and bround out as a CASE (its template names the function in the node's meta);
# the CASE as a whole takes Spark's type for the function: Remainder's for pmod, RoundBase's for bround.
_WRITTEN = ("PMOD", "BROUND")


def _targets(statement: exp.Expr) -> list[Any]:
    """The nodes whose result type Spark's rules set, outermost first (as find_all walks)."""
    return [n for n in statement.walk() if isinstance(n, (*_ARITHMETIC, *_AGGREGATES, *_ROUNDING)) or _written(n)]


def _written(node: exp.Expr) -> str | None:
    """"PMOD" or "BROUND" for the CASE sql.parse wrote that function out as, else None."""
    from .sql import SPARK_FUNCTION
    name = node.meta.get(SPARK_FUNCTION) if isinstance(node, exp.Case) else None
    return name if name in _WRITTEN else None


def _zero_guard(node: Any) -> bool:
    """Whether ``node`` is Databricks SQL's check that a division's divisor isn't 0 (sql._ansi_divisions),
    a CASE typed as the division it holds."""
    return isinstance(node, exp.Case) and bool(node.meta.get(ZERO_GUARD))


ZERO_GUARD = "kf_zero_guard"


def _operands(node: exp.Expr) -> list[Any]:
    if _written(node):
        last = node.args.get("default")  # ELSE (a) % (b), or ELSE ROUND(x, places)
        if isinstance(last, exp.Mod):
            return [last.this, last.expression]
        if isinstance(last, exp.Round):
            return [last.this, last.args.get("decimals")]
        return []
    if isinstance(node, _ROUNDING):
        return [node.this]
    return [node.this, node.args.get("expression")]


_KEY = "kf_decimal"
# The rewrite's lambda parameters. DuckDB takes a column of the same name before a lambda parameter, so a
# statement that can see a column of either name is left as it was.
_LAMBDAS = ("kf_decv", "kf_decn", "kf_decsum")


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
    from .spark_complex import TYPED

    given = (
        node.type if node.meta.get(TYPED) else None
    )  # a rewrite that keeps its type, as a Spark cast's
    if isinstance(given, exp.DataType) and given.this in _INTEGERS:
        return ("int", _INTEGERS[given.this])
    while isinstance(node, exp.Paren) or _zero_guard(node):
        node = node.this if isinstance(node, exp.Paren) else node.args["default"]
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


# Spark's error for a decimal sum (or an average's) past its type with ANSI mode on, which Databricks SQL runs
# with (Sum's CheckOverflowInSum); with ANSI off, as Fabric runs Spark, it is NULL.
SUM_OVERFLOW = ("[ARITHMETIC_OVERFLOW] Overflow in sum of decimals. If necessary set ansi_mode to false to bypass "
                "this error.")


def _summed(value: str, total: tuple[int, int], ansi: bool) -> str:
    """``value`` (a sum, at the decimal's scale) as decimal ``total``: NULL when its whole part has more
    digits than the type holds, or with ``ansi`` an error. DuckDB's own sum can hold such a value in a
    DECIMAL(38) without complaint, so the digits are counted."""
    whole = total[0] - total[1]
    over = "NULL" if not ansi else f"ERROR({exp.Literal.string(SUM_OVERFLOW).sql('duckdb')})"
    return (f"(LIST_TRANSFORM([{value}], kf_decsum -> CASE WHEN kf_decsum IS NULL THEN NULL "
            f"WHEN length(regexp_extract(CAST(kf_decsum AS VARCHAR), '^-?([0-9]*)', 1)) <= {whole} "
            f"THEN TRY_CAST(kf_decsum AS DECIMAL({total[0]}, {total[1]})) ELSE {over} END))[1]")


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
    """The connection's tables and views by name; a temp view wins over a table of the same name. Names
    are kept in lower case, as qualify reads Spark's names (case-insensitive) in lower case (#2663)."""
    tables: dict[str, dict[str, str]] = {}
    rows = conn.execute("SELECT table_name, column_name, data_type FROM duckdb_columns() "
                        "WHERE NOT internal ORDER BY database_name = 'temp' DESC, column_index").fetchall()
    for table, column, kind in rows:
        tables.setdefault(table.lower(), {}).setdefault(column.lower(), kind)
    return MappingSchema(cast(dict[str, object], tables), dialect="duckdb", normalize=False)


def spark_decimal_types(statement: exp.Expr, conn: Any, ansi: bool = False) -> exp.Expr:
    """``statement`` with Spark's decimal result types (see the module docstring). Its tables must be
    bound to the connection's views already. A statement whose types can't be worked out is returned
    as it was."""
    targets = _targets(statement)
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
        root = _retype(statement, targets, schema, ansi)
    except Exception as exc:  # noqa: BLE001 - a type fix only: any failure runs the statement unchanged; logged
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

    from .sql import duckdb_sql
    try:
        statement = parse_one(f"SELECT {', '.join(expressions)} FROM {_FRAME}", read="duckdb")
        if not isinstance(statement, exp.Select) or len(statement.expressions) != len(expressions):
            return unchanged
        before = [duckdb_sql(p) for p in statement.expressions]
        targets = _targets(statement)
        if not targets:
            return unchanged
        frame: dict[str, str] = {}
        for name, kind in columns:
            frame.setdefault(name.lower(), kind)  # qualify reads Spark's names in lower case (#2663)
        schema = MappingSchema(cast(dict[str, object], {_FRAME: frame}), dialect="duckdb", normalize=False)
        if _sees_lambda_name(statement, schema):
            log.info("Spark decimal types not applied: a column is named like the rewrite's lambdas %s", _LAMBDAS)
            return unchanged
        root = _retype(statement, targets, schema)
        if not isinstance(root, exp.Select):
            return unchanged
        out = unchanged.copy()
        for i, (projection, was) in enumerate(zip(root.expressions, before, strict=True)):
            if duckdb_sql(projection) != was:
                named = isinstance(projection, exp.Alias)
                out[i] = (duckdb_sql(projection.this if named else projection), projection.alias if named else None)
    except Exception as exc:  # noqa: BLE001 - a type fix only: any failure runs the statement unchanged; logged
        log.info("Spark decimal types not applied: %s: %s", type(exc).__name__, exc)
        return unchanged
    return out


def _retype(statement: exp.Expr, targets: list[Any], schema: MappingSchema, ansi: bool = False) -> exp.Expr:
    """``statement`` with each of ``targets`` (its arithmetic and sums) given Spark's decimal type, typed
    against ``schema``. Raises when sqlglot can't qualify or type it."""
    marked = 0
    for node in targets:
        for operand in _operands(node):
            while isinstance(operand, (exp.Paren, exp.Neg, exp.Distinct)) or _zero_guard(operand):
                if _zero_guard(operand):
                    operand = operand.args["default"]
                    continue
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
    # A pmod's or bround's operands as they are now: they sit inside nodes rewritten before it.
    written = {id(node): _operands(node) for node in targets if _written(node)}
    root = statement
    # Innermost first, so an operand that is itself rewritten has its Spark type.
    for node in reversed(targets):
        replacement = _rewrite(node, typed, annotated, written.get(id(node)) or _operands(node), ansi)
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
    except Exception as exc:  # noqa: BLE001 - a type fix only: unknown column names leave types as they are; logged
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


def _rewrite(node: exp.Expr, typed: dict[int, tuple[int, int] | tuple[str, int]], annotated: dict[int, exp.Expr],
             operands: list[Any], ansi: bool = False) -> tuple[exp.Expr, exp.Expr, tuple[int, int] | tuple[str, int]] | None:
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
            return _with_args(_summed("fj_arg0", total, ansi), [top]), top, total
        result = (min(MAX, p + 4), min(MAX, s + 4))
        summed = _rewrap(top, node, exp.Sum(this=argument.copy()))
        counted = _rewrap(top, node, exp.Count(this=argument.copy()))
        mean = _divide(_summed("fj_arg0", total, ansi), "fj_arg1", s, 0, *result)
        if mean is None:
            return None
        return _with_args(mean, [summed, counted]), top, result
    if isinstance(node, _ROUNDING) or _written(node) == "BROUND":
        return _rounding(node, typed, annotated, operands)
    if len(operands) != 2 or operands[0] is None or operands[1] is None:
        return None
    left, right = operands
    a, b = _decimal(left, typed, annotated), _decimal(right, typed, annotated)
    if a is None or b is None:
        return None
    if isinstance(node, exp.IntDiv):
        return _integral_divide(node, left, right, a, b)
    if a[0] == "int" and b[0] == "int":
        return None if ansi else _whole_wrapped(node, left, right, a, b)
    (p1, s1), (p2, s2) = _operand(a), _operand(b)
    if isinstance(node, (exp.Mod, exp.Case)):  # % and pmod: Spark's Remainder rule
        scale = max(s1, s2)
        precision, scale = _adjust(min(p1 - s1, p2 - s2) + scale, scale)
        if max(p1 - s1, p2 - s2) + scale > MAX:
            return None  # an operand doesn't fit DECIMAL(38, scale): DuckDB's own type and value are kept
        if isinstance(node, exp.Mod):
            # NULL for a zero divisor, as Spark with ANSI off (DuckDB 2.0 raises an error).
            text = (f"CAST(CAST(fj_arg0 AS DECIMAL(38, {scale})) % NULLIF(CAST(fj_arg1 AS DECIMAL(38, {scale})), 0) "
                    f"AS DECIMAL({precision}, {scale}))")
            return _with_args(text, [left, right]), node, (precision, scale)
        return _with_args(f"CAST(fj_arg0 AS DECIMAL({precision}, {scale}))", [node]), node, (precision, scale)
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
        # Spark rounds to a narrower scale here (DecimalType.adjustPrecisionScale), and the exact result
        # needs more than DuckDB's 38 digits: DuckDB's own type and value are kept (LOC-005).
        return None
    op = "*" if isinstance(node, exp.Mul) else "+" if isinstance(node, exp.Add) else "-"
    text = (f"CAST(CAST(fj_arg0 AS DECIMAL(38, {s1})) {op} CAST(fj_arg1 AS DECIMAL(38, {s2})) "
            f"AS DECIMAL({precision}, {scale}))")
    return _with_args(text, [left, right]), node, (precision, scale)


# Spark's whole-number types by the precision _decimal gives them, with their width in bits.
_WHOLE = {
    3: ("TINYINT", 8),
    5: ("SMALLINT", 16),
    10: ("INTEGER", 32),
    20: ("BIGINT", 64),
}


def _whole_rank(node: exp.Expr, found: tuple[int, int] | tuple[str, int]) -> int | None:
    """The precision of ``node``'s Spark type (a key of _WHOLE): a column's by its type, a whole-number
    literal INT when it fits in 32 bits and BIGINT when it fits in 64 (a larger one is a decimal)."""
    while isinstance(node, exp.Paren):
        node = node.this
    sign = 1
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        node, sign = node.this, -1
    if isinstance(node, exp.Literal):
        try:
            value = sign * int(node.this)
        except ValueError:
            return None
        return (
            10
            if -(2**31) <= value < 2**31
            else 20
            if -(2**63) <= value < 2**63
            else None
        )
    return int(found[1]) if int(found[1]) in _WHOLE else None


def _wraps_here(node: exp.Expr) -> bool:
    """Whether ``node`` wraps around: not inside a TRY (try_add and try_multiply give NULL on an overflow), nor
    an array index (get and element_at write their own index as a sum), nor a filter (WHERE, HAVING, QUALIFY or a
    join's ON), where DuckDB can only skip row groups by a column's min and max when ``id + 1 = 5`` stays plain."""
    child, parent = node, node.parent
    while parent is not None:
        if isinstance(parent, exp.Try) or (
            isinstance(parent, exp.Bracket) and child is not parent.this
        ):
            return False
        if isinstance(parent, (exp.Where, exp.Having, exp.Qualify)) or (
            isinstance(parent, exp.Join) and child is parent.args.get("on")
        ):
            return False
        if isinstance(parent, exp.Select):  # a select list, or a subquery's own, wraps
            return True
        child, parent = parent, parent.parent
    return True


def _whole_wrapped(
    node: exp.Expr,
    left: exp.Expr,
    right: exp.Expr,
    a: tuple[int, int] | tuple[str, int],
    b: tuple[int, int] | tuple[str, int],
) -> tuple[exp.Expr, exp.Expr, tuple[str, int]] | None:
    """``+``, ``-`` and ``*`` of two whole numbers as Java computes them, with ANSI mode off: in the wider of
    the two types, wrapping around past its range (2147483647 + 1 is -2147483648), where DuckDB stops."""
    if not isinstance(node, (exp.Add, exp.Sub, exp.Mul)) or not _wraps_here(node):
        return None
    ranks = (_whole_rank(left, a), _whole_rank(right, b))
    if None in ranks:
        return None
    rank = max(r for r in ranks if r is not None)
    duck_type, bits = _WHOLE[rank]
    op = "*" if isinstance(node, exp.Mul) else "+" if isinstance(node, exp.Add) else "-"
    # In a type twice as wide (a product of two ints fits a BIGINT), the low bits kept with & as Java keeps them.
    wide = "BIGINT" if bits <= 32 else "HUGEINT"
    half, mask = 2 ** (bits - 1), 2**bits - 1
    text = (
        f"CAST((((CAST(fj_arg0 AS {wide}) {op} CAST(fj_arg1 AS {wide})) + {half}) & {mask}) - {half} "
        f"AS {duck_type})"
    )
    return _with_args(text, [left, right]), node, ("int", rank)


def _integral_divide(node: exp.Expr, left: exp.Expr, right: exp.Expr, a: tuple[int, int] | tuple[str, int],
                     b: tuple[int, int] | tuple[str, int]) -> tuple[exp.Expr, exp.Expr, tuple[str, int]]:
    """Spark's ``div``: a BIGINT, the quotient truncated toward zero, NULL for a zero divisor (ANSI off).
    DuckDB's ``//`` of a decimal is a DOUBLE division (10.00 // 3 is 3.33...), and of integers an INTEGER."""
    if a[0] == "int" and b[0] == "int":
        return _with_args("CAST(fj_arg0 // NULLIF(fj_arg1, 0) AS BIGINT)", [left, right]), node, ("int", 20)
    (p1, s1), (p2, s2) = _operand(a), _operand(b)
    scale = max(s1, s2)
    if max(p1 - s1, p2 - s2) + scale <= MAX:
        # Both as whole numbers at one scale: their quotient is exact in HUGEINT, which truncates toward zero.
        text = (f"TRY_CAST({_unscaled('fj_arg0', scale)} // NULLIF({_unscaled('fj_arg1', scale)}, 0) "
                "AS BIGINT)")
    else:
        text = "TRY_CAST(TRUNC(CAST(fj_arg0 AS DOUBLE) / NULLIF(CAST(fj_arg1 AS DOUBLE), 0)) AS BIGINT)"
    return _with_args(text, [left, right]), node, ("int", 20)


def _places(arg: exp.Expr | None) -> int | None:
    """The literal number of places a round, bround, ceil or floor rounds to: 0 when not given."""
    if arg is None:
        return 0
    negative = isinstance(arg, exp.Neg)
    literal = arg.this if negative else arg
    if not isinstance(literal, exp.Literal) or literal.is_string or not literal.this.isdigit():
        return None
    return -int(literal.this) if negative else int(literal.this)


def _rounding(node: exp.Expr, typed: dict[int, tuple[int, int] | tuple[str, int]], annotated: dict[int, exp.Expr],
              operands: list[Any]) -> tuple[exp.Expr, exp.Expr, tuple[int, int]] | None:
    """round, bround, ceil and floor of a decimal, typed by Spark's RoundBase (and Ceil, Floor) rules."""
    if not operands or operands[0] is None:
        return None
    operand = operands[0]
    found = _decimal(operand, typed, annotated)
    if (isinstance(node, (exp.Ceil, exp.Floor)) and node.args.get("decimals") is None and found is None
            and _is_double(operand, annotated)):  # sqlglot types 0.1 as a DOUBLE: _decimal reads it as Spark's decimal
        # Spark's ceil and floor of a double are a BIGINT (math.ceil(d).toLong: NaN is 0, past the range the
        # nearest end); DuckDB's are a DOUBLE, so CAST(ceil(1.0E3D) AS STRING) was '1000.0' and div divided.
        fn = "CEIL" if isinstance(node, exp.Ceil) else "FLOOR"
        from .sql import _java_narrowed
        return _with_args(_java_narrowed(f"{fn}(fj_arg0)", "BIGINT", from_double=True), [operand]), node, ("int", 20)  # type: ignore[return-value]
    places = _places(operands[1] if len(operands) > 1 else node.args.get("decimals"))
    if found is not None and found[0] == "int" and isinstance(node, (exp.Ceil, exp.Floor)) and node.args.get("decimals") is None:
        return _with_args("CAST(fj_arg0 AS BIGINT)", [operand]), node, ("int", 20)  # type: ignore[return-value]  # a whole number's ceil is itself, a BIGINT in Spark
    if found is None or found[0] == "int" or places is None:
        return None
    p, s = _operand(found)
    plain = isinstance(node, (exp.Ceil, exp.Floor)) and node.args.get("decimals") is None
    if plain and s == 0:
        return None  # ceil and floor of decimal(p, 0) keep its type, as DuckDB's do
    scale = max(0, min(s, places))
    precision = min(MAX, max(p - s + 1, 1 - places) + scale)
    if isinstance(node, (exp.Ceil, exp.Floor)) and not plain:
        text = _ceil_floor("CEIL" if isinstance(node, exp.Ceil) else "FLOOR", p, s, places)
        if text is None:
            return None
        return _with_args(f"CAST({text} AS DECIMAL({precision}, {scale}))", [operand]), node, (precision, scale)
    return _with_args(f"CAST(fj_arg0 AS DECIMAL({precision}, {scale}))", [node]), node, (precision, scale)


def _is_double(node: exp.Expr, annotated: dict[int, exp.Expr]) -> bool:
    while isinstance(node, (exp.Paren, exp.Neg)):
        node = node.this
    copy = annotated.get(id(node))
    kind = copy.type if copy is not None else None
    return isinstance(kind, exp.DataType) and kind.this in (exp.DataType.Type.DOUBLE, exp.DataType.Type.FLOAT)


def _ceil_floor(fn: str, p: int, s: int, places: int) -> str | None:
    """Spark's ceil(x, places) and floor(x, places) of a decimal(p, s), in decimals throughout:
    sqlglot's translation goes through POWER, a DOUBLE."""
    if places >= s:
        return "fj_arg0"
    if places >= 0:
        if p + places > MAX:
            return None  # the value moved left by ``places`` digits wouldn't fit DECIMAL(38)
        up = f"CAST('1{'0' * places}' AS DECIMAL({places + 1}, 0))"
        down = f"CAST('{_unit(places)}' AS DECIMAL({places + 1}, {places}))"
    else:
        if s - places > MAX:
            return None
        up = f"CAST('{_unit(-places)}' AS DECIMAL({1 - places}, {-places}))"
        down = f"CAST('1{'0' * -places}' AS DECIMAL({1 - places}, 0))"
    return f"{fn}(CAST(fj_arg0 AS DECIMAL(38, {s})) * {up}) * {down}"


def _unit(places: int) -> str:
    return "0." + "0" * (places - 1) + "1" if places else "1"


