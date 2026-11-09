"""Databricks SQL's own built-in functions (not in Spark 3.5) on this PC.

engines.local_sql calls ``rewrite`` for a Databricks SQL cell, after sql.spark_fixes. Each function
Databricks has that DuckDB names or answers differently is written as DuckDB SQL that gives Databricks'
documented value, and each one this PC can't answer the same way stops the cell by name, with what to do
instead, so nothing runs with another answer. databricks_coverage lists each one's status, with a value
check from Databricks' reference where the function has one.
"""

from __future__ import annotations

import functools
import json
import math
import re
from pathlib import Path

from sqlglot import exp, parse_one

_WAREHOUSE = "Run the cell on a Databricks SQL warehouse"

# DataSketches sketches (KLL quantiles, Theta and Tuple distinct counts) and approx_top_k's state are bytes
# in Databricks' own format, which this PC can't build or read the same way.
_SKETCHES = re.compile(r"^(kll_sketch_|kll_merge_agg_|theta_|tuple_|approx_top_k_(accumulate|combine|estimate)$)",
                       re.IGNORECASE)


@functools.cache
def _sketch_names() -> frozenset[str]:
    """The sketch functions databricks_builtins.json lists, so a function of a person's own that only
    starts the same way (tuple_merge) isn't refused."""
    listed = json.loads(Path(__file__).with_name("databricks_builtins.json").read_text(encoding="utf-8"))
    return frozenset(f["name"] for f in listed if _SKETCHES.match(f["name"]))


# sqlglot reads these by name into their own nodes
_TYPED_SKETCHES: dict[type[exp.Expression], str] = {
    exp.ApproxTopKAccumulate: "approx_top_k_accumulate", exp.ApproxTopKCombine: "approx_top_k_combine",
    exp.ApproxTopKEstimate: "approx_top_k_estimate"}


def _refusal(name: str, why: str, instead: str = "") -> ValueError:
    return ValueError(f"{name}() can't run on this PC the way it runs in Databricks, so nothing ran: {why} "
                      f"{_WAREHOUSE}{', or ' + instead if instead else ''}.")


def _template(sql: str, **args: exp.Expression) -> exp.Expression:
    """DuckDB SQL with each ``_name`` column replaced by that argument, in brackets."""
    def put(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table and node.name.startswith("_") and node.name[1:] in args:
            return exp.paren(args[node.name[1:]].copy(), copy=False)
        return node
    return parse_one(sql, read="duckdb").transform(put)


# A vector is ARRAY<FLOAT> in Databricks; it is worked out here in DOUBLE, which gives the values its
# reference shows (vector_cosine_similarity's 0.9746318461970762). A NULL vector, or one with a NULL
# element, gives NULL as Databricks documents. DuckDB's list functions stop on a NULL element even in a
# CASE branch not taken, so they get the vector with NULLs as 0 and the CASE gives the NULL.
def _clean(value: exp.Expression) -> exp.Expression:
    return _template("list_transform(_v, __kf_z -> coalesce(__kf_z, 0))", v=exp.cast(value, "DOUBLE[]"))


def _whole(sql: str, *vectors: exp.Expression) -> exp.Expression:
    """``sql`` over the cleaned vectors ``_a`` (and ``_b``), NULL when any vector is NULL or has a NULL."""
    names = "ab"[:len(vectors)]
    whole = " AND ".join(f"list_count(_{n}_) = len(_{n}_)" for n in names)
    args = {n: _clean(v) for n, v in zip(names, vectors, strict=True)}
    args |= {f"{n}_": exp.cast(v, "DOUBLE[]") for n, v in zip(names, vectors, strict=True)}
    return _template(f"CASE WHEN {whole} THEN {sql} END", **args)


def _norm_sql(degree: float) -> str:
    if degree == 1:
        return "list_sum(list_transform(_a, __kf_x -> abs(__kf_x)))"
    if degree == 2:
        return "sqrt(list_dot_product(_a, _a))"
    return "list_max(list_transform(_a, __kf_x -> abs(__kf_x)))"


def _degree(name: str, args: list[exp.Expression]) -> float:
    """vector_norm's or vector_normalize's degree: 2 when left out, else a constant 1, 2 or infinity."""
    if len(args) < 2:
        return 2.0
    value, sign = args[1], 1.0
    while isinstance(value, (exp.Cast, exp.Neg, exp.Paren)):  # -1.0, 2.0f, -float('inf')
        sign, value = (-sign if isinstance(value, exp.Neg) else sign), value.this
    if isinstance(value, exp.Literal):
        try:  # a string degree is cast to FLOAT, as Databricks casts it ('2.0', 'inf')
            degree = sign * float(value.name.strip())
        except ValueError:
            degree = math.nan
        if degree in (1.0, 2.0, math.inf):
            return degree
        raise ValueError(f"{name}() takes degree 1.0, 2.0 or float('inf'), as in Databricks "
                         f"(INVALID_VECTOR_NORM_DEGREE), not {args[1].sql(dialect='databricks')}.")
    raise _refusal(name, "its degree isn't a constant here, and this PC works out the norm from it.",
                   "write the degree as 1.0, 2.0 or float('inf')")


def _vector_norm(name: str, args: list[exp.Expression]) -> exp.Expression:
    degree = _degree(name, args)
    norm = _norm_sql(degree)
    sql = (f"CASE WHEN len(_a) = 0 THEN 0.0 ELSE {norm} END" if name == "vector_norm" else
           f"CASE WHEN len(_a) = 0 THEN CAST([] AS DOUBLE[]) WHEN {norm} = 0 THEN NULL "
           # the lambda's body names the vector, so its argument has a name no table is likely to have
           f"ELSE list_transform(_a, __kf_y -> __kf_y / ({norm})) END")
    return _whole(sql, args[0])


_PAIRS = {
    "vector_inner_product": "list_dot_product(_a, _b)",
    "vector_l2_distance": "list_distance(_a, _b)",
    "vector_cosine_similarity": "CASE WHEN list_dot_product(_a, _a) = 0 OR list_dot_product(_b, _b) = 0 THEN NULL "
                                "ELSE list_cosine_similarity(_a, _b) END",
}

# Databricks' own scalar functions that are DuckDB SQL here, each as Databricks documents it:
# zeroifnull is coalesce(expr, 0), nullifzero is if(expr = 0, null, expr), and try_mod is mod with
# NULL for a zero divisor (DuckDB's % keeps the dividend's sign and a DECIMAL type, as Databricks does).
_SCALARS = {
    "zeroifnull": ("COALESCE(_a, 0)", 1),
    "nullifzero": ("NULLIF(_a, 0)", 1),
    "try_mod": ("_a % NULLIF(_b, 0)", 2),
}

# Databricks' TIME (microseconds since midnight), as DuckDB's TIME. A time from a count outside the day
# stops, as Databricks documents its ranges; DuckDB would wrap it round midnight.
_TIME_UNITS = {"MICROSECOND": 1, "MILLISECOND": 1_000, "SECOND": 1_000_000, "MINUTE": 60_000_000,
               "HOUR": 3_600_000_000}
_DAY_MICROS = 86_400_000_000
_TIME_FROM = {"time_from_seconds": 1_000_000, "time_from_millis": 1_000, "time_from_micros": 1}
_TIME_TO = {
    "time_to_seconds": "CAST(epoch_us(CAST(_a AS TIME)) / 1000000 AS DECIMAL(14, 6))",
    "time_to_millis": "epoch_us(CAST(_a AS TIME)) // 1000",
    "time_to_micros": "epoch_us(CAST(_a AS TIME))",
}


def _time_from(name: str, value: exp.Expression) -> exp.Expression:
    scale = _TIME_FROM[name]
    micros = "CAST(trunc(_a * 1000000) AS BIGINT)" if scale == 1_000_000 else f"CAST(_a AS BIGINT) * {scale}"
    limit = _DAY_MICROS // scale
    return _template(f"CASE WHEN _a IS NULL THEN NULL WHEN _a >= 0 AND _a < {limit} "
                     f"THEN TIME '00:00:00' + to_microseconds({micros}) "
                     f"ELSE error('{name}() takes a count from 0 to under {limit} since midnight, as in Databricks') END",
                     a=value)


def _time_unit(name: str, unit: exp.Expression) -> int:
    word = unit.name.upper()
    if word not in _TIME_UNITS:
        raise ValueError(f"{name}() takes the unit HOUR, MINUTE, SECOND, MILLISECOND or MICROSECOND, as in "
                         f"Databricks, not {unit.sql(dialect='databricks')}.")
    return _TIME_UNITS[word]


def _time_diff(node: exp.TimeDiff) -> exp.Expression:
    """time_diff(unit, start, end): whole units from start to end, rounded toward zero (sqlglot reads the
    three arguments in order into this, expression and unit)."""
    n = _time_unit("time_diff", node.this)
    return _template(f"(epoch_us(CAST(_e AS TIME)) - epoch_us(CAST(_s AS TIME))) // {n}",
                     s=node.expression, e=node.args["unit"])


def _time_trunc(node: exp.TimeTrunc) -> exp.Expression:
    """time_trunc(unit, time), read by sqlglot into this and unit in that order."""
    if not (isinstance(node.this, exp.Literal) and node.this.is_string):
        raise _refusal("time_trunc", "its unit isn't a constant string here.", "write the unit as 'HOUR' or 'MINUTE'")
    n = _time_unit("time_trunc", node.this)
    return _template(f"TIME '00:00:00' + to_microseconds(epoch_us(CAST(_a AS TIME)) // {n} * {n})",
                     a=node.args["unit"])


def _parsed(name: str, args: list[exp.Expression], kind: str, safe: bool) -> exp.Expression:
    """to_time, try_to_time and try_to_date: a cast without a format, else strptime with the Spark
    pattern written for DuckDB, as sqlglot writes to_timestamp's; the try_ ones give NULL for text
    that doesn't parse."""
    _arity(name, args, 1, 2)
    if len(args) == 1:
        return (exp.TryCast if safe else exp.Cast)(this=args[0].copy(), to=exp.DataType.build(kind))
    if not (isinstance(args[1], exp.Literal) and args[1].is_string):
        raise _refusal(name, "its format isn't a constant string here.", "write the format as a string")
    parsed = parse_one(f"to_timestamp(x, {args[1].sql(dialect='databricks')})", read="databricks")
    parsed.set("this", args[0].copy())
    if safe:
        parsed.set("safe", True)
    return exp.cast(parsed, kind)


def _try_make_timestamp(name: str, args: list[exp.Expression]) -> exp.Expression:
    """try_make_timestamp_ntz and _ltz: NULL for a field out of range or a day the month hasn't, from
    six fields (a second of 60 is the next minute, as in Databricks and DuckDB) or a date and a time;
    _ltz reads them in its timezone argument when given, else in the session's (UTC here)."""
    ltz = name.endswith("_ltz")
    if len(args) in (2, 3) and (ltz or len(args) == 2):
        made = _template("TRY(CAST(_d AS DATE) + CAST(_t AS TIME))", d=args[0], t=args[1])
    elif len(args) in (6, 7) and (ltz or len(args) == 6):
        made = _template("CASE WHEN _y BETWEEN 1 AND 9999 THEN TRY(make_timestamp(_y, _mo, _d, _h, _mi, _s)) END",
                         **dict(zip(("y", "mo", "d", "h", "mi", "s"), args, strict=False)))
    else:
        raise ValueError(f"{name}() takes year, month, day, hour, minute and second, or a date and a time"
                         f"{', and a timezone' if ltz else ''}, as in Databricks; not {len(args)} arguments.")
    if not ltz:
        return made
    zone = args[-1] if len(args) in (3, 7) else None
    return _template("timezone(_z, _m)", z=zone, m=made) if zone is not None else exp.cast(made, "TIMESTAMPTZ")


_TRY_MAKE = ("try_make_timestamp_ntz", "try_make_timestamp_ltz")

# A pattern list (#extended, #iso8601, #rfc3339, #unix) and several patterns tried in turn
_PATTERN_LISTS = ("parse_timestamp", "try_parse_timestamp")

_PARSED = {"to_time": ("TIME", False), "try_to_time": ("TIME", True), "try_to_date": ("DATE", True)}

# Element-wise aggregates over vectors: DuckDB has no aggregate that sums lists element by element.
_VECTOR_AGGREGATES = {"vector_sum", "vector_avg"}


def _arity(name: str, args: list[exp.Expression], low: int, high: int) -> None:
    if not low <= len(args) <= high:
        want = str(low) if low == high else f"{low} or {high}"
        raise ValueError(f"{name}() takes {want} argument{'s' if high > 1 else ''} in Databricks, not {len(args)}.")


def _function(node: exp.Anonymous) -> exp.Expression:
    name, args = node.name.lower(), list(node.expressions)
    if name in _SCALARS:
        sql, count = _SCALARS[name]
        _arity(name, args, count, count)
        return exp.paren(_template(sql, **dict(zip("ab", args, strict=False))))
    if name in _PAIRS:
        _arity(name, args, 2, 2)
        return _whole(_PAIRS[name], args[0], args[1])
    if name in ("vector_norm", "vector_normalize"):
        _arity(name, args, 1, 2)
        return _vector_norm(name, args)
    if name in _TIME_FROM:
        _arity(name, args, 1, 1)
        return _time_from(name, args[0])
    if name in _TIME_TO:
        _arity(name, args, 1, 1)
        return exp.paren(_template(_TIME_TO[name], a=args[0]))
    if name in _PARSED:
        return _parsed(name, args, *_PARSED[name])
    if name in _TRY_MAKE:
        return _try_make_timestamp(name, args)
    if name in _PATTERN_LISTS:
        raise _refusal(name, "it tries Databricks' own lists of timestamp patterns (#extended, #iso8601, "
                             "#unix), which this PC doesn't have.",
                       f"use {'try_' if name.startswith('try_') else ''}to_timestamp with a format")
    if name == "timediff":  # a synonym of timestampdiff (sql.diff_spellings), so here only with no unit
        raise ValueError("timediff() takes a unit first, as in timediff(HOUR, start, end).")
    if name in _VECTOR_AGGREGATES:
        raise _refusal(name, "DuckDB has no aggregate that adds vectors element by element.",
                       "posexplode the vectors and GROUP BY the position")
    return node


def rewrite(statement: exp.Expression) -> exp.Expression:
    """``statement`` with Databricks' own functions written for DuckDB, or ValueError for one this PC refuses."""
    for node in statement.find_all(exp.Anonymous, *_TYPED_SKETCHES):
        name = node.name.lower() if isinstance(node, exp.Anonymous) else _TYPED_SKETCHES[type(node)]
        if name in _sketch_names():
            instead = ("use approx_count_distinct" if name.startswith(("theta_", "tuple_"))
                       else "use percentile_approx" if name.startswith("kll_") else "")
            raise _refusal(name, "it builds or reads a DataSketches sketch, whose bytes this PC can't make the "
                                 "same way.", instead)

    # innermost first, so a call inside another's arguments is written before the outer one copies them
    for call in reversed(list(statement.find_all(exp.Anonymous, exp.TimeDiff, exp.TimeTrunc, bfs=False))):
        written = _written(call)
        if written is not None:
            if call.parent is None:  # the statement itself
                return written
            call.replace(written)
    return statement


def _written(call: exp.Expr) -> exp.Expression | None:
    """``call`` written for DuckDB, or None when it stays as it is."""
    if isinstance(call, exp.TimeDiff):
        return _time_diff(call)
    if isinstance(call, exp.TimeTrunc):
        return _time_trunc(call)
    if isinstance(call, exp.Anonymous):
        written = _function(call)
        return None if written is call else written
    return None
