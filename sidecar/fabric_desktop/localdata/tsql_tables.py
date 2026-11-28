"""Local T-SQL's table-valued forms as a Fabric Warehouse has them (WR-227), or refused by name.

From Microsoft's T-SQL reference pages for Warehouse in Microsoft Fabric (GENERATE_SERIES, STRING_SPLIT, TOP,
FROM and the table value constructor):

- ``GENERATE_SERIES(start, stop [, step])`` is a table of one column, ``value``, of start's type. The step is 1
  when start is less than stop and -1 when it is greater; a step whose sign points away from stop gives no rows;
  a step of 0 is an error. The series stops before it passes stop. DuckDB names its column ``generate_series``,
  counts down only with a negative step, and has no decimal series, so the series is made here from a count of
  steps: start + i * step for i from 0. A literal NULL argument, and start and stop of different kinds (a whole
  number and a decimal), are refused by name: the page doesn't say what a warehouse answers.
- ``STRING_SPLIT``'s third argument, enable_ordinal: 0 or NULL leaves the ordinal column off, as there; 1 is
  refused by name, because Microsoft's page lists the ordinal column for Azure SQL and SQL Server but not for
  a Fabric Warehouse. A NULL separator is refused, since the page doesn't say what it does.
- ``TOP (n) PERCENT`` rounds a fractional row count up, where DuckDB's ``LIMIT n%`` rounds down. It is made
  here as a QUALIFY on the row's number (or, WITH TIES, its rank) against CEILING(count * n / 100). A percent
  outside 0 to 100 is refused with SQL Server's own words.
- ``(VALUES ...) AS v`` with no column names: a warehouse says no column name was specified, where DuckDB
  names them col0, col1. Refused the same way, so a query that runs here also runs there.
"""

from __future__ import annotations

from sqlglot import exp

_NOT_YET = "This T-SQL can't be run on the local engine: "


def rewrite(tree: exp.Expression) -> None:
    for series in list(tree.find_all(exp.GenerateSeries)):
        _series(series)
    for limit in list(tree.find_all(exp.Limit)):
        options = limit.args.get("limit_options")
        if options is not None and options.args.get("percent"):
            _percent(limit, bool(options.args.get("with_ties")))
    for values in list(tree.find_all(exp.Values)):
        _named(values)


# ---- GENERATE_SERIES

def _kind(node: exp.Expression) -> str:
    """'whole', 'decimal' or '' (not a number literal) for a literal start or stop, sign included."""
    if isinstance(node, exp.Neg):
        node = node.this
    if isinstance(node, exp.Paren):
        return _kind(node.this)
    if isinstance(node, exp.Literal) and not node.is_string:
        return "float" if any(c in node.name for c in "eE") else "decimal" if "." in node.name else "whole"
    return ""


def _is_null(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Null) or (isinstance(node, exp.Paren) and _is_null(node.this))


def _series(node: exp.GenerateSeries) -> None:
    start, stop, step = node.args.get("start"), node.args.get("end"), node.args.get("step")
    if start is None or stop is None:
        raise ValueError(_NOT_YET + "GENERATE_SERIES takes a start and a stop, and a step if you give one.")
    if any(_is_null(a) for a in (start, stop, step)):
        raise ValueError(_NOT_YET + "GENERATE_SERIES with NULL isn't mapped yet. Microsoft's page doesn't say what "
                         "a warehouse returns. Give it numbers.")
    if any(a is not None and _kind(a) == "float" for a in (start, stop, step)):
        raise ValueError(_NOT_YET + "GENERATE_SERIES takes whole numbers and decimals, not float, as in T-SQL. "
                         "Write the number without an exponent, such as 1.5.")
    kinds = {_kind(start), _kind(stop)}
    if kinds == {"whole", "decimal"}:
        raise ValueError(_NOT_YET + "GENERATE_SERIES needs start and stop of the same type, as in T-SQL. Here one "
                         "is a whole number and the other a decimal, so write both as decimals, such as 1.0 and 5.0.")
    if step is not None and _places(step) > max(_places(start), _places(stop)):
        raise ValueError(_NOT_YET + "GENERATE_SERIES with a step that has more decimal places than start and stop "
                         "isn't mapped yet. Its values are of start's type, and Microsoft's page doesn't say how a "
                         "step that doesn't fit is rounded. Give start and stop as many places, such as 1.00.")
    holder = node.parent
    if not isinstance(holder, (exp.Table, exp.Lateral)):
        raise ValueError(_NOT_YET + "GENERATE_SERIES runs here only in FROM or CROSS APPLY. "
                         "Move it there, or run it in a Fabric Warehouse.")
    s, e = start.copy(), stop.copy()
    if step is None:  # 1 counting up, -1 counting down
        st: exp.Expression = exp.Case(ifs=[exp.If(this=exp.GT(this=s.copy(), expression=e.copy()),
                                                  true=exp.Literal.number(-1))], default=exp.Literal.number(1))
    else:
        number = _literal(step)
        if number == 0:
            raise ValueError(_NOT_YET + "GENERATE_SERIES's step can't be 0, as in T-SQL.")
        st = step.copy() if number is not None else exp.Case(
            ifs=[exp.If(this=exp.EQ(this=step.copy(), expression=exp.Literal.number(0)),
                        true=exp.Anonymous(this="ERROR", expressions=[
                            exp.Literal.string("GENERATE_SERIES's step can't be 0, as in T-SQL.")]))],
            default=step.copy())
    # The steps that stay within stop: about FLOOR((stop - start) / step) + 1, none when the step points away.
    # That division is a DOUBLE here, so one more step is made and each value is compared with stop exactly
    # (0.0 to 0.3 by 0.1 divides to 2.9999999999999996, and 0.3 is still in the series).
    count = exp.Add(this=exp.Floor(this=exp.Div(this=exp.Paren(this=exp.Sub(this=e.copy(), expression=s.copy())),
                                                expression=exp.Paren(this=st.copy()))),
                    expression=exp.Literal.number(2))
    i = exp.column("kf_i", "kf_steps")
    value: exp.Expression = exp.Add(this=s.copy(), expression=exp.Mul(this=i, expression=exp.Paren(this=st.copy())))
    zero = exp.Literal.number(0)
    within = exp.or_(exp.and_(exp.GT(this=exp.Paren(this=st.copy()), expression=zero.copy()),
                              exp.LTE(this=value.copy(), expression=e.copy())),
                     exp.and_(exp.LT(this=exp.Paren(this=st), expression=zero.copy()),
                              exp.GTE(this=value.copy(), expression=e)))
    if kinds == {"whole"}:  # a whole-number literal is int in T-SQL; the arithmetic here is BIGINT
        value = exp.cast(value, "INT") if all(_fits_int(a) for a in (start, stop)) else exp.cast(value, "BIGINT")
    steps = exp.Anonymous(this="RANGE", expressions=[exp.Literal.number(0), exp.Anonymous(
        this="GREATEST", expressions=[exp.cast(count, "BIGINT"), exp.Literal.number(0)])])
    query = exp.select(exp.alias_(value, "value")).from_(
        exp.Table(this=steps, alias=exp.TableAlias(this=exp.to_identifier("kf_steps"),
                                                   columns=[exp.to_identifier("kf_i")]))).where(within)
    alias = holder.args.get("alias")
    name = alias.this.copy() if alias is not None and alias.this else exp.to_identifier("generate_series")
    sub = query.subquery(alias=name)
    if isinstance(holder, exp.Table):
        holder.replace(sub)
    else:
        node.replace(query.subquery())


def _places(node: exp.Expression) -> int:
    """A decimal literal's places after the point (0 for anything else)."""
    while isinstance(node, (exp.Neg, exp.Paren)):
        node = node.this
    if isinstance(node, exp.Literal) and not node.is_string and "." in node.name:
        return len(node.name.split(".", 1)[1])
    return 0


def _literal(node: exp.Expression) -> float | None:
    sign = 1.0
    while isinstance(node, (exp.Neg, exp.Paren)):
        if isinstance(node, exp.Neg):
            sign = -sign
        node = node.this
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return sign * float(node.name)
        except ValueError:
            return None
    return None


def _fits_int(node: exp.Expression) -> bool:
    number = _literal(node)
    return number is not None and -2**31 <= number < 2**31


# ---- TOP (n) PERCENT

def _percent(limit: exp.Limit, ties: bool) -> None:
    select = limit.parent
    if not isinstance(select, exp.Select):
        raise ValueError(_NOT_YET + "TOP ... PERCENT runs here only on a SELECT. Run it in a Fabric Warehouse.")
    if select.args.get("distinct") or select.args.get("qualify"):
        raise ValueError(_NOT_YET + "TOP ... PERCENT runs here only without DISTINCT or QUALIFY. "
                         "Run it in a Fabric Warehouse.")
    order = select.args.get("order")
    if ties and order is None:
        raise ValueError(_NOT_YET + "TOP ... WITH TIES needs an ORDER BY, as in T-SQL.")
    share = limit.expression
    number = _literal(share)
    if number is not None and not 0 <= number <= 100:
        raise ValueError("Percent values must be between 0 and 100.")
    if number is None:  # anything but a number written out (text such as '150', an expression) is checked as it runs
        value = exp.cast(share.copy(), "DOUBLE")  # as a number: text compared with 0 would compare as text
        share = exp.Case(ifs=[exp.If(this=exp.or_(exp.LT(this=value.copy(), expression=exp.Literal.number(0)),
                                                   exp.GT(this=value.copy(), expression=exp.Literal.number(100))),
                                     true=exp.Anonymous(this="ERROR", expressions=[
                                         exp.Literal.string("Percent values must be between 0 and 100.")]))],
                         default=share.copy())
    over = exp.Window(this=exp.Anonymous(this="RANK" if ties else "ROW_NUMBER", expressions=[]),
                      order=order.copy() if order is not None else None)
    rows = exp.Window(this=exp.Count(this=exp.Star()))
    keep = exp.Anonymous(this="CEIL", expressions=[exp.Div(
        this=exp.Mul(this=rows, expression=exp.cast(share.copy(), "DOUBLE")), expression=exp.Literal.number(100))])
    select.set("limit", None)
    select.set("qualify", exp.Qualify(this=exp.LTE(this=over, expression=keep)))


# ---- (VALUES ...) AS v(a, b)

def _named(values: exp.Values) -> None:
    if not isinstance(values.parent, (exp.From, exp.Join)):
        return  # INSERT ... VALUES and the like name their columns elsewhere
    alias = values.args.get("alias")
    width = len(values.expressions[0].expressions) if values.expressions and isinstance(
        values.expressions[0], exp.Tuple) else 1
    if alias is None or not alias.this:
        raise ValueError(_NOT_YET + "a VALUES list in FROM needs a name and a name for each column, as in T-SQL, "
                         "such as (VALUES (1, 'a')) AS v (n, c).")
    names = alias.args.get("columns") or []
    if len(names) < width:
        raise ValueError(f"No column name was specified for column {len(names) + 1} of '{alias.this.name}'. "
                         f"Name each of its {width} columns, as in (VALUES ...) AS {alias.this.name} (c1, c2).")
    if len(names) > width:
        raise ValueError(f"'{alias.this.name}' has fewer columns than were specified in the column list.")
