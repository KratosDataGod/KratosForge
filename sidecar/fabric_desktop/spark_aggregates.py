"""Spark's aggregate results on DuckDB (WR-108): what an aggregate in Spark SQL or the DataFrame API
gives in Fabric, where DuckDB's own aggregate answers differently.

- collect_list, collect_set and array_agg give an empty array, not NULL, when no row has a value
  (no rows, or only NULLs), as aggregates and over a window.
- count(a, b, ...) counts the rows where none of them is NULL.
- GROUP BY a, b WITH ROLLUP (or WITH CUBE) is GROUP BY ROLLUP(a, b).
- approx_count_distinct(x, rsd) runs DuckDB's estimate; rsd doesn't change it (docs/sql-compatibility.md).
- percentile, median and percentile_cont give a double (an array of doubles for an array of
  percentages), as Spark does; percentile_approx and percentile_disc keep the column's type, as
  Databricks documents (percentile_disc(0.5) of ints is 6).
- grouping() is a tinyint, count_if() and regr_count() a bigint.

DuckDB's sum of an int or bigint is a 128-bit integer, which Spark doesn't have; spark_local's writes
store it as a bigint, and typeof and the schema name it bigint.
"""
from __future__ import annotations

from typing import Any

from sqlglot import exp

# The aggregates whose empty answer is an empty array in Spark.
_ARRAYS = (exp.ArrayAgg, exp.ArrayUniqueAgg)


def _outer(node: exp.Expr) -> exp.Expr:
    """The whole call: an aggregate with its FILTER and OVER."""
    while isinstance(node.parent, (exp.Filter, exp.Window)) and node.parent.this is node:
        node = node.parent
    return node


def _cast(node: exp.Expr, to: str) -> None:
    outer = _outer(node)
    if isinstance(outer.parent, exp.Cast) and outer.parent.this is outer:
        return  # already cast once
    hold = exp.Null()
    outer.replace(hold)
    hold.replace(exp.Cast(this=outer, to=exp.DataType.build(to)))


def _quantile_is_array(node: exp.Expr) -> bool:
    return isinstance(node.args.get("quantile"), exp.Array)


def spark_aggregate_results(statement: exp.Expression) -> exp.Expression:
    """``statement`` with Spark's answers for the aggregates listed in the module docstring."""
    for select in statement.find_all(exp.Select):
        group = select.args.get("group")
        if group is None:
            continue
        for kind in ("rollup", "cube"):
            # WITH ROLLUP: a ROLLUP of no expressions after the GROUP BY list (sqlglot's reading).
            marks = group.args.get(kind) or []
            if len(marks) == 1 and not marks[0].expressions and group.expressions:
                columns = [e.pop() for e in list(group.expressions)]
                group.set("expressions", [])
                group.set(kind, [(exp.Rollup if kind == "rollup" else exp.Cube)(expressions=columns)])
    for count in list(statement.find_all(exp.Count)):
        extra = count.expressions
        if extra and not isinstance(count.this, exp.Distinct):
            every = [count.this, *extra]
            condition = exp.and_(*[exp.Not(this=exp.Is(this=e.copy(), expression=exp.Null())) for e in every])
            count.set("expressions", [])
            count.set("this", exp.Case(ifs=[exp.If(this=condition, true=exp.Literal.number(1))]))
    for approx in list(statement.find_all(exp.ApproxDistinct)):
        approx.set("accuracy", None)
    node: exp.Expr
    for node in list(statement.find_all(*_ARRAYS)):
        outer = _outer(node)
        if isinstance(outer.parent, exp.Coalesce) and outer.parent.this is outer:
            continue
        hold = exp.Null()
        outer.replace(hold)
        hold.replace(exp.Coalesce(this=outer, expressions=[exp.Array(expressions=[])]))
    for node in list(statement.find_all(exp.Quantile, exp.Median, exp.PercentileCont)):
        if isinstance(node, exp.ApproxQuantile):
            continue  # percentile_approx: a value from the column, in its type
        if isinstance(node, exp.PercentileCont):
            target: exp.Expr = node.parent if isinstance(node.parent, exp.WithinGroup) else node
            _cast(target, "DOUBLE")
        else:
            _cast(node, "DOUBLE[]" if _quantile_is_array(node) else "DOUBLE")
    for node in list(statement.find_all(exp.Grouping)):
        _cast(node, "TINYINT")
    for node in list(statement.find_all(exp.CountIf, exp.RegrCount)):
        _cast(node, "BIGINT")
    return statement


def empty_array(sql: str) -> str:
    """DuckDB SQL for a collect_list or collect_set that gives [] where DuckDB gives NULL."""
    return f"COALESCE({sql}, [])"


def wide_ints_as_long(type_map: dict[str, Any], long_type: Any) -> None:
    """The DataFrame schema's name for DuckDB's 128-bit integer: Spark's long (see the module docstring)."""
    type_map["hugeint"] = long_type
