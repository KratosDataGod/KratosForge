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
whole numbers counts down when stop is below start. A statement whose types can't be worked out keeps
the array forms; what still differs is listed in docs/sql-compatibility.md ("Arrays, maps and structs
on this PC").
"""
from __future__ import annotations

import logging
from typing import Any, cast

from sqlglot import exp

from . import sql_dialects

log = logging.getLogger(__name__)

_KEY = "kf_complex"
_ARG = "kf_complex_arg"
_ID = "kf_complex_id"
_MAP_KEYS = ("The key is already in the first map ([DUPLICATED_MAP_KEY]): Spark stops on a map key that "
             "repeats (spark.sql.mapKeyDedupPolicy is EXCEPTION), so nothing ran. Remove the duplicate keys, "
             "or set the policy to LAST_WIN in Fabric.")


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


def _size_sql(arg: str, is_map: bool) -> str:
    count = f"CARDINALITY({arg})" if is_map else f"ARRAY_LENGTH({arg})"
    return f"CASE WHEN {arg} IS NULL THEN -1 ELSE CAST({count} AS INT) END"


_CALLS = (exp.ArrayContains, exp.ArrayPosition, exp.Flatten, exp.ArraySize, exp.Reverse, exp.GenerateSeries,
          exp.Explode, exp.Lateral, exp.Anonymous)
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
        a, v = _sql(node.this), _sql(node.expression)
        _put(node, f"CASE WHEN {a} IS NULL OR {v} IS NULL THEN NULL WHEN LIST_CONTAINS({a}, {v}) THEN TRUE "
                   f"WHEN LIST_COUNT({a}) < ARRAY_LENGTH({a}) THEN NULL ELSE FALSE END")
    for node in _mine(list(statement.find_all(exp.ArrayPosition))):
        a, v = _sql(node.this), _sql(node.expression)
        _put(node, f"CASE WHEN {a} IS NULL OR {v} IS NULL THEN NULL "
                   f"ELSE CAST(COALESCE(LIST_POSITION({a}, {v}), 0) AS BIGINT) END")
    for node in _mine(list(statement.find_all(exp.Flatten))):
        a = _sql(node.this)
        _put(node, f"CASE WHEN {a} IS NULL OR LIST_COUNT({a}) < ARRAY_LENGTH({a}) THEN NULL ELSE FLATTEN({a}) END")
    for node in _mine(list(statement.find_all(exp.Anonymous))):
        if node.name.upper() == "MAP_CONCAT" and len(node.expressions) >= 2:
            maps = [_sql(m) for m in node.expressions]
            joined = f"MAP_CONCAT({', '.join(maps)})"
            nulls = " OR ".join(f"{m} IS NULL" for m in maps)
            keys = " + ".join(f"ARRAY_LENGTH(MAP_KEYS({m}))" for m in maps)
            _put(node, f"CASE WHEN {nulls} THEN NULL WHEN ARRAY_LENGTH(MAP_KEYS({joined})) < {keys} "
                       f"THEN ERROR({exp.Literal.string(_MAP_KEYS).sql('duckdb')}) ELSE {joined} END")
    for node in _mine(list(statement.find_all(exp.ArraySize))):
        literal_map = isinstance(node.this, (exp.Map, exp.VarMap, exp.MapFromEntries))
        new = _put(node, _size_sql(_sql(node.this), is_map=literal_map))
        _mark(new, "size", cast(exp.Is, new.find(exp.Is)).this)
    for node in _mine(list(statement.find_all(exp.Reverse))):
        _mark(node, "reverse", node.this)
    for node in _mine(list(statement.find_all(exp.GenerateSeries))):
        _mark(node, "sequence", node.args["start"])
    for lateral in _mine(list(statement.find_all(exp.Lateral))):
        call = lateral.this
        if isinstance(call, (exp.Explode, exp.Posexplode)) and not isinstance(call, exp.ExplodeOuter):
            lateral.meta[_KEY] = "lateral"
            lateral.meta["kf_complex_source"] = call.this.copy()
            if lateral.args.get("outer"):
                a = _sql(call.this)
                call.set("this", _parse(f"CASE WHEN {a} IS NULL OR ARRAY_LENGTH({a}) = 0 THEN [NULL] ELSE {a} END"))
            source = call.this.find(exp.Is).this if lateral.args.get("outer") else call.this
            source.meta[_ARG] = True
    for node in _mine(list(statement.find_all(exp.Explode))):
        if not isinstance(node.parent, exp.Lateral) and type(node) is exp.Explode:
            _mark(node, "explode", node.this)
    statement = cast(exp.Expression, holder.expressions[0])
    statement.pop()
    for node in statement.walk():
        node.meta.pop(_WRITTEN, None)
    return statement


def spark_complex_types(statement: exp.Expr, conn: Any) -> exp.Expr:
    """``statement`` with the map forms and typed fixes in the module docstring. Its tables must be bound
    to the connection's views already. When its types can't be worked out it is returned as it was."""
    marked = [n for n in statement.walk() if _KEY in n.meta]
    if not marked:
        return statement
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
        annotate_types(copy, schema=schema, dialect="spark")
        types = {n.meta[_ID]: n.type for n in copy.walk() if _ID in n.meta}
    except Exception as exc:  # noqa: BLE001 - a type fix only: untyped statements keep the array forms; logged
        log.info("Spark map forms not applied: %s: %s", type(exc).__name__, exc)
        return _unmark(statement)
    for node in marked:
        kind = node.meta.pop(_KEY)
        source = node.meta.pop("kf_complex_source", None)
        found = next((a for a in node.walk() if a.meta.get(_ARG)), None)
        if found is None:
            continue
        arg = found
        dtype = types.get(arg.meta.get(_ID, -1))
        if dtype is None or dtype.is_type(exp.DataType.Type.UNKNOWN, exp.DataType.Type.NULL):
            continue
        is_map = dtype.is_type(exp.DataType.Type.MAP)
        a = _sql(source if source is not None else arg)
        if kind == "size" and is_map:
            node.replace(_parse(_size_sql(a, is_map=True)))
        elif kind == "reverse" and dtype.is_type(exp.DataType.Type.ARRAY):
            node.replace(_parse(f"LIST_REVERSE({a})"))
        elif kind == "sequence" and dtype.is_type(*exp.DataType.INTEGER_TYPES):
            start, end = _sql(node.args["start"]), _sql(node.args["end"])
            whole = "INT" if dtype.is_type(exp.DataType.Type.INT) else "BIGINT"
            step = node.args.get("step")
            by = _sql(step) if step is not None else f"CASE WHEN {start} <= {end} THEN 1 ELSE -1 END"
            node.replace(_parse(f"CAST(GENERATE_SERIES({start}, {end}, {by}) AS {whole}[])"))
        elif kind == "explode" and is_map:
            _explode_map(node, a)
        elif kind == "lateral" and is_map:
            _lateral_map(node, a)
    return _unmark(statement)


def _unmark(statement: exp.Expr) -> exp.Expr:
    for node in statement.walk():
        for key in (_KEY, _ARG, _ID, "kf_complex_source"):
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


def _lateral_map(lateral: exp.Expr, a: str) -> None:
    """LATERAL VIEW explode(map) t AS k, v: one row per entry, joined to its row."""
    alias = lateral.args.get("alias")
    names = [c.name for c in (alias.columns if alias is not None else [])] or ["key", "value"]
    if len(names) != 2:
        return
    table = alias.name if alias is not None and alias.name else "kf_lateral"
    outer = bool(lateral.args.get("outer"))
    keys = f"CASE WHEN {a} IS NULL OR CARDINALITY({a}) = 0 THEN [NULL] ELSE MAP_KEYS({a}) END" if outer else f"MAP_KEYS({a})"
    values = f"CASE WHEN {a} IS NULL OR CARDINALITY({a}) = 0 THEN [NULL] ELSE MAP_VALUES({a}) END" if outer else f"MAP_VALUES({a})"
    join = _parse(f"SELECT 1 FROM kf_x CROSS JOIN LATERAL (SELECT UNNEST({keys}) AS \"{names[0]}\", "
                  f"UNNEST({values}) AS \"{names[1]}\") AS \"{table}\"").args["joins"][0]
    select = lateral.parent
    if not isinstance(select, exp.Select):
        return
    lateral.pop()
    select.append("joins", join)
