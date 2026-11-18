"""Spark's answers for arrays, maps and generators on this PC (WR-109), where DuckDB's own differ.

Written into every Spark SQL statement (sql.spark_fixes, so spark.sql, %%sql and the DuckDB engine's
Databricks SQL share it):

- array_contains is NULL when the value isn't found and the array holds a NULL, or either is NULL.
- array_position is 0 when the value isn't found, and a bigint.
- flatten is NULL when one of the inner arrays is NULL.
- map_concat stops with Spark's message when two maps share a key (spark.sql.mapKeyDedupPolicy is
  EXCEPTION by default).
- size and cardinality are -1 for NULL (spark.sql.legacy.sizeOfNull, true with ANSI off), and an int.
- LATERAL VIEW OUTER explode (and posexplode) of an empty or NULL array keeps the row, with NULLs.

Some answers depend on whether a column is an array or a map, which only the bound tables tell. Once
spark.sql has bound them (spark_local), ``spark_complex_types`` types the statement and gives a map
argument its own form: size and cardinality count its keys, explode gives ``key`` and ``value``
columns (in the select list and in LATERAL VIEW), reverse of an array reverses it, sequence is
written as Spark gives it (_sequence_sql; a sequence of literals needs no tables, so it is written before), and to_json writes its argument as Spark does
(spark_json; WR-118). A statement whose types can't be worked out keeps
the array forms; what still differs is listed in docs/sql-compatibility.md ("Arrays, maps and structs
on this PC"). A to_json whose argument can't be typed is refused by name, not written as DuckDB does.
"""
from __future__ import annotations

import logging
from typing import Any, cast

from sqlglot import exp

from . import spark_json, sql_dialects

log = logging.getLogger(__name__)

_KEY = "kf_complex"
_ARG = "kf_complex_arg"
_ID = "kf_complex_id"
TYPED = "kf_complex_typed"  # a rewrite whose type is known, though sqlglot can't work it out from its SQL
_MAP_KEYS = ("map_concat found a key in more than one of its maps, so nothing ran: Spark stops on it too "
             "([DUPLICATED_MAP_KEY]), since spark.sql.mapKeyDedupPolicy is EXCEPTION by default. Remove the "
             "repeated keys, or run it in Fabric with the policy set to LAST_WIN.")


def _sql(node: exp.Expr) -> str:
    from .sql import duckdb_sql
    return duckdb_sql(node.copy())


def _parse(text: str) -> exp.Expr:
    return sql_dialects.parse_one(text, read="duckdb")


def _put(node: exp.Expr, text: str) -> exp.Expr:
    new = _parse(text)
    node.replace(new)
    return new


def _mark(node: exp.Expr, kind: str, arg: exp.Expr) -> None:
    """``node`` is fixed by its argument's type, ``arg`` (a node inside ``node``, typed in place)."""
    node.meta[_KEY] = kind
    arg.meta[_ARG] = True


# Functions whose value changes each time they're read: an argument holding one is read once.
_VOLATILE = (exp.Rand, exp.Randn, exp.Uuid)


def _once(template: str, **parts: exp.Expr) -> str:
    """``template`` with each ``{name}`` filled in by its part's SQL. A part may be written more than once,
    so when one holds a function such as rand() or shuffle(), whose value changes each time it is read,
    the parts are read once, through a one-element list, and the template reads them from there."""
    if not any(part.find(*_VOLATILE) for part in parts.values()):
        return template.format(**{name: _sql(part) for name, part in parts.items()})
    fields = ", ".join(f"'kf_{name}': {_sql(part)}" for name, part in parts.items())
    body = template.format(**{name: f"kf_once.kf_{name}" for name in parts})
    return f"(LIST_TRANSFORM([{{{fields}}}], kf_once -> {body}))[1]"


def _once_arg(new: exp.Expr, name: str = "a") -> exp.Expr | None:
    """The part named ``name`` that _once read once, when ``new`` is _once's wrapper; None when _once
    wrote the template directly (a struct of the query's own is never taken for it)."""
    transform = new.this.this if isinstance(new, exp.Bracket) and isinstance(new.this, exp.Paren) else None
    if not isinstance(transform, exp.Transform) or not isinstance(transform.this, exp.Array):
        return None
    row = transform.this.expressions[0] if transform.this.expressions else None
    for field in row.expressions if isinstance(row, exp.Struct) else []:
        if isinstance(field, exp.PropertyEQ) and field.name == f"kf_{name}":
            return field.expression
    return None


def _size_sql(arg: exp.Expr, is_map: bool) -> str:
    count = "CARDINALITY({a})" if is_map else "ARRAY_LENGTH({a})"
    return _once(f"CASE WHEN {{a}} IS NULL THEN -1 ELSE CAST({count} AS INT) END", a=arg)


_CALLS = (exp.ArrayContains, exp.ArrayPosition, exp.Flatten, exp.ArraySize, exp.Reverse, exp.GenerateSeries,
          exp.Explode, exp.Lateral, exp.Anonymous, exp.JSONFormat)
_WRITTEN = "kf_complex_written"


def mark_calls(statement: exp.Expression) -> exp.Expression:
    """Mark the calls the query itself makes, before other rewrites write their own (an arrays_zip's
    sizes, array_repeat's range), so spark_complex_results fixes only these."""
    for node in statement.find_all(*_CALLS):
        node.meta[_WRITTEN] = True
    return statement


def _mine(nodes: Any) -> list[Any]:
    return [n for n in nodes if n.meta.pop(_WRITTEN, False)]


def spark_complex_results(statement: exp.Expression) -> exp.Expression:
    """``statement`` with the fixes in the module docstring that don't need its types."""
    holder = exp.Tuple(expressions=[statement])  # so the statement itself can be replaced, as in spark_expr
    for node in _mine(list(statement.find_all(exp.ArrayContains))):
        _put(node, _once("CASE WHEN {a} IS NULL OR {v} IS NULL THEN NULL WHEN LIST_CONTAINS({a}, {v}) THEN TRUE "
                         "WHEN LIST_COUNT({a}) < ARRAY_LENGTH({a}) THEN NULL ELSE FALSE END",
                         a=node.this, v=node.expression))
    for node in _mine(list(statement.find_all(exp.ArrayPosition))):
        _put(node, _once("CASE WHEN {a} IS NULL OR {v} IS NULL THEN NULL "
                         "ELSE CAST(COALESCE(LIST_POSITION({a}, {v}), 0) AS BIGINT) END", a=node.this, v=node.expression))
    for node in _mine(list(statement.find_all(exp.Flatten))):
        _put(node, _once("CASE WHEN {a} IS NULL OR LIST_COUNT({a}) < ARRAY_LENGTH({a}) THEN NULL ELSE FLATTEN({a}) END",
                         a=node.this))
    for node in _mine(list(statement.find_all(exp.Anonymous))):
        if node.name.upper() == "MAP_CONCAT" and len(node.expressions) >= 2:
            maps = {f"m{i}": m for i, m in enumerate(node.expressions)}
            joined = f"MAP_CONCAT({', '.join('{' + m + '}' for m in maps)})"
            nulls = " OR ".join(f"{{{m}}} IS NULL" for m in maps)
            keys = " + ".join(f"ARRAY_LENGTH(MAP_KEYS({{{m}}}))" for m in maps)
            stop = exp.Literal.string(_MAP_KEYS).sql("duckdb").replace("{", "{{").replace("}", "}}")
            _put(node, _once(f"CASE WHEN {nulls} THEN NULL WHEN ARRAY_LENGTH(MAP_KEYS({joined})) < {keys} "
                             f"THEN ERROR({stop}) ELSE {joined} END", **maps))
    for node in _mine(list(statement.find_all(exp.ArraySize))):
        literal_map = isinstance(node.this, (exp.Map, exp.VarMap, exp.MapFromEntries))
        new = _put(node, _size_sql(node.this, is_map=literal_map))
        _mark(new, "size", _once_arg(new) or cast(exp.Is, new.find(exp.Is)).this)
    for node in _mine(list(statement.find_all(exp.Reverse))):
        _mark(node, "reverse", node.this)
    for node in reversed(_mine(list(statement.find_all(exp.GenerateSeries)))):  # innermost first
        written = _sequence_sql(node, [_literal_type(node.args.get(k)) for k in _SEQUENCE_ARGS])
        if written is not None:
            _put(node, written)
            continue
        node.meta[_KEY] = "sequence"  # typed once the tables are bound (spark_complex_types)
        for k in _SEQUENCE_ARGS:
            if node.args.get(k) is not None:
                node.args[k].meta[_ARG] = True
    for lateral in _mine(list(statement.find_all(exp.Lateral))):
        call = lateral.this
        # exp.Inline: stack, json_tuple and inline, as spark_builtins_sql writes them after LATERAL VIEW
        if isinstance(call, (exp.Explode, exp.Posexplode, exp.Inline)) and not isinstance(call, exp.ExplodeOuter):
            lateral.meta[_KEY] = "lateral"
            lateral.meta["kf_complex_source"] = call.this.copy()
            if lateral.args.get("outer"):
                call.set("this", _parse(_once("CASE WHEN {a} IS NULL OR ARRAY_LENGTH({a}) = 0 THEN [NULL] ELSE {a} END",
                                              a=call.this)))
            source = (_once_arg(call.this) or call.this.find(exp.Is).this) if lateral.args.get("outer") else call.this
            source.meta[_ARG] = True
    for node in _mine(list(statement.find_all(exp.Explode))):
        if not isinstance(node.parent, exp.Lateral) and type(node) is exp.Explode:
            _mark(node, "explode", node.this)
    for node in _mine(list(statement.find_all(exp.JSONFormat))):
        spark_json.options_of(node)  # an option this PC doesn't read is refused before anything is typed
        _mark(node, "to_json", node.this)
    statement = cast(exp.Expression, holder.expressions[0])
    statement.pop()
    for node in statement.walk():
        node.meta.pop(_WRITTEN, None)
    return statement


def spark_complex_types(statement: exp.Expr, conn: Any, kinds: tuple[str, ...] | None = None) -> exp.Expr:
    """``statement`` with the map forms and typed fixes in the module docstring. Its tables must be bound
    to the connection's views already. When its types can't be worked out it is returned as it was, but
    a to_json in it is refused (spark_json.Refused). ``kinds``: only those fixes (the others' marks stay)."""
    marked = [n for n in statement.walk() if _KEY in n.meta and (kinds is None or n.meta[_KEY] in kinds)]
    if not marked:
        return statement
    to_json = [n for n in marked if n.meta[_KEY] == "to_json"]
    from sqlglot.optimizer.annotate_types import annotate_types
    from sqlglot.optimizer.qualify import qualify

    from .spark_decimals import _schema
    args = [n for n in statement.walk() if n.meta.get(_ARG)]
    for i, arg in enumerate(args):
        arg.meta[_ID] = i
    try:
        schema = _schema(conn)
        copy = qualify(statement.copy(), schema=schema, dialect="spark", validate_qualify_columns=False,
                       quote_identifiers=False, identify=False)
        for n in copy.walk():  # only the types rewrites gave are kept; the rest are worked out here
            if not n.meta.get(TYPED):
                n.type = None
        annotate_types(copy, schema=schema, dialect="spark", overwrite_types=False)
        types = {n.meta[_ID]: n.type for n in copy.walk() if _ID in n.meta}
    except Exception as exc:  # noqa: BLE001 - a type fix only: untyped statements keep the array forms; logged
        log.info("Spark map forms not applied: %s: %s", type(exc).__name__, exc)
        if to_json:
            raise spark_json.refused(spark_json.UNTYPED, spark_json.UNTYPED_STEP) from None
        return _unmark(statement)
    # to_json is written last, innermost first, from its argument as the other fixes leave it
    json_types = [(n, types.get(n.this.meta.get(_ID, -1))) for n in to_json]
    for node in marked:
        if any(node is n for n in to_json):
            node.meta.pop(_KEY)
            continue
        kind = node.meta.pop(_KEY)
        if kind == "sequence":
            arg_types = [types.get(a.meta.get(_ID, -1)) if (a := node.args.get(k)) is not None else None
                         for k in _SEQUENCE_ARGS]
            if (written := _sequence_sql(node, arg_types)) is not None:
                node.replace(_parse(written))
            continue
        source = node.meta.pop("kf_complex_source", None)
        found = next((a for a in node.walk() if a.meta.get(_ARG)), None)
        if found is None:
            continue
        arg = found
        dtype = types.get(arg.meta.get(_ID, -1))
        if dtype is None or dtype.is_type(exp.DataType.Type.UNKNOWN, exp.DataType.Type.NULL):
            continue
        is_map = dtype.is_type(exp.DataType.Type.MAP)
        value = source if source is not None else arg
        a = _sql(value)
        if kind == "size" and is_map:
            node.replace(_parse(_size_sql(value, is_map=True)))
        elif kind == "reverse" and dtype.is_type(exp.DataType.Type.ARRAY):
            node.replace(_parse(f"LIST_REVERSE({a})"))
        elif kind == "explode" and is_map:
            _explode_map(node, a)
        elif kind == "lateral" and is_map:
            _lateral_map(node, value)
        elif kind == "typeof" and (name := spark_json.type_name(dtype)) is not None:
            node.replace(exp.Literal.string(name))
    for node, json_type in reversed(json_types):
        _to_json(node, json_type)
    return _unmark(statement)


_SEQUENCE_ARGS = ("start", "end", "step")
_T = exp.DataType.Type
_WHOLE = {_T.TINYINT: "TINYINT", _T.SMALLINT: "SMALLINT", _T.INT: "INT", _T.BIGINT: "BIGINT"}
_WHOLE_ORDER = ("TINYINT", "SMALLINT", "INT", "BIGINT")
_TIMES = {_T.DATE: "DATE", _T.TIMESTAMP: "TIMESTAMP", _T.TIMESTAMPNTZ: "TIMESTAMP", _T.TIMESTAMPTZ: "TIMESTAMPTZ",
          _T.TIMESTAMPLTZ: "TIMESTAMPTZ"}
# Units of an interval literal with no months in it: adding it step by step gives what adding it i times does.
_FIXED_UNITS = frozenset({"WEEK", "WEEKS", "DAY", "DAYS", "HOUR", "HOURS", "MINUTE", "MINUTES", "SECOND", "SECONDS",
                          "MILLISECOND", "MILLISECONDS", "MICROSECOND", "MICROSECONDS"})


def _literal_type(arg: exp.Expr | None) -> exp.DataType | None:
    """``arg``'s type when it is a literal (a whole number, a typed literal such as DATE'...', an interval), so a
    sequence of literals needs no tables to be typed; None otherwise."""
    if arg is None:
        return None
    value = arg.this if isinstance(arg, exp.Neg) else arg
    if isinstance(value, exp.Literal) and not value.is_string and value.is_int:
        n = int(value.this)
        return exp.DataType.build("INT" if -2**31 <= n < 2**31 else "BIGINT") if -2**63 <= n < 2**63 else None
    if isinstance(arg, exp.Cast) and isinstance(arg.this, (exp.Literal, exp.Null)):
        return arg.to
    if isinstance(arg, exp.Interval):
        return exp.DataType.build("INTERVAL")
    return None


def _sequence_sql(node: exp.Expr, types: list[exp.DataType | None]) -> str | None:
    """Spark's sequence(start, stop[, step]) as DuckDB SQL, from its arguments' types, or None when they aren't
    ones handled here. Whole numbers count down when stop is below start, and keep the wider of start's and stop's
    types. Dates and timestamps step by a day (or minus a day) by default; a step with months in it gives
    start + i * step for each i, as Spark does, not each element plus the step (Jan 30 + 2 months is Mar 30, not
    Mar 29), and stops at stop. Dates stay dates."""
    start, end, step = (node.args.get(k) for k in _SEQUENCE_ARGS)
    s_type, e_type, b_type = types
    if start is None or end is None or s_type is None or e_type is None:
        return None
    parts = {"s": start, "e": end}
    if step is not None:
        parts["b"] = step
    wholes = [_WHOLE.get(t.this) for t in (s_type, e_type)]
    if all(wholes) and (b_type is None or b_type.this in _WHOLE):
        whole = max(wholes, key=lambda w: _WHOLE_ORDER.index(w or ""))
        by = "{b}" if step is not None else "CASE WHEN {s} <= {e} THEN 1 ELSE -1 END"
        return _once(f"CAST(GENERATE_SERIES({{s}}, {{e}}, {by}) AS {whole}[])", **parts)
    times = [_TIMES.get(t.this) for t in (s_type, e_type)]
    if not all(times) or (step is not None and (b_type is None or not b_type.is_type(_T.INTERVAL))):
        return None
    out = "DATE" if times == ["DATE", "DATE"] else "TIMESTAMPTZ" if "TIMESTAMPTZ" in times else "TIMESTAMP"
    calc = "TIMESTAMP" if out == "DATE" else out
    s, e = f"CAST({{s}} AS {calc})", f"CAST({{e}} AS {calc})"
    unit = step.args.get("unit") if isinstance(step, exp.Interval) else None
    if step is None:
        series = f"GENERATE_SERIES({s}, {e}, CASE WHEN {s} <= {e} THEN INTERVAL 1 DAY ELSE INTERVAL -1 DAY END)"
    elif isinstance(unit, exp.Var) and unit.name.upper() in _FIXED_UNITS:
        series = f"GENERATE_SERIES({s}, {e}, {{b}})"
    else:
        # Spark's estimate of the step, a month counted as 28 days, gives its direction and the most elements
        # there can be; those past stop are dropped. DuckDB's EPOCH counts a month as 30 days.
        est = "(EPOCH({b}) - 172800 * (DATEPART('year', {b}) * 12 + DATEPART('month', {b})))"
        count = (f"CASE WHEN {s} = {e} THEN 1 WHEN ({est} > 0 AND {s} < {e}) OR ({est} < 0 AND {s} > {e}) "
                 f"THEN CAST(FLOOR(ABS(EPOCH({e}) - EPOCH({s})) / ABS({est})) AS BIGINT) + 1 ELSE 0 END")
        series = (f"LIST_FILTER(LIST_TRANSFORM(RANGE(0, {count}), LAMBDA kf_i: {s} + {{b}} * kf_i), "
                  f"LAMBDA kf_v: CASE WHEN {est} > 0 THEN kf_v <= {e} ELSE kf_v >= {e} END)")
    return _once(f"CAST({series} AS {out}[])", **parts)


def _to_json(node: exp.Expr, dtype: exp.DataType | None) -> None:
    """to_json ``node`` as Spark writes its argument of type ``dtype`` (spark_json)."""
    if dtype is None or dtype.is_type(exp.DataType.Type.UNKNOWN, exp.DataType.Type.NULL):
        raise spark_json.refused(spark_json.UNTYPED, spark_json.UNTYPED_STEP)
    options = spark_json.options_of(node)
    value = node.this
    if isinstance(value, exp.Column):
        node.replace(_parse(spark_json.to_json_sql(_sql(value), dtype, options)))
        return
    # The argument is written many times over, so it is read once, through a one-element list. It goes in
    # as it is, not as SQL text read again: DuckDB's -> (json_extract) inside it would read as a lambda.
    body = spark_json.to_json_sql("kf_once.kf_a", dtype, options)
    new = _parse(f"(LIST_TRANSFORM([{{'kf_a': kf_json_value}}], kf_once -> {body}))[1]")
    slot = next(c for c in new.find_all(exp.Column) if c.name == "kf_json_value")
    value.pop()
    slot.replace(value)
    node.replace(new)


def _unmark(statement: exp.Expr) -> exp.Expr:
    for node in statement.walk():
        for key in (_KEY, _ARG, _ID, TYPED, "kf_complex_source"):
            node.meta.pop(key, None)
    return statement


def _explode_map(node: exp.Expr, a: str) -> None:
    """explode(map) in a select list: Spark's ``key`` and ``value`` columns."""
    item = node.parent if isinstance(node.parent, exp.Alias) else node
    select = item.parent
    if not isinstance(select, exp.Select):
        return
    at = select.expressions.index(item)
    pair = [_parse(f"UNNEST(MAP_KEYS({a})) AS key"), _parse(f"UNNEST(MAP_VALUES({a})) AS value")]
    select.set("expressions", select.expressions[:at] + pair + select.expressions[at + 1:])


def _quoted(name: str) -> str:
    """``name`` as a quoted DuckDB identifier, its own quotes doubled, so it stays one name."""
    return _sql(exp.to_identifier(name, quoted=True))


def _lateral_map(lateral: exp.Expr, value: exp.Expr) -> None:
    """LATERAL VIEW explode(map) t AS k, v: one row per entry, joined to its row. The map is read once,
    so its keys and values come from the same map even when it holds rand()."""
    alias = lateral.args.get("alias")
    names = [c.name for c in (alias.columns if alias is not None else [])] or ["key", "value"]
    if len(names) != 2:
        return
    table = alias.name if alias is not None and alias.name else "kf_lateral"
    outer = bool(lateral.args.get("outer"))
    a = "kf_map.kf_mv"
    keys = f"CASE WHEN {a} IS NULL OR CARDINALITY({a}) = 0 THEN [NULL] ELSE MAP_KEYS({a}) END" if outer else f"MAP_KEYS({a})"
    values = f"CASE WHEN {a} IS NULL OR CARDINALITY({a}) = 0 THEN [NULL] ELSE MAP_VALUES({a}) END" if outer else f"MAP_VALUES({a})"
    k, v, t = (_quoted(n) for n in (names[0], names[1], table))
    join = _parse(f"SELECT 1 FROM kf_x CROSS JOIN LATERAL (SELECT UNNEST({keys}) AS {k}, "
                  f"UNNEST({values}) AS {v} FROM (SELECT {_sql(value)} AS kf_mv) AS kf_map) AS {t}").args["joins"][0]
    select = lateral.parent
    if not isinstance(select, exp.Select):
        return
    lateral.pop()
    select.append("joins", join)
