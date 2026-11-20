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
columns (in the select list and in LATERAL VIEW), reverse of an array reverses it, and sequence of
whole numbers counts down when stop is below start, and to_json writes its argument as Spark does
(spark_json; WR-118). A statement whose types can't be worked out keeps
the array forms; what still differs is listed in docs/sql-compatibility.md ("Arrays, maps and structs
on this PC"). A to_json whose argument can't be typed is refused by name, not written as DuckDB does.
"""
from __future__ import annotations

import logging
from typing import Any, cast

from sqlglot import exp

from . import spark_json, spark_sql_names, sql_dialects

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
        if node.meta.get(spark_sql_names._NAME) == "array_size":  # NULL for NULL, and arrays only, as in Spark
            _put(node, _once("CASE WHEN {a} IS NULL THEN NULL ELSE CAST(ARRAY_LENGTH({a}) AS INT) END", a=node.this))
            continue
        literal_map = isinstance(node.this, (exp.Map, exp.VarMap, exp.MapFromEntries))
        new = _put(node, _size_sql(node.this, is_map=literal_map))
        _mark(new, "size", _once_arg(new) or cast(exp.Is, new.find(exp.Is)).this)
    for node in _mine(list(statement.find_all(exp.Reverse))):
        _mark(node, "reverse", node.this)
    for node in _mine(list(statement.find_all(exp.GenerateSeries))):
        _mark(node, "sequence", node.args["start"])
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
        source = node.meta.pop("kf_complex_source", None)
        found = next((a for a in node.walk() if a.meta.get(_ARG)), None)
        if found is None:
            continue
        arg = found
        dtype = types.get(arg.meta.get(_ID, -1))
        if kind == "typeof" and _int_shift_type(arg, types) is not None:
            node.replace(exp.Literal.string("int"))  # a shift of an int is an int, though its SQL is a long
            continue
        if dtype is None or dtype.is_type(exp.DataType.Type.UNKNOWN, exp.DataType.Type.NULL):
            continue
        is_map = dtype.is_type(exp.DataType.Type.MAP)
        value = source if source is not None else arg
        a = _sql(value)
        if kind == "size" and is_map:
            node.replace(_parse(_size_sql(value, is_map=True)))
        elif kind == "reverse" and dtype.is_type(exp.DataType.Type.ARRAY):
            node.replace(_parse(f"LIST_REVERSE({a})"))
        elif kind == "sequence" and dtype.is_type(*exp.DataType.INTEGER_TYPES):
            whole = "INT" if dtype.is_type(exp.DataType.Type.INT) else "BIGINT"
            parts = {"s": node.args["start"], "e": node.args["end"]}
            step = node.args.get("step")
            if step is not None:
                parts["b"] = step
            by = "{b}" if step is not None else "CASE WHEN {s} <= {e} THEN 1 ELSE -1 END"
            node.replace(_parse(_once(f"CAST(GENERATE_SERIES({{s}}, {{e}}, {by}) AS {whole}[])", **parts)))
        elif kind == "explode" and is_map:
            _explode_map(node, a)
        elif kind == "lateral" and is_map:
            _lateral_map(node, value)
        elif kind == "int_shift" and dtype.is_type(*_INT_SHIFTED):
            # spark_builtins_sql._int_when_int: the int path's value, typed as the int Spark gives
            new = exp.Cast(this=node.copy(), to=exp.DataType.build("INT"))
            new.meta[TYPED] = True
            new.type = exp.DataType.build("INT")
            node.replace(new)
        elif kind == "typeof" and (name := spark_json.type_name(dtype)) is not None:
            node.replace(exp.Literal.string(name))
    for node, json_type in reversed(json_types):
        _to_json(node, json_type)
    return _unmark(statement)


_INT_SHIFTED = (exp.DataType.Type.TINYINT, exp.DataType.Type.SMALLINT, exp.DataType.Type.INT)


def _int_shift_type(value: exp.Expr, types: dict[int, Any]) -> exp.DataType | None:
    """INT when ``value`` is a shift of a tinyint, smallint or int not yet cast to one (an "int_shift" mark
    written after the typeof around it), else None."""
    while isinstance(value, exp.Paren) and value.meta.get(_KEY) != "int_shift":
        value = value.this
    if value.meta.get(_KEY) != "int_shift":
        return None
    arg = next((a for a in value.walk() if a is not value and a.meta.get(_ARG)), None)
    dtype = types.get(arg.meta.get(_ID, -1)) if arg is not None else None
    return exp.DataType.build("INT") if dtype is not None and dtype.is_type(*_INT_SHIFTED) else None


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
