"""Spark's window functions on this PC (WR-111), where DuckDB's own answers differ.

- row_number, rank, dense_rank and ntile give an int, as Spark does (DuckDB gives a bigint).
- A ranking function (those four, percent_rank and cume_dist) and lag and lead need an ORDER BY in
  their window. Spark stops without one; DuckDB numbers the rows in whatever order it reads them, so
  the statement is refused here too.
- lag and lead take no frame, and a ranking function takes only its own one (ROWS BETWEEN UNBOUNDED
  PRECEDING AND CURRENT ROW; RANGE for cume_dist). Spark stops on any other; DuckDB ignores it.

The rest already answers as Spark does: NULLs first in an ascending window order, ties in rank,
dense_rank and percent_rank, lag and lead's defaults, the default frames (RANGE up to the current
row with an ORDER BY, the whole partition without one) and ntile's uneven groups. A window named in
a WINDOW clause isn't checked here. Expected values are Spark 3.5's (CheckAnalysis, ResolveWindowFrame
and ResolveWindowOrder); none of it was run on a Fabric cluster: UNVERIFIED live.
"""
from __future__ import annotations

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


def check_windows(statement: exp.Expression) -> exp.Expression:
    """Refuse (ValueError) a window Spark stops on, and mark the query's own windows, before other
    rewrites write theirs (monotonically_increasing_id's row_number() OVER ())."""
    for window in statement.find_all(exp.Window):
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


def spark_window_results(statement: exp.Expression) -> exp.Expression:
    """``statement`` with the query's own row_number, rank, dense_rank and ntile windows giving an int."""
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
    return statement
