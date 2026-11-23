"""Spark's window functions on this PC (WR-111), where DuckDB's own answers differ.

- row_number, rank, dense_rank and ntile give an int, as Spark does (DuckDB gives a bigint).
- A ranking function (those four, percent_rank and cume_dist) and lag and lead need an ORDER BY in
  their window. Spark stops without one; DuckDB numbers the rows in whatever order it reads them, so
  the statement is refused here too.
- lag and lead take no frame, and a ranking function takes only its own one (ROWS BETWEEN UNBOUNDED
  PRECEDING AND CURRENT ROW; RANGE for cume_dist). Spark stops on any other; DuckDB ignores it.
- A RANGE frame between two INTERVALs of different units (INTERVAL 1 DAY PRECEDING AND INTERVAL 2 HOUR
  FOLLOWING) is refused, as Spark types them apart and stops; DuckDB ran it.
- A RANGE frame given as an INTERVAL in days or less (RANGE BETWEEN INTERVAL 1 DAY PRECEDING AND CURRENT
  ROW) needs a timestamp to order by. Spark stops on a date ([DATATYPE_MISMATCH.RANGE_FRAME_INVALID_TYPE]), where
  DuckDB counted the days, so a date is refused here too, once the query reads a row (DuckDB knows the
  order's type only when it binds the query).

The rest already answers as Spark does: NULLs first in an ascending window order, ties in rank,
dense_rank and percent_rank, lag and lead's defaults, the default frames (RANGE up to the current
row with an ORDER BY, the whole partition without one) and ntile's uneven groups. A window named in
a WINDOW clause isn't checked here. Expected values are Spark 3.5's (CheckAnalysis, ResolveWindowFrame
and ResolveWindowOrder); none of it was run on a Fabric cluster: UNVERIFIED live.
"""
from __future__ import annotations

from typing import cast

import sqlglot
from sqlglot import exp

from . import spark_sql_names

# Spark's IntegerType ranking functions (RowNumberLike, RankLike, NTile).
_INTS = (exp.RowNumber, exp.Rank, exp.DenseRank, exp.Ntile)
_RANKING = (*_INTS, exp.PercentRank, exp.CumeDist)
_SHIFTS = (exp.Lag, exp.Lead)
_NAMES = {exp.RowNumber: "row_number", exp.Rank: "rank", exp.DenseRank: "dense_rank", exp.Ntile: "ntile",
          exp.PercentRank: "percent_rank", exp.CumeDist: "cume_dist", exp.Lag: "lag", exp.Lead: "lead"}

UNORDERED = ("{name} needs an ORDER BY in its window, so nothing ran: Spark stops on it too (\"Window function "
             "... requires window to be ordered\"). Add an ORDER BY to its OVER clause.")
FRAMED_SHIFT = ("{name} can't be given a window frame, so nothing ran: Spark stops on it too (\"Cannot specify "
                "window frame for {name} function\"). Remove the ROWS or RANGE clause from its OVER clause.")
FRAMED_RANK = ("{name} works over {frame} only, so nothing ran: Spark stops on any other frame too (\"Window "
               "Frame ... must match the required frame\"). Remove the ROWS or RANGE clause from its OVER clause.")


def required_frame(name: str) -> str:
    """The one frame Spark lets the ranking function ``name`` have."""
    return f"{'RANGE' if name == 'cume_dist' else 'ROWS'} BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"


def refusal(name: str, ordered: bool, frame: str | None) -> str | None:
    """Why Spark stops on the window function ``name`` (lowercase) with or without an ORDER BY and with
    ``frame`` (its text, upper case, or None for none), or None when it runs."""
    if name not in _NAMES.values():
        return None
    if not ordered:
        return UNORDERED.format(name=name)
    if frame is None:
        return None
    if name in ("lag", "lead"):
        return FRAMED_SHIFT.format(name=name)
    if frame != required_frame(name):
        return FRAMED_RANK.format(name=name, frame=required_frame(name))
    return None


def _frame(spec: exp.Expression | None) -> str | None:
    if spec is None:
        return None
    parts = []
    for which in ("start", "end"):
        value, side = spec.args.get(which), spec.args.get(f"{which}_side")
        text = str(value.name if isinstance(value, exp.Expression) else value or "").upper()
        parts.append("CURRENT ROW" if text == "CURRENT ROW" else f"{text} {str(side or '').upper()}".strip())
    return f"{str(spec.args.get('kind') or 'ROWS').upper()} BETWEEN {parts[0]} AND {parts[1]}"


_MINE = "kf_spark_window"


FRAME_UNITS = ("A RANGE frame's two bounds, {start} and {end}, are intervals of two types, so nothing ran. Spark "
               "stops on it too ([DATATYPE_MISMATCH.SPECIFIED_WINDOW_FRAME_DIFF_TYPES]). Give both bounds in one "
               "unit, such as INTERVAL 26 HOUR PRECEDING AND INTERVAL 2 HOUR FOLLOWING.")


def _interval_unit(bound: object) -> str | None:
    """The unit Spark types an INTERVAL bound by (WEEK is 7 DAY), or None for a bound that isn't one."""
    if not isinstance(bound, exp.Interval) or bound.unit is None:
        return None
    unit = bound.unit.sql("spark").upper()
    return "DAY" if unit == "WEEK" else unit


def _check_frame_units(window: exp.Expression) -> None:
    """Spark types INTERVAL 1 DAY and INTERVAL 2 HOUR apart, and stops on a frame between them; DuckDB ran it."""
    spec = window.args.get("spec")
    if not isinstance(spec, exp.WindowSpec):
        return
    start, end = spec.args.get("start"), spec.args.get("end")
    units = (_interval_unit(start), _interval_unit(end))
    if None not in units and units[0] != units[1]:
        raise ValueError(FRAME_UNITS.format(start=cast(exp.Expression, start).sql("spark"),
                                            end=cast(exp.Expression, end).sql("spark")))


def check_windows(statement: exp.Expression) -> exp.Expression:
    """Refuse (ValueError) a window Spark stops on, and mark the query's own windows, before other
    rewrites write theirs (monotonically_increasing_id's row_number() OVER ())."""
    for window in statement.find_all(exp.Window):
        _check_frame_units(window)
        func = window.this
        inner = func.this if isinstance(func, (exp.IgnoreNulls, exp.RespectNulls)) else func
        if not isinstance(inner, (*_RANKING, *_SHIFTS)) or window.meta.get("kf_own_window"):
            continue
        window.meta[_MINE] = True
        if window.args.get("alias") is None:  # OVER w: the named window holds the order and frame
            order = window.args.get("order")
            why = refusal(_NAMES[type(inner)], order is not None and bool(order.expressions),
                          _frame(window.args.get("spec")))
            if why:
                raise ValueError(why)
    return statement


RANGE_DATE = ("A RANGE frame given as an INTERVAL in days, hours, minutes or seconds can't order by a date, so "
              "nothing ran. Spark stops on it too ([DATATYPE_MISMATCH.RANGE_FRAME_INVALID_TYPE]). Order by a timestamp, such as CAST(d AS TIMESTAMP), "
              "or give the frame in days, such as RANGE BETWEEN 1 PRECEDING AND CURRENT ROW.")


_DAY_TIME = ("WEEK", "DAY", "HOUR", "MINUTE", "SECOND", "MILLI", "MICRO")


def _interval_range(window: exp.Expression) -> bool:
    """Whether ``window`` is a RANGE frame with a bound in days or less (INTERVAL 1 DAY, INTERVAL '2' HOUR);
    Spark takes one in years or months (INTERVAL 1 MONTH) with a date."""
    spec = window.args.get("spec")
    if not isinstance(spec, exp.WindowSpec) or str(spec.args.get("kind") or "").upper() != "RANGE":
        return False
    bounds = [spec.args.get(which) for which in ("start", "end")]
    return any(isinstance(b, exp.Interval) and any(u in b.sql("spark").upper() for u in _DAY_TIME) for b in bounds)


def _no_date_order(window: exp.Expression) -> None:
    """An INTERVAL RANGE window, refused (RANGE_DATE) when its order is a date. The check reads only the
    order's type, which DuckDB settles when it binds the query; it sits outside the window, since DuckDB 2.0
    takes nothing volatile, such as ERROR, in a window's ORDER BY."""
    order = window.args.get("order")
    keys = [o.this for o in order.expressions] if isinstance(order, exp.Order) else []
    if not keys:
        return
    check = sqlglot.parse_one("CASE WHEN TYPEOF(fj_key) = 'DATE' THEN ERROR(fj_why) ELSE fj_window END", read="duckdb")
    next(c for c in check.find_all(exp.Column) if c.name == "fj_why").replace(exp.Literal.string(RANGE_DATE))
    next(c for c in check.find_all(exp.Column) if c.name == "fj_key").replace(keys[0].copy())
    check.meta.update(spark_sql_names.kept(window))
    window.replace(check)
    next(c for c in check.find_all(exp.Column) if c.name == "fj_window").replace(window)


def spark_window_results(statement: exp.Expression) -> exp.Expression:
    """``statement`` with the query's own row_number, rank, dense_rank and ntile windows giving an int, and
    a date refused as the order of an INTERVAL RANGE frame."""
    for window in list(statement.find_all(exp.Window)):
        if not window.meta.pop(_MINE, False):
            continue
        func = window.this
        inner = func.this if isinstance(func, (exp.IgnoreNulls, exp.RespectNulls)) else func
        if isinstance(inner, _INTS) and window is not statement and not (
                isinstance(window.parent, exp.Cast) and window.parent.to.is_type(exp.DataType.Type.INT)):
            new = exp.Cast(to=exp.DataType.build("INT"))
            new.meta.update(spark_sql_names.kept(window))
            window.replace(new)
            new.set("this", window)
    for window in list(statement.find_all(exp.Window)):
        if _interval_range(window) and window is not statement:
            _no_date_order(window)
    return statement
