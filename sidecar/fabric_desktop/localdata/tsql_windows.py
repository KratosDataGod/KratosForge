"""Local T-SQL's window functions, aggregates, GROUP BY, TOP and OFFSET/FETCH where T-SQL stops (WR-281).

Each check is one Microsoft Learn's T-SQL reference documents, as its pages read on 2026-11-28. DuckDB gave a
silent answer, or its own error, for each of these:

- A ranking or analytic function (ROW_NUMBER, RANK, DENSE_RANK, NTILE, CUME_DIST, PERCENT_RANK, LAG, LEAD,
  FIRST_VALUE, LAST_VALUE) needs an ORDER BY in its OVER clause (row-number-transact-sql and each function's
  syntax); DuckDB numbered the rows in any order.
- "You can't use the OVER clause with the DISTINCT aggregations" (select-over-clause-transact-sql); DuckDB counted.
- LAG and LEAD: "offset cannot be a negative value" (lag-transact-sql); DuckDB read LAG(x, -1) as LEAD(x, 1).
- NTILE takes "a positive integer expression ... of type int, or bigint" (ntile-transact-sql).
- PERCENTILE_CONT and PERCENTILE_DISC: the percentile "must range between 0.0 and 1.0", and "The ORDER BY clause
  and <rows or range clause> of the OVER syntax can't be specified" (percentile-cont-transact-sql); DuckDB read a
  negative percentile as one counted from the other end.
- GROUP BY ... WITH ROLLUP, WITH CUBE and GROUP BY ALL are SQL Server's older forms, and the Fabric Warehouse
  syntax of select-group-by-transact-sql has ROLLUP (...) and CUBE (...) instead; DuckDB stopped on the text.
- TOP: "expression is converted to bigint" unless PERCENT (top-transact-sql); DuckDB rounded TOP (2.7) to 3 rows.
- OFFSET is "greater than or equal to zero" and FETCH "greater than or equal to one", and both need the ORDER BY
  (select-order-by-clause-transact-sql); DuckDB returned no rows for FETCH NEXT 0 ROWS.
"""

from __future__ import annotations

from sqlglot import exp

_NOT_YET = "This T-SQL can't be run on the local engine: "

_NEEDS_ORDER = {exp.RowNumber: "ROW_NUMBER", exp.Rank: "RANK", exp.DenseRank: "DENSE_RANK", exp.Ntile: "NTILE",
                exp.CumeDist: "CUME_DIST", exp.PercentRank: "PERCENT_RANK", exp.Lag: "LAG", exp.Lead: "LEAD",
                exp.FirstValue: "FIRST_VALUE", exp.LastValue: "LAST_VALUE"}
_PERCENTILES: dict[type[exp.Expression], str] = {exp.PercentileCont: "PERCENTILE_CONT",
                                                 exp.PercentileDisc: "PERCENTILE_DISC"}


def _literal(node: exp.Expression | None) -> tuple[bool, float | None]:
    """(True, its value) for a number written in the query, such as 2, -1 or 0.5; (True, None) for NULL;
    (False, None) for anything else."""
    if isinstance(node, exp.Paren):
        return _literal(node.this)
    if isinstance(node, exp.Null):
        return True, None
    sign = 1.0
    if isinstance(node, exp.Neg):
        node, sign = node.this, -1.0
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return True, sign * float(node.name)
        except ValueError:
            return False, None
    return False, None


def check(tree: exp.Expression) -> None:
    """Raise T-SQL's error, or refuse by name, for each case above; and cut a TOP's whole-number count."""
    for window in tree.find_all(exp.Window):
        _window(window)
    for node in tree.find_all(exp.PercentileCont, exp.PercentileDisc):
        assert isinstance(node, exp.PercentileCont | exp.PercentileDisc)
        percentile(node)
    for group in tree.find_all(exp.Group):
        _group(group)
    for select in tree.find_all(exp.Select):
        _rows(select)


def _window(window: exp.Window) -> None:
    function = window.this.this if isinstance(window.this, exp.IgnoreNulls | exp.RespectNulls) else window.this
    if window.args.get("over") is None and not isinstance(window.parent, exp.Window):
        return  # a named window or FILTER: not T-SQL's OVER
    name = next((n for k, n in _NEEDS_ORDER.items() if type(function) is k), None)
    if name is not None and window.args.get("order") is None:
        raise ValueError(f"The function '{name}' must have an OVER clause with ORDER BY.")
    if isinstance(function, exp.AggFunc) and isinstance(function.this, exp.Distinct):
        raise ValueError("Use of DISTINCT is not allowed with the OVER clause.")
    if isinstance(function, exp.Lag | exp.Lead):
        _offset(function, "LAG" if isinstance(function, exp.Lag) else "LEAD")
    elif isinstance(function, exp.Ntile):
        written, value = _literal(function.this)
        if written and value is None:
            raise ValueError(_NOT_YET + "NTILE (NULL) isn't mapped yet. Give NTILE a positive whole number.")
        if written and (value is None or value != int(value) or value < 1):
            raise ValueError("NTILE takes a positive whole number (an int or a bigint) of groups, as in T-SQL. "
                             f"Here it was {function.this.sql(dialect='tsql')}.")
    elif isinstance(function, exp.WithinGroup) and isinstance(function.this, tuple(_PERCENTILES)):
        name = _PERCENTILES[type(function.this)]  # type: ignore[index]  # function.this is one of its keys, checked above
        if window.args.get("order") is not None or window.args.get("spec") is not None:
            raise ValueError(f"{name}'s OVER takes only PARTITION BY, as in T-SQL: "
                             "the ORDER BY goes in WITHIN GROUP, and it takes no ROWS or RANGE.")


def percentile(node: exp.PercentileCont | exp.PercentileDisc) -> None:
    """The percentile is a number from 0.0 to 1.0 written in the query, as T-SQL takes it."""
    name = _PERCENTILES[type(node)]
    written, value = _literal(node.this)
    if not written:
        raise ValueError(_NOT_YET + f"{name} takes its percentile as a number written in the query, as T-SQL "
                         "does, such as 0.5.")
    if value is None:
        raise ValueError(_NOT_YET + f"{name} (NULL) isn't mapped yet. Give {name} a number from 0.0 to 1.0.")
    if not 0 <= value <= 1:
        raise ValueError(f"The percentile of {name} must range between 0.0 and 1.0, as in T-SQL. "
                         f"Here it was {node.this.sql(dialect='tsql')}.")


def _offset(function: exp.Lag | exp.Lead, name: str) -> None:
    offset = function.args.get("offset")
    written, value = _literal(offset)
    if not written:
        return  # checked as it runs (tsql._lag_types)
    if value is None:
        raise ValueError(_NOT_YET + f"{name} with a NULL offset isn't mapped yet. Give the offset as a "
                         "whole number of 0 or more.")
    if value < 0:
        raise ValueError(f"The offset of {name} can't be a negative value, as in T-SQL. Here it was "
                         f"{offset.sql(dialect='tsql') if offset is not None else ''}; use "
                         f"{'LEAD' if name == 'LAG' else 'LAG'} to look the other way.")
    if value != int(value):
        function.set("offset", exp.Literal.number(int(value)))  # converted to bigint: toward zero


def _group(group: exp.Group) -> None:
    rollup, cube = group.args.get("rollup") or [], group.args.get("cube") or []
    if any(isinstance(r, exp.Rollup) and not r.expressions for r in rollup) or \
            any(isinstance(c, exp.Cube) and not c.expressions for c in cube):
        which = "ROLLUP" if rollup else "CUBE"
        raise ValueError(f"GROUP BY ... WITH {which} is SQL Server's older form, which a Fabric Warehouse "
                         f"doesn't take. Write GROUP BY {which} (a, b) instead.")
    if group.args.get("all"):
        raise ValueError(_NOT_YET + "GROUP BY ALL isn't mapped yet (it is SQL Server's older form). Group by the "
                         "columns, and filter in HAVING or a CASE.")


def _rows(select: exp.Select) -> None:
    limit, offset = select.args.get("limit"), select.args.get("offset")
    if isinstance(offset, exp.Offset):
        if select.args.get("order") is None:
            raise ValueError("OFFSET needs an ORDER BY, as in T-SQL.")
        _count(offset, "expression", "OFFSET", 0)
    if isinstance(limit, exp.Fetch) and limit.args.get("count") is not None:
        options = limit.args.get("limit_options")
        if not (options is not None and options.args.get("percent")):
            _count(limit, "count", "FETCH", 1)
    elif isinstance(limit, exp.Limit):
        options = limit.args.get("limit_options")
        if not (options is not None and options.args.get("percent")):  # TOP ... PERCENT is a float (tsql_tables)
            _count(limit, "expression", "TOP", 0)


def _count(node: exp.Expression, key: str, clause: str, low: int) -> None:
    written, value = _literal(node.args.get(key))
    if not written:
        return
    shown = node.args[key].sql(dialect="tsql")
    if value is None:
        raise ValueError(_NOT_YET + f"{clause} (NULL) isn't mapped yet. Give {clause} a whole number of rows.")
    if value < low:
        least = "0 or more" if low == 0 else "1 or more"
        raise ValueError(f"The number of rows in a {clause} clause has to be {least}, as in T-SQL. Here it was "
                         f"{shown}.")
    if value != int(value):
        if clause != "TOP":
            raise ValueError(f"The number of rows provided for a {clause} clause must be an integer. "
                             f"Here it was {shown}.")
        node.set(key, exp.Literal.number(int(value)))  # TOP's count is converted to bigint: toward zero


def written_number(node: exp.Expression | None) -> bool:
    """A number or NULL written in the query, which check() has seen to already."""
    return _literal(node)[0]
