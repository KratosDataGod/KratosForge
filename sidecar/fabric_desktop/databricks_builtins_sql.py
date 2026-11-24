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
import uuid
from pathlib import Path
from typing import Any, cast

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

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


def _current_time(node: exp.CurrentTime | exp.Localtime) -> exp.Expression:
    """current_time([precision]) and its synonym localtime: a TIME, the session zone's time of day when the
    query started, cut to ``precision`` (0 to 6, a constant; 6 when left out) digits after the second, as
    Databricks documents (current_time(0) is 16:57:04 where current_time() is 16:57:04.304361)."""
    name = "localtime" if isinstance(node, exp.Localtime) else "current_time"
    given = node.this
    digits = _int_literal(given) if given is not None else 6
    if digits is None or not 0 <= digits <= 6:
        raise ValueError(f"{name}() takes a precision from 0 to 6, an INTEGER literal, as in Databricks"
                         f"{'' if given is None else ', not ' + given.sql(dialect='databricks')}.")
    step = 10 ** (6 - digits)
    return _template(f"TIME '00:00:00' + to_microseconds(epoch_us(CAST(CAST(current_timestamp AS TIMESTAMP) AS TIME)) "
                     f"// {step} * {step})")


_MAX_DIGITS = 40  # longer than any Spark number; int() of thousands of digits would stop with Python's own error


def _int_literal(node: exp.Expression) -> int | None:
    """A whole number written in the query (10, -3), or None for anything else."""
    inner = node.this if isinstance(node, exp.Neg) else node
    if isinstance(inner, exp.Literal) and inner.is_int and len(inner.name) <= _MAX_DIGITS:
        return -int(inner.name) if isinstance(node, exp.Neg) else int(inner.name)
    return None


# uniform(min, max [, seed]) and randstr(length [, seed]), as Spark's (randomExpressions.scala): uniform is
# min + (max - min) * rand(), cast to the bounds' type (a whole number is cut toward zero, so uniform(10, 20)
# gives 10 to 19), and randstr draws each character from 0-9, a-z and A-Z. Without a seed each row gets its own
# draw. With a seed Databricks gives its own series, which depends on its random generator and on how it splits
# the rows, so a seed is refused.
_RANDOM_SEED = ("with a seed Databricks gives its own series of values, from its own random generator and the "
                "way it splits the rows, which this PC can't make.")
_INTEGRAL = (exp.DataType.Type.TINYINT, exp.DataType.Type.SMALLINT, exp.DataType.Type.INT, exp.DataType.Type.BIGINT)


def _bound_type(value: exp.Expression) -> exp.DataType | None:
    """The Databricks type of a bound of uniform written as a number or a cast of one: INT or BIGINT for a
    whole number, DECIMAL(p, s) for one with a point, DOUBLE with an exponent; None for anything else."""
    inner = value.this if isinstance(value, (exp.Neg, exp.Paren)) else value
    if isinstance(inner, exp.Cast) and _bound_type(inner.this) is not None:
        kind = inner.to
        return kind if kind.this in (*_INTEGRAL, exp.DataType.Type.DOUBLE, exp.DataType.Type.FLOAT,
                                     exp.DataType.Type.DECIMAL) else None
    if not (isinstance(inner, exp.Literal) and not inner.is_string) or len(inner.name) > _MAX_DIGITS:
        return None
    text = inner.name.lower()
    if "e" in text:
        return exp.DataType.build("DOUBLE")
    if "." not in text:
        return exp.DataType.build("INT" if int(text) < 2 ** 31 else "BIGINT")
    whole, _, fraction = text.partition(".")
    digits = len(whole.lstrip("0")) + len(fraction)
    return exp.DataType.build(f"DECIMAL({max(digits, len(fraction), 1)}, {len(fraction)})")


def _decimal_sizes(kind: exp.DataType) -> tuple[int, int]:
    sizes = [int(e.name) for e in kind.expressions if isinstance(e, exp.DataTypeParam)] or [10, 0]
    return sizes[0], sizes[1] if len(sizes) > 1 else 0


def _uniform_type(low: exp.DataType, high: exp.DataType) -> exp.DataType:
    """Spark's Uniform.dataType for its bounds' types."""
    order = list(_INTEGRAL)
    if low.this in order and high.this in order:
        return high if order.index(high.this) >= order.index(low.this) else low
    for wide in (exp.DataType.Type.DOUBLE, exp.DataType.Type.FLOAT):
        if wide in (low.this, high.this):
            return exp.DataType.build(wide.value)
    if low.this == exp.DataType.Type.DECIMAL and high.this == exp.DataType.Type.DECIMAL:
        (lp, ls), (hp, hs) = _decimal_sizes(low), _decimal_sizes(high)
        return low if lp - ls >= hp - hs and ls >= hs else high
    return low if low.this == exp.DataType.Type.DECIMAL else high


def _uniform(node: exp.Uniform) -> exp.Expression:
    if node.args.get("seed") is not None or node.args.get("gen") is not None:
        raise _refusal("uniform", _RANDOM_SEED, "leave out the seed")
    low, high = node.this, node.expression
    kinds = [_bound_type(low), _bound_type(high)]
    if kinds[0] is None or kinds[1] is None:
        raise _refusal("uniform", "its bounds aren't numbers written in the query here, and this PC works out the "
                                  "result's type from them.", "write the bounds as numbers")
    kind = _uniform_type(kinds[0], kinds[1])
    drawn = "CAST(_a AS DOUBLE) + (CAST(_b AS DOUBLE) - CAST(_a AS DOUBLE)) * random()"
    if kind.this in _INTEGRAL:
        drawn = f"trunc({drawn})"
    return exp.cast(_template(f"({drawn})", a=low, b=high), kind)


_RANDSTR_MAX = 1_000_000


def _randstr(node: exp.Randstr) -> exp.Expression:
    if node.args.get("generator") is not None:
        raise _refusal("randstr", _RANDOM_SEED, "leave out the seed")
    length = _int_literal(node.this)
    if length is None or length < 0:
        raise _refusal("randstr", "its length isn't a whole number from 0 up written in the query here.",
                       "write the length as a number")
    if length > _RANDSTR_MAX:
        raise _refusal("randstr", f"this PC builds strings of up to {_RANDSTR_MAX:,} characters on each row.",
                       "ask for a shorter string")
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return _template(f"array_to_string(list_transform(range({length}), __kf_i -> "
                     f"substr('{alphabet}', 1 + CAST(floor(random() * 62) AS INTEGER), 1)), '')")


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
    ltz = not name.endswith("_ntz")  # try_make_timestamp is a session-zone TIMESTAMP, as _ltz
    if len(args) == 1 and name == "try_make_timestamp":
        made = _template("TRY(CAST(_d AS DATE) + TIME '00:00:00')", d=args[0])
    elif len(args) in (2, 3) and (ltz or len(args) == 2):
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


_TRY_MAKE = ("try_make_timestamp", "try_make_timestamp_ntz", "try_make_timestamp_ltz")

# try_make_interval: each part as DuckDB's interval of that unit, so one past its range (months in an
# INT, as Databricks' are) fails and TRY gives NULL
_INTERVAL_PARTS = ("to_years(CAST(_a AS INTEGER))", "to_months(CAST(_b AS INTEGER))", "to_days(CAST(_c * 7 AS INTEGER))",
                   "to_days(CAST(_d AS INTEGER))", "to_hours(CAST(_e AS BIGINT))", "to_minutes(CAST(_f AS BIGINT))",
                   "to_microseconds(CAST(round(_g * 1000000) AS BIGINT))")


def _try_make_interval(args: list[exp.Expression]) -> exp.Expression:
    _arity("try_make_interval", args, 1, 7)
    names = "abcdefg"[:len(args)]
    parts = " + ".join(_INTERVAL_PARTS[:len(args)])
    return _template(f"TRY({parts})", **dict(zip(names, args, strict=True)))


# Nanosecond timestamps are DuckDB's TIMESTAMP_NS, which holds the nanoseconds since 1970 in a BIGINT
_NANOS_PAST = "past the nanoseconds a TIMESTAMP_NS holds here (about 1677 to 2262)"


def _timestamp_nanos(args: list[exp.Expression]) -> exp.Expression:
    _arity("timestamp_nanos", args, 1, 1)
    return exp.paren(_template(
        "CASE WHEN floor(TRY_CAST(_a AS DECIMAL(38, 9))) BETWEEN -9223372036854775808 AND 9223372036854775807 "
        "THEN make_timestamp_ns(CAST(floor(TRY_CAST(_a AS DECIMAL(38, 9))) AS BIGINT)) WHEN _a IS NOT NULL THEN error('timestamp_nanos() can''t "
        f"run on this PC the way it runs in Databricks, so nothing ran: the count is {_NANOS_PAST}.') END",
        a=args[0]))


def _unix_nanos(args: list[exp.Expression]) -> exp.Expression:
    """unix_nanos; a TIMESTAMP literal is read to the nanosecond, as Databricks reads it for TIMESTAMP(9)."""
    _arity("unix_nanos", args, 1, 1)
    value = args[0]
    if isinstance(value, exp.Null):
        return exp.cast(exp.null(), "DECIMAL(21, 0)")
    if isinstance(value, exp.Cast) and isinstance(value.this, exp.Literal) and value.this.is_string and \
            value.to.this in (exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ, exp.DataType.Type.TIMESTAMPNTZ):
        value = _template("TRY_CAST(_a AS TIMESTAMP_NS)", a=value.this)
    return exp.paren(_template(
        "CASE WHEN _a IS NULL AND _r IS NULL THEN NULL ELSE coalesce(CAST(TRY(epoch_ns(_a)) AS DECIMAL(21, 0)), "
        f"error('unix_nanos() can''t run on this PC the way it runs in Databricks, so nothing ran: the timestamp "
        f"is {_NANOS_PAST}.')) END", a=value, r=args[0]))


# bitmap_and_agg ANDs the first 4096 bytes of each bitmap, as bitmap_or_agg ORs them (spark_builtins_sql)
_BITMAP_AND_AGG = ("COALESCE(CAST(BIT_AND(CAST(LEFT(RPAD(CAST(CAST(_a || UNHEX('00') AS BIT) AS VARCHAR), "
                   "32768, '0'), 32768) AS BIT)) AS BLOB), error('bitmap_and_agg() can''t run on this PC the way "
                   "it runs in Databricks, so nothing ran: its value for a group with no bitmap isn''t documented.'))")


def _bitmap_and_agg(node: exp.Anonymous) -> exp.Expression:
    args = list(node.expressions)
    _arity("bitmap_and_agg", args, 1, 1)
    if isinstance(node.parent, (exp.Filter, exp.Window)):
        raise _refusal("bitmap_and_agg", "this PC runs it as a plain aggregate only.",
                       "filter the rows in WHERE or a subquery")
    return _template(_BITMAP_AND_AGG, a=args[0])


def _counter_diff(node: exp.Anonymous) -> exp.Expression:
    """counter_diff(value [, start_time]) OVER (... ORDER BY ...): the rise from the last row with a value,
    NULL on the first row or a reset (a smaller value, or a later start_time)."""
    args = list(node.expressions)
    _arity("counter_diff", args, 1, 2)
    window = node.parent
    if not isinstance(window, exp.Window) or window.this is not node:
        raise ValueError("[WINDOW_FUNCTION_WITHOUT_OVER_CLAUSE] counter_diff() needs OVER (... ORDER BY ...).")
    named = window.args.get("alias")
    if named is not None and not window.args.get("order"):  # OVER w, with WINDOW w AS (...) on its SELECT
        select = window.find_ancestor(exp.Select)
        specs = (select.args.get("windows") or []) if select is not None else []
        spec = next((w for w in specs if w.name == named.name), None)
        if spec is None:
            raise ValueError(f"counter_diff() is over the window {named.name}, which no WINDOW clause of its SELECT "
                             "names; write the window inline.")
        window = spec
    if not window.args.get("order"):
        raise ValueError("[WINDOW_FUNCTION_FRAME_NOT_ORDERED] counter_diff() needs an ORDER BY in its OVER clause.")
    start = args[1] if len(args) == 2 else exp.null()
    last = _template("lag(CASE WHEN _v IS NOT NULL THEN {'v': _v, 's': _s} END IGNORE NULLS)", v=args[0], s=start)
    last = exp.Window(this=last, partition_by=[p.copy() for p in window.args.get("partition_by") or []],
                      order=window.args["order"].copy())
    return exp.paren(_template(
        "CASE WHEN _v IS NULL THEN NULL "
        "WHEN _v < 0 THEN error('[COUNTER_DIFF_NEGATIVE_COUNTER_VALUE] counter_diff() got a negative counter value.') "
        "WHEN _p IS NULL THEN NULL "
        "WHEN _s < struct_extract(_p, 's') THEN error('[COUNTER_DIFF_START_TIME_DECREASED] counter_diff() got a "
        "start_time earlier than the previous row''s.') "
        "WHEN struct_extract(_p, 'v') > _v OR _s > struct_extract(_p, 's') THEN NULL "
        "ELSE _v - struct_extract(_p, 'v') END", v=args[0], s=start, p=last))

# A pattern list (#extended, #iso8601, #rfc3339, #unix) and several patterns tried in turn
_PATTERN_LISTS = ("parse_timestamp", "try_parse_timestamp")

_PARSED = {"to_time": ("TIME", False), "try_to_time": ("TIME", True), "try_to_date": ("DATE", True)}

# IP addresses: databricks_ip's functions, which local_sql registers on every DuckDB connection
_IP_SAME_TYPE = ("ip_cidr", "ip_host", "ip_network", "ip_network_first", "ip_network_last")
_IP_INTEGER = ("ip_version", "ip_prefix_length")


def _binary(value: exp.Expression) -> bool:
    """Whether ``value`` is BINARY as written: an X'..' literal or a cast to BINARY, as written or as
    sql.spark_binary_cast rewrote it (which keeps the cast's type)."""
    kinds = (exp.DataType.Type.BINARY, exp.DataType.Type.VARBINARY)
    rewritten = isinstance(value, exp.Paren) and isinstance(value.type, exp.DataType) and value.type.this in kinds
    return isinstance(value, exp.HexString) or rewritten or (isinstance(value, exp.Cast) and value.to.this in kinds)


def _ip(name: str, args: list[exp.Expression]) -> exp.Expression | None:
    from . import databricks_ip
    base = name.removeprefix("try_")
    if base == "ip_cidr_contains" and name == base:
        _arity(name, args, 2, 2)
        return _template("__kf_ip_contains(typeof(_a), hex(_a), typeof(_b), hex(_b))", a=args[0], b=args[1])
    if base not in databricks_ip.OPS:
        return None
    _arity(name, args, 1, 1)
    binary = base == "ip_as_binary" or (base in _IP_SAME_TYPE and _binary(args[0]))
    out = "binary" if binary else "text" if base in _IP_SAME_TYPE else "fixed"  # text: STRING as written
    call = f"__kf_ip('{name}:{out}', typeof(_a), hex(_a))"
    sql = f"unhex({call})" if binary else f"CAST({call} AS INTEGER)" if base in _IP_INTEGER else call
    return _template(sql, a=args[0])


# Byte-level strings: a DuckDB VARCHAR is always UTF-8, so the bytes come from hex(), which gives a STRING's
# UTF-8 bytes and a BINARY's own; databricks_bytes has the ones DuckDB has no function for
_UTF8 = "TRY(decode(unhex(hex(_a))))"
_STRINGS = {
    "is_valid_utf8": f"CASE WHEN _a IS NULL THEN NULL ELSE {_UTF8} IS NOT NULL END",
    "try_validate_utf8": _UTF8,
    "validate_utf8": f"CASE WHEN _a IS NULL THEN NULL WHEN {_UTF8} IS NULL THEN error('[INVALID_UTF8_STRING] "
                     "validate_utf8() got bytes that aren''t valid UTF-8; try_validate_utf8 gives NULL for them, "
                     "and make_valid_utf8 replaces them.') ELSE decode(unhex(hex(_a))) END",
    "make_valid_utf8": "__kf_make_valid_utf8(hex(_a))",
    "url_decode": "__kf_url_decode(_a, false)",
    "try_url_decode": "__kf_url_decode(_a, true)",
}
_ZSTD = ("zstd_compress", "zstd_decompress", "try_zstd_decompress")
_INTEGERS = (exp.DataType.Type.TINYINT, exp.DataType.Type.SMALLINT, exp.DataType.Type.INT, exp.DataType.Type.BIGINT)


def _hmac(args: list[exp.Expression]) -> exp.Expression:
    _arity("hmac", args, 2, 3)
    algorithm = args[2] if len(args) == 3 else exp.Literal.string("SHA-256")
    return _template("unhex(__kf_hmac(hex(_a), hex(_b), _c))", a=args[0], b=args[1], c=algorithm)


def _bit_reverse(args: list[exp.Expression]) -> exp.Expression:
    """bit_reverse, in the argument's own type where it shows as written (64Y, CAST(x AS SMALLINT), -2),
    and BIGINT otherwise, since a DuckDB function has one result type."""
    _arity("bit_reverse", args, 1, 1)
    value = args[0]
    call = _template("__kf_bit_reverse(typeof(_a), _a)", a=value)
    bare = value.this if isinstance(value, exp.Neg) else value
    if isinstance(value, exp.Cast) and value.to.this in _INTEGERS:
        return exp.cast(call, value.to.copy())
    if isinstance(bare, exp.Literal) and not bare.is_string and bare.this.isdigit() and int(bare.this) < 2**31:
        return exp.cast(call, "INT")
    return call


def _collate(node: exp.Collate) -> exp.Expression:
    """UTF8_BINARY is how DuckDB compares already, and UTF8_LCASE (lower case, then byte by byte) is its
    NOCASE; the ICU ones (UNICODE, a language, _CI, _AI, _RTRIM) order and match another way."""
    name = node.expression.name.upper()
    if name == "UTF8_BINARY":
        return node.this
    if name == "UTF8_LCASE":
        return exp.Collate(this=node.this, expression=exp.var("NOCASE"))
    raise _refusal("collate", f"it compares by the {name} collation, and this PC has UTF8_BINARY and "
                              "UTF8_LCASE only.", "use UTF8_LCASE, or lower() on both sides")


# VARIANT: databricks_variant's functions keep it as DuckDB JSON text in the form Databricks prints it
_EXPLODE = ("variant_explode", "variant_explode_outer")
_ROWS = "unnest(__kf_variant_items(_v, typeof(_v)))"
_ROW = ("struct_extract(__kf_r, 'pos') AS pos, struct_extract(__kf_r, 'key') AS key, "
        "CAST(struct_extract(__kf_r, 'value') AS JSON) AS value")
_VARIANT_SCHEMAS = ("schema_of_variant", "schema_of_variant_agg")


def _parse_json(value: exp.Expression, safe: bool) -> exp.Expression:
    return _template(f"CAST(__kf_parse_json(_a, {'true' if safe else 'false'}) AS JSON)", a=value)


def _literal(name: str, value: exp.Expression, what: str) -> str:
    if not (isinstance(value, exp.Literal) and value.is_string):
        raise ValueError(f"{name}() takes its {what} as a string literal in Databricks.")
    return str(value.this)


def _variant_get(name: str, args: list[exp.Expression]) -> exp.Expression:
    from . import databricks_variant
    _arity(name, args, 3, 3)
    steps = json.dumps(databricks_variant.path(_literal(name, args[1], "path")))
    kind = exp.DataType.build(_literal(name, args[2], "type"), dialect="databricks")
    if kind.this == exp.DataType.Type.DECIMAL and not kind.expressions:
        kind = exp.DataType.build("DECIMAL(10,0)")  # Databricks' DECIMAL
    target = "variant" if kind.this == exp.DataType.Type.VARIANT else kind.sql(dialect="databricks").lower()
    safe = "true" if name.startswith("try_") else "false"
    call = f"__kf_variant_get(_v, typeof(_v), {exp.Literal.string(steps).sql()}, " \
           f"{exp.Literal.string(target).sql()}, {safe})"
    return exp.cast(_template(call, v=args[0]), exp.DataType.build("JSON") if target == "variant" else kind)


def _explode(node: exp.Anonymous) -> exp.Expression:
    """variant_explode(_outer) in FROM or LATERAL, as a subquery of pos, key and value rows."""
    name, args = node.name.lower(), list(node.expressions)
    _arity(name, args, 1, 1)
    if name == "variant_explode":
        sql = f"SELECT {_ROW} FROM (SELECT {_ROWS} AS __kf_r)"
    else:  # one row of NULLs for NULL, a scalar, or an empty array or object
        sql = f"SELECT {_ROW} FROM (SELECT 1) AS __kf_one LEFT JOIN LATERAL (SELECT {_ROWS} AS __kf_r) ON TRUE"
    return _template(sql, v=args[0])


_XML = ("from_xml", "to_xml", "schema_of_xml")


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
    if name.startswith(("ip_", "try_ip_")):
        return _ip(name, args) or node
    if name in _STRINGS:
        _arity(name, args, 1, 1)
        return exp.paren(_template(_STRINGS[name], a=args[0]))
    if name == "hmac":
        return _hmac(args)
    if name == "bit_reverse":
        return _bit_reverse(args)
    if name in _ZSTD:
        raise _refusal(name, "this PC has no Zstandard codec.", "use base64 or hex for text that has to "
                                                                "travel as bytes")
    if name == "try_parse_json":
        _arity(name, args, 1, 1)
        return _parse_json(args[0], safe=True)
    if name == "is_variant_null":
        _arity(name, args, 1, 1)
        return exp.paren(_template(
            "CASE WHEN typeof(_a) NOT IN ('JSON', '\"NULL\"') THEN error('[DATATYPE_MISMATCH] is_variant_null() "
            "takes a VARIANT; write parse_json(...).') ELSE coalesce(json_type(_a) = 'NULL', false) END", a=args[0]))
    if name in ("variant_get", "try_variant_get"):
        return _variant_get(name, args)
    if name == "to_variant_object":
        _arity(name, args, 1, 1)
        return _template("CAST(__kf_to_variant(CAST(to_json(_a) AS VARCHAR), typeof(_a)) AS JSON)", a=args[0])
    if name in _VARIANT_SCHEMAS:
        raise _refusal(name, "this PC keeps a VARIANT as JSON text, which doesn't keep every type Databricks "
                             "reports (a DECIMAL's scale, or a DOUBLE that prints like a DECIMAL).",
                       "use typeof on variant_get's values")
    if name in _EXPLODE:
        raise ValueError(f"{name}() is a table function in Databricks: write it in FROM or after LATERAL.")
    if name == "try_make_interval":
        return _try_make_interval(args)
    if name == "timestamp_nanos":
        return _timestamp_nanos(args)
    if name == "unix_nanos":
        return _unix_nanos(args)
    if name == "bitmap_and_agg":
        return _bitmap_and_agg(node)
    if name == "counter_diff":
        raise ValueError("[WINDOW_FUNCTION_WITHOUT_OVER_CLAUSE] counter_diff() needs OVER (... ORDER BY ...).")
    if name == "schema_of_json_agg":
        raise _refusal(name, "this PC works out schema_of_json for a literal as it reads the cell, and has no "
                             "merge of the schemas of many rows' JSON.", "use schema_of_json on one sample row")
    if name == "sql_keywords":
        raise _refusal(name, "this PC doesn't have Databricks' list of SQL keywords and which of them are reserved.")
    if name == "try_reflect":
        raise _refusal(name, "it calls a Java method, and this PC runs no Java.")
    if name in _XML:
        raise _refusal(name, "this PC has no XML reader that infers and converts values the way Databricks' "
                             "XML source does.", "use from_json and to_json for data you can keep as JSON")
    if name in _VECTOR_AGGREGATES:
        raise _refusal(name, "DuckDB has no aggregate that adds vectors element by element.",
                       "posexplode the vectors and GROUP BY the position")
    return node


def rewrite(statement: exp.Expression) -> exp.Expression:
    """``statement`` with Databricks' own functions written for DuckDB, or ValueError for one this PC refuses."""
    # current_timezone() is the session's zone (DuckDB's TimeZone setting, UTC unless the cell sets another);
    # DuckDB has no function of that name
    for zone in list(statement.find_all(exp.CurrentTimezone)):
        setting = exp.Anonymous(this="CURRENT_SETTING", expressions=[exp.Literal.string("TimeZone")])
        if zone is statement:
            return setting
        zone.replace(setting)
    # current_database() is a synonym of current_schema() in Databricks: the schema, not DuckDB's catalog
    for database in list(statement.find_all(exp.CurrentDatabase)):
        schema = exp.CurrentSchema()
        if database is statement:
            return schema
        database.replace(schema)
    for node in statement.find_all(exp.Anonymous, *_TYPED_SKETCHES):
        name = node.name.lower() if isinstance(node, exp.Anonymous) else _TYPED_SKETCHES[type(node)]
        if name in _sketch_names():
            instead = ("use approx_count_distinct" if name.startswith(("theta_", "tuple_"))
                       else "use percentile_approx" if name.startswith("kll_") else "")
            raise _refusal(name, "it builds or reads a DataSketches sketch, whose bytes this PC can't make the "
                                 "same way.", instead)

    for node in list(statement.find_all(exp.Anonymous)):  # table and window functions first: they replace their parent
        if node.name.lower() == "counter_diff" and isinstance(node.parent, exp.Window):
            node.parent.replace(_counter_diff(node))
        elif node.name.lower() in _EXPLODE and isinstance(node.parent, (exp.Table, exp.Lateral)):
            rows = exp.Subquery(this=_explode(node))
            if isinstance(node.parent, exp.Table):
                rows.set("alias", node.parent.args.get("alias"))
                node.parent.replace(rows)
            else:
                node.replace(rows)

    # innermost first, so a call inside another's arguments is written before the outer one copies them
    for call in reversed(list(statement.find_all(exp.Anonymous, exp.TimeDiff, exp.TimeTrunc, exp.Collate, exp.ParseJSON,
                                                   exp.Dayname, exp.Monthname, exp.GroupConcat, exp.Time,
                                                   exp.CurrentTime, exp.Localtime, exp.Uniform, exp.Randstr,
                                                   bfs=False))):
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
    if isinstance(call, exp.Collate):
        return _collate(call)
    if isinstance(call, exp.ParseJSON):
        return _parse_json(call.this, safe=bool(call.args.get("safe")))
    if isinstance(call, (exp.Dayname, exp.Monthname)):  # Databricks' short English names: Fri, Apr
        return _template(f"strftime(CAST(_a AS DATE), '{'%a' if isinstance(call, exp.Dayname) else '%b'}')",
                         a=call.this)
    if isinstance(call, exp.GroupConcat):  # listagg and string_agg join with no delimiter unless given one
        if call.args.get("separator") is not None:
            return None
        joined = call.copy()
        joined.set("separator", exp.Literal.string(""))
        return joined
    if isinstance(call, exp.Time):  # a TIME from a string or a timestamp's time of day (in UTC here)
        return exp.cast(exp.cast(call.this.copy(), exp.DataType.build("TIMETZ")), exp.DataType.build("TIME"))
    if isinstance(call, (exp.CurrentTime, exp.Localtime)):
        return _current_time(call)
    if isinstance(call, exp.Uniform):
        return _uniform(call)
    if isinstance(call, exp.Randstr):
        return _randstr(call)
    if isinstance(call, exp.Anonymous):
        written = _function(call)
        return None if written is call else written
    return None


# WR-309: Databricks SQL's table statements. A table a SQL cell makes here is a DuckDB table in the session,
# which answers queries as a Delta table does; what only a Delta table has is refused by name.
_LOCAL_TABLE = "a table a SQL cell makes here is a DuckDB table in this session, not a Delta table"
_IDENTITY = "__kf_identity_"   # the sequence behind a GENERATED BY DEFAULT AS IDENTITY column
_ALWAYS = "__kf_always_"       # and behind a GENERATED ALWAYS AS IDENTITY one, whose values no write may give
WRITTEN_HERE = "kf_written_here"  # a statement this module adds, not one the cell wrote
IDENTITY_PREFIXES = (_IDENTITY, _ALWAYS)
_SEQUENCE_NAME = re.compile(r"__kf_(?:always|identity)_[0-9a-f]{32}")  # the names _identity gives, and only those
_BIGINT_RANGE = "MINVALUE -9223372036854775808 MAXVALUE 9223372036854775807"


def _statement_refusal(what: str, why: str, instead: str) -> ValueError:
    return ValueError(f"{what} can't run on this PC the way it runs in Databricks, because {why}, so nothing ran. "
                      f"{_WAREHOUSE}, or {instead}.")


def _whole_number(value: exp.Expression | None, default: int) -> int:
    """An identity column's START WITH or INCREMENT BY, which Databricks takes as a whole number only."""
    if value is None:
        return default
    negative = isinstance(value, exp.Neg)
    number = value.this if negative else value
    if not (isinstance(number, exp.Literal) and not number.is_string and number.name.isdigit()):
        raise _statement_refusal("This identity column", "its START WITH and INCREMENT BY must be whole numbers",
                                 "write them as whole numbers")
    return -int(number.name) if negative else int(number.name)


def _identity(column: exp.ColumnDef, constraint: exp.ColumnConstraint,
              identity: exp.GeneratedAsIdentityColumnConstraint) -> exp.Expression:
    """The CREATE SEQUENCE behind an identity column, with the column's constraint made its DEFAULT. Databricks
    numbers from START WITH (1 unless given) by INCREMENT BY (1 unless given); its values are unique but may
    skip, so DuckDB's one after another are among those it could give."""
    kind = column.args.get("kind")
    if not (isinstance(kind, exp.DataType) and kind.is_type(exp.DataType.Type.BIGINT)):
        raise _statement_refusal(f"Identity column {column.name}", "Databricks makes an identity column BIGINT only",
                                 "declare the column BIGINT")
    start = _whole_number(identity.args.get("start"), 1)
    step = _whole_number(identity.args.get("increment"), 1)
    if step == 0:
        raise _statement_refusal(f"Identity column {column.name}", "its INCREMENT BY can't be 0",
                                 "give it a step other than 0")
    name = f"{_ALWAYS if identity.this else _IDENTITY}{uuid.uuid4().hex}"
    constraint.set("kind", exp.DefaultColumnConstraint(
        this=exp.Anonymous(this="nextval", expressions=[exp.Literal.string(name)])))
    sequence = cast(exp.Expression, parse_one(
        f"CREATE SEQUENCE {name} START WITH {start} INCREMENT BY {step} {_BIGINT_RANGE}", read="duckdb"))
    sequence.meta[WRITTEN_HERE] = True  # local_sql's policy check skips it
    return sequence


def _table_properties(statement: exp.Create) -> str | None:
    """USING DELTA is how Databricks makes a table anyway (it is the default), so it is dropped, and so are
    PARTITIONED BY and CLUSTER BY, which lay out a Delta table's files but don't change what a query reads. The
    table's COMMENT is given back, to keep with COMMENT ON. Another format and TBLPROPERTIES are refused by name."""
    properties = statement.args.get("properties")
    if not isinstance(properties, exp.Properties):
        return None
    comment = None
    for prop in list(properties.expressions):
        if isinstance(prop, exp.SchemaCommentProperty):
            comment = prop.this.name
            prop.pop()
        elif isinstance(prop, exp.ClusterProperty):
            prop.pop()
        elif isinstance(prop, exp.PartitionedByProperty):
            names = prop.this.expressions if isinstance(prop.this, exp.Schema) else []
            if not names or not all(isinstance(n, (exp.Identifier, exp.Column)) for n in names):
                raise _statement_refusal("PARTITIONED BY with column types", "DuckDB makes a table's columns only "
                                         "from its column list", "declare the columns there and partition by name")
            prop.pop()
        if isinstance(prop, exp.FileFormatProperty):
            if prop.name.upper() != "DELTA":
                raise _statement_refusal(f"USING {prop.name.upper()}", f"{_LOCAL_TABLE}, so it can't keep its "
                                         f"rows as {prop.name.upper()} files", "leave out USING, or write USING DELTA")
            prop.pop()
        elif type(prop) is exp.Property:  # TBLPROPERTIES; its subclasses are the other clauses
            raise _statement_refusal("TBLPROPERTIES", f"{_LOCAL_TABLE}, so a table property such as "
                                     f"{prop.name} would have no effect", "leave out TBLPROPERTIES")
    if not properties.expressions:
        statement.set("properties", None)
    return comment


def _comment(target: exp.Expression, text: str) -> exp.Expression:
    """COMMENT ON a table or column, to keep a comment a CREATE TABLE gives (DuckDB reads none in CREATE)."""
    comment = exp.Comment(this=target, kind="TABLE" if isinstance(target, exp.Table) else "COLUMN",
                          expression=exp.Literal.string(text))
    comment.meta[WRITTEN_HERE] = True
    return comment


_ALTER_CLAUSES = ((r"\bUNSET\s+TBLPROPERTIES\b", "UNSET TBLPROPERTIES"), (r"\bSET\s+TBLPROPERTIES\b", "SET TBLPROPERTIES"),
                  (r"\bADD\s+CONSTRAINT\b", "ADD CONSTRAINT"), (r"\bDROP\s+CONSTRAINT\b", "DROP CONSTRAINT"))


def _alter_refusal(clause: str) -> ValueError:
    """The refusal for an ALTER TABLE clause a DuckDB table can't take."""
    if clause.endswith("TBLPROPERTIES"):
        return _statement_refusal(f"ALTER TABLE ... {clause}", f"{_LOCAL_TABLE}, which has no table properties",
                                  "leave the statement out")
    if clause == "ADD CONSTRAINT":
        return _statement_refusal("ALTER TABLE ... ADD CONSTRAINT", "DuckDB can't add a constraint to a table that "
                                  "already exists", "put the CHECK in the table's CREATE TABLE, where every write is "
                                  "checked against it as on Databricks")
    return _statement_refusal("ALTER TABLE ... DROP CONSTRAINT", "DuckDB can't drop a constraint from a table",
                              "make the table again without it")


def table_statements(statement: exp.Expression) -> list[exp.Expression]:
    """``statement`` with Databricks' table definitions written for DuckDB: a generated column as DuckDB's
    GENERATED ALWAYS AS (expression), an identity column as a sequence (which comes first), USING DELTA
    dropped. What a DuckDB table can't do is a ValueError that names it: TBLPROPERTIES, another format,
    and adding or dropping a constraint on a table that exists (a CHECK in CREATE TABLE is checked on every
    write, as Databricks checks it)."""
    if isinstance(statement, exp.Command) and statement.name.upper() == "ALTER":
        # sqlglot can fall back to a Command for some of these, so the text is read as well as the tree
        for pattern, clause in _ALTER_CLAUSES:
            if re.search(pattern, str(statement.expression or ""), re.IGNORECASE):
                raise _alter_refusal(clause)
    if isinstance(statement, exp.Alter):
        for action in statement.args.get("actions") or []:
            if isinstance(action, exp.AlterSet) and action.find(exp.Properties):
                raise _alter_refusal("SET TBLPROPERTIES")
            if isinstance(action, exp.AddConstraint):
                raise _alter_refusal("ADD CONSTRAINT")
            if isinstance(action, exp.Drop) and str(action.args.get("kind") or "").upper() == "CONSTRAINT":
                raise _alter_refusal("DROP CONSTRAINT")
        return [statement]
    if not (isinstance(statement, exp.Create) and str(statement.args.get("kind") or "").upper() == "TABLE"):
        return [statement]
    table_comment = _table_properties(statement)
    table = statement.this.this if isinstance(statement.this, exp.Schema) else statement.this
    sequences: list[exp.Expression] = []
    comments: list[exp.Expression] = []
    if table_comment is not None and isinstance(table, exp.Table):
        comments.append(_comment(table.copy(), table_comment))
    for column in statement.find_all(exp.ColumnDef):
        for constraint in list(column.args.get("constraints") or []):
            kind = constraint.args.get("kind")
            if isinstance(kind, exp.CommentColumnConstraint):
                constraint.pop()
                if isinstance(table, exp.Table):
                    comments.append(_comment(exp.Column(this=column.this.copy(), table=table.this.copy(),
                                                        db=table.args.get("db"), catalog=table.args.get("catalog")),
                                             kind.this.name))
            elif isinstance(kind, exp.ComputedColumnConstraint):  # sqlglot writes it AS expr, which DuckDB can't read
                constraint.set("kind", exp.GeneratedAsIdentityColumnConstraint(this=True, expression=kind.this))
            elif isinstance(kind, exp.GeneratedAsIdentityColumnConstraint) and kind.args.get("expression") is None:
                sequences.append(_identity(column, constraint, kind))
    return [*sequences, statement, *comments]


def _special_columns(create: exp.Create) -> dict[str, tuple[str, str]]:
    """The columns of a CREATE TABLE (as table_statements writes it, or as DuckDB keeps it) whose values a
    write may not give: a GENERATED ALWAYS AS IDENTITY column as ("identity", its sequence) and a generated
    column as ("generated", its expression). A GENERATED BY DEFAULT AS IDENTITY column is there as
    ("by default", its sequence), which a write may give, so that a dropped table's sequence can go too."""
    found: dict[str, tuple[str, str]] = {}
    schema = create.this
    if not isinstance(schema, exp.Schema):
        return found
    for column in schema.expressions:
        if not isinstance(column, exp.ColumnDef):
            continue
        for constraint in column.args.get("constraints") or []:
            kind = constraint.args.get("kind")
            if isinstance(kind, exp.GeneratedAsIdentityColumnConstraint) and kind.args.get("expression") is not None:
                expression = kind.args["expression"]
                expression = expression.this if isinstance(expression, exp.Paren) else expression
                found[column.name.lower()] = ("generated", expression.sql(dialect="databricks"))
            elif isinstance(kind, exp.DefaultColumnConstraint):
                call = kind.find(exp.Anonymous)
                name = call.expressions[0].name if call and call.name.lower() == "nextval" and call.expressions else ""
                if name.startswith(_ALWAYS):
                    found[column.name.lower()] = ("identity", name)
                elif name.startswith(_IDENTITY):
                    found[column.name.lower()] = ("by default", name)
    return found


def _table_key(table: exp.Table, here: tuple[str, str]) -> tuple[str, str, str]:
    """A table's catalog, schema and name, lower case, with the session's current ones where it gives none."""
    return ((table.catalog or here[0]).lower(), (table.db or here[1]).lower(), table.name.lower())


def _session_columns(key: tuple[str, str, str], con: Any) -> dict[str, tuple[str, str]] | None:
    """_special_columns of a table the session has, read from the CREATE TABLE DuckDB keeps for it (DuckDB 1
    has no column that says a column is generated); None when the session has no such table."""
    rows = con.execute("SELECT sql FROM duckdb_tables() WHERE lower(database_name) = ? AND lower(schema_name) = ? "
                       "AND lower(table_name) = ?", list(key)).fetchall()
    if not rows:
        return None
    try:
        create = parse_one(str(rows[0][0]), read="duckdb")
    except (ParseError, ValueError):  # silent-ok: a table sqlglot can't read has no columns this module made
        return {}
    return _special_columns(create) if isinstance(create, exp.Create) else {}


def _drop_sequences(names: set[str], con: Any) -> list[exp.Expression]:
    """DROP SEQUENCE for each of ``names`` that _identity made, qualified with where the session keeps it (it
    was made in the schema current then, which may not be the one current now). A name the session doesn't
    have yet was made by this cell, in the schema current now. Each name goes in as a quoted identifier."""
    drops: list[exp.Expression] = []
    for name in sorted(n for n in names if _SEQUENCE_NAME.fullmatch(n)):
        places = con.execute("SELECT database_name, schema_name FROM duckdb_sequences() WHERE sequence_name = ? "
                             "ORDER BY 1, 2", [name]).fetchall() or [(None, None)]
        for database, schema in places:
            parts = [str(p) for p in (database, schema) if p is not None] + [name]
            template = ".".join(f"kf_part{i}" for i in range(len(parts)))
            drop = cast(exp.Expression, parse_one(f"DROP SEQUENCE IF EXISTS {template}", read="duckdb"))
            # a tree, not text: the names are read from the session, so each goes in as one quoted identifier
            for identifier in list(drop.find_all(exp.Identifier)):
                identifier.replace(exp.to_identifier(parts[int(identifier.name.removeprefix("kf_part"))], quoted=True))
            drop.meta[WRITTEN_HERE] = True
            drops.append(drop)
    return drops


def _written_columns(statement: exp.Expression) -> tuple[exp.Table, list[set[str] | None]] | None:
    """The table ``statement`` writes, and for each of its writes the columns it gives values for (None when
    it gives every column: no column list, INSERT *, UPDATE SET *)."""
    target = statement.this
    if isinstance(statement, exp.Insert):
        table = target.this if isinstance(target, exp.Schema) else target
        if statement.args.get("by_name") and isinstance(statement.expression, exp.Query):
            return table, [{n.lower() for n in statement.expression.named_selects}]
        return table, [{c.name.lower() for c in target.expressions} if isinstance(target, exp.Schema) else None]
    if isinstance(statement, exp.Update):
        return target, [{e.this.name.lower() for e in statement.expressions if isinstance(e, exp.EQ)}]
    if isinstance(statement, exp.Merge):
        writes: list[set[str] | None] = []
        whens = statement.args.get("whens")
        for when in whens.expressions if whens else []:
            then = when.args.get("then")
            if isinstance(then, exp.Insert):
                writes.append(None if isinstance(then.this, exp.Star) or then.this is None
                              else {c.name.lower() for c in then.this.expressions})
            elif isinstance(then, exp.Update):
                writes.append(None if any(isinstance(e, exp.Star) for e in then.expressions)
                              else {e.this.name.lower() for e in then.expressions if isinstance(e, exp.EQ)})
        return target, writes
    return None


def table_writes(statements: list[exp.Expression], con: Any) -> list[exp.Expression]:
    """``statements`` checked against the identity and generated columns of the tables they write, from the
    session (``con``) and from the cell's own CREATE TABLEs, before anything runs. Databricks refuses a value
    written into a GENERATED ALWAYS AS IDENTITY column, where a DuckDB DEFAULT would take it; and it takes a
    value written into a generated column only when it equals the expression, where DuckDB refuses any. Both
    are refused by name. A DROP TABLE (or CREATE OR REPLACE) of a table with an identity column is followed by
    a DROP SEQUENCE of the sequence behind it, so none is left behind."""
    from .duckdb_rows import one_row
    # read to its end, which frees the connection on DuckDB 2.0 for what runs next (a read_files table's register)
    here_row = one_row(con.execute("SELECT current_database(), current_schema()"))
    here = (str(here_row[0]), str(here_row[1]))
    made: dict[tuple[str, str, str], dict[str, tuple[str, str]] | None] = {}  # None: no such table

    def columns_of(table: exp.Table) -> dict[str, tuple[str, str]] | None:
        key = _table_key(table, here)
        if key not in made:
            made[key] = _session_columns(key, con)
        return made[key]

    def sequences_of(columns: dict[str, tuple[str, str]] | None) -> set[str]:
        return {name for how, name in (columns or {}).values() if how != "generated"}

    written_out: list[exp.Expression] = []
    for statement in statements:
        sequences: set[str] = set()
        if str(statement.args.get("kind") or "").upper() == "TABLE" and isinstance(statement, (exp.Create, exp.Drop)):
            target = statement.this.this if isinstance(statement.this, exp.Schema) else statement.this
            # sqlglot keeps a DROP's tables in tables (this, in some builds)
            for table in (statement.args.get("tables") or [target]) if isinstance(statement, exp.Drop) else [target]:
                if not isinstance(table, exp.Table):
                    continue
                there = columns_of(table)
                if isinstance(statement, exp.Drop):
                    sequences |= sequences_of(there)
                    made[_table_key(table, here)] = None
                elif statement.args.get("exists") and there is not None:
                    # CREATE TABLE IF NOT EXISTS of a table that is there keeps it, and its columns; the sequences
                    # made for the new definition are left unused, so they go
                    sequences |= sequences_of(_special_columns(statement))
                else:
                    if statement.args.get("replace"):
                        sequences |= sequences_of(there)
                    made[_table_key(table, here)] = _special_columns(statement)
        written_out.append(statement)
        written_out.extend(_drop_sequences(sequences, con))
        written = _written_columns(statement)
        if written is None or not isinstance(written[0], exp.Table):
            continue
        special = columns_of(written[0]) or {}
        always = {c for c, (how, _) in special.items() if how == "identity"}
        generated = {c: text for c, (how, text) in special.items() if how == "generated"}
        for columns in written[1]:
            given: set[str] = always if columns is None else always & columns
            if given:
                name = sorted(given)[0]
                raise _statement_refusal(f"A write that gives {name} a value", f"{name} is GENERATED ALWAYS AS "
                                         "IDENTITY, so Databricks makes its values and refuses one a write gives",
                                         f"list the columns to write and leave {name} out")
            computed = sorted(generated.keys() & (columns or set()))  # no column list: DuckDB says so itself
            if computed:
                name = computed[0]
                raise _statement_refusal(f"A write that gives {name} a value", f"{name} is GENERATED ALWAYS AS "
                                         f"({generated[name]}), and this PC works it out on every write and "
                                         "takes no value for it, not even one Databricks would take",
                                         f"list the columns to write and leave {name} out")
    return written_out


# Statements Databricks answers one way and DuckDB another, or reads where Databricks doesn't: written so the
# answer is Databricks', or refused by name.
_VARIABLES = "a query reads a Databricks session variable by its name, which DuckDB doesn't"
_LEAVE_OUT = "leave the statement out"
_NOT_HERE: dict[type[exp.Expression], tuple[str, str, str]] = {  # what, why, instead
    exp.Declare: ("DECLARE VARIABLE", _VARIABLES, "write the value into the query"),
    exp.Cache: ("CACHE TABLE", "it keeps a table in Apache Spark's cache, which this PC doesn't have",
                "leave the statement out (for CACHE TABLE name AS query, write CREATE TEMPORARY VIEW name AS query)"),
    exp.Uncache: ("UNCACHE TABLE", "it takes a table out of Apache Spark's cache, which this PC doesn't have",
                  _LEAVE_OUT),
}
_MAINTENANCE = {"OPTIMIZE": "it lays out a Delta table's files", "VACUUM": "it removes a Delta table's unused files"}
# DuckDB's type names as Databricks' DESCRIBE TABLE writes them
_DESCRIBE_TYPES = {"INTEGER": "int", "BIGINT": "bigint", "SMALLINT": "smallint", "TINYINT": "tinyint",
                   "VARCHAR": "string", "DOUBLE": "double", "FLOAT": "float", "BOOLEAN": "boolean", "DATE": "date",
                   "TIMESTAMP WITH TIME ZONE": "timestamp", "TIMESTAMP": "timestamp_ntz", "BLOB": "binary"}
MULTIPLE_MATCHES = ("[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE] MERGE stopped, as Databricks stops it, "
                    "because more than one source row matches the same target row and which one should change it is "
                    "ambiguous. "
                    "Nothing was changed. Keep one source row for each target row, for example the latest change "
                    "for each key.")


def _databricks_stops(what: str, why: str, instead: str) -> ValueError:
    """A refusal of a statement Databricks stops too, so it has no warehouse step: running it there won't help."""
    return ValueError(f"{what}, so nothing ran. Databricks stops it too, {why}. {instead}.")


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _describe(statement: exp.Describe) -> exp.Expression:
    """DESCRIBE [TABLE] t as Databricks answers it: col_name, data_type and comment for each column, in order,
    with Databricks' type names. A column of a type this PC doesn't name as Databricks does stops the query by
    name, and so does a table that isn't there."""
    table = statement.this
    if any(statement.args.get(k) for k in ("style", "properties", "expressions", "partition", "format", "as_json")) \
            or not isinstance(table, exp.Table) or str(statement.args.get("kind") or "TABLE").upper() != "TABLE":
        raise _statement_refusal("This DESCRIBE", "only a plain DESCRIBE TABLE answers here with Databricks' columns",
                                 "write DESCRIBE TABLE with the table's name only")
    where = [f"lower(table_name) = lower({_quoted(table.name)})", "NOT internal"]
    if table.args.get("db"):
        where.append(f"lower(schema_name) = lower({_quoted(table.db)})")
    if table.args.get("catalog"):
        where.append(f"lower(database_name) = lower({_quoted(table.catalog)})")
    types = " ".join(f"WHEN {_quoted(duck)} THEN {_quoted(dbx)}" for duck, dbx in _DESCRIBE_TYPES.items())
    other = _statement_refusal("DESCRIBE TABLE of this column's type", "this PC doesn't write that type's name as "
                               "Databricks does", "read the column's type with typeof()")
    missing = (f"[TABLE_OR_VIEW_NOT_FOUND] The table or view {table.name} cannot be found, so DESCRIBE TABLE has "
               "nothing to describe.")
    query = (f"WITH c AS (SELECT column_name, data_type, comment, column_index FROM duckdb_columns() "
             f"WHERE {' AND '.join(where)}) "
             f"SELECT col_name, data_type, comment FROM (SELECT column_name AS col_name, CASE data_type {types} "
             f"ELSE CASE WHEN data_type LIKE 'DECIMAL(%' THEN lower(data_type) "
             f"ELSE error({_quoted(str(other))}) END END AS data_type, comment, column_index AS i FROM c "
             f"UNION ALL SELECT error({_quoted(missing)}), NULL, NULL, 0 WHERE NOT EXISTS (SELECT 1 FROM c)) "
             "ORDER BY i")
    return cast(exp.Expression, parse_one(query, read="duckdb"))


def _merge_matches_once(statement: exp.Merge) -> exp.Merge:
    """``statement`` with its source read through a check that stops the MERGE when more than one source row
    matches the same target row through its WHEN MATCHED clauses, as Databricks stops it (DuckDB 1.x would change
    the row from one of them). Left as it is when Databricks allows that: no WHEN MATCHED clause, or only an
    unconditional DELETE. The check is in the MERGE itself, so the cell still answers with the MERGE's count."""
    whens = statement.args.get("whens")
    matched = [w for w in (whens.expressions if whens else []) if w.args.get("matched") and not w.args.get("source")]
    target, source = statement.this, statement.args.get("using")
    if not matched or statement.args.get("on") is None or not isinstance(target, exp.Table) or source is None:
        return statement
    if len(matched) == 1 and matched[0].args.get("condition") is None and not isinstance(
            matched[0].args.get("then"), (exp.Update, exp.Insert)):  # WHEN MATCHED THEN DELETE
        return statement
    # Databricks (after 15.4 LTS) counts the matches the WHEN MATCHED conditions let through
    on = statement.args["on"].copy()
    conditions = [w.args["condition"].copy() for w in matched if w.args.get("condition") is not None]
    if len(conditions) == len(matched):
        on = exp.and_(on, exp.or_(*conditions))
    rowid = exp.column("rowid", table=target.alias_or_name)
    pairs = exp.select(rowid).from_(target.copy()).join(source.copy(), on=on) \
        .group_by(rowid.copy()).having("count(*) > 1")
    check = cast(exp.Select, parse_one(f"SELECT CASE WHEN EXISTS (SELECT 1) THEN error({_quoted(MULTIPLE_MATCHES)}) "
                                       "ELSE TRUE END", read="duckdb"))
    cast(exp.Exists, check.find(exp.Exists)).set("this", pairs)
    checked = exp.select("*").from_(source.copy()).where(exp.Subquery(this=check))
    statement.set("using", exp.Subquery(this=checked, alias=exp.TableAlias(
        this=exp.to_identifier(source.alias_or_name or "__kf_source"))))
    return statement


def _alter_columns(statement: exp.Alter) -> list[exp.Expression]:
    """ALTER TABLE ... ADD COLUMNS (a, b) and DROP COLUMNS (a, b) as one ALTER TABLE for each column, which is how
    DuckDB reads them, with an added column's COMMENT kept. An added column with a DEFAULT is refused: Databricks
    can't define one when it adds a column to a Delta table (its rows that are there stay NULL), where DuckDB would
    give those rows the default. So is FIRST or AFTER, since DuckDB adds a column at the end only."""
    actions = statement.args.get("actions") or []
    if len(actions) != 1:
        return [statement]
    action = actions[0]
    if isinstance(action, exp.Schema) and all(isinstance(c, exp.ColumnDef) for c in action.expressions):
        added: list[exp.Expression] = list(action.expressions)
    elif isinstance(action, exp.ColumnDef):
        added = [action]
    else:
        added = []
    table = statement.this
    written: list[exp.Expression] = []
    comments: list[exp.Expression] = []
    for column in added:
        if column.args.get("position") is not None:
            raise _statement_refusal("ALTER TABLE ... ADD COLUMN ... FIRST or AFTER", "DuckDB adds a column at the "
                                     "end of the table only", "add it at the end, and list the columns in the order "
                                     "you want in the query")
        for constraint in list(column.args.get("constraints") or []):
            kind = constraint.args.get("kind")
            if isinstance(kind, exp.DefaultColumnConstraint):
                raise _databricks_stops("ALTER TABLE ... ADD COLUMN ... DEFAULT can't run here",
                                        "because it takes no default when it adds a column to a Delta table",
                                        "Add the column, then ALTER TABLE ... ALTER COLUMN ... SET DEFAULT, which "
                                        "applies to new rows only")
            if isinstance(kind, exp.CommentColumnConstraint) and isinstance(table, exp.Table):
                constraint.pop()
                comments.append(_comment(exp.Column(this=column.this.copy(), table=table.this.copy(),
                                                    db=table.args.get("db"), catalog=table.args.get("catalog")),
                                         kind.this.name))
        one = statement.copy()
        one.set("actions", [column.copy()])
        written.append(one)
    if not added and isinstance(action, exp.Drop) and str(action.args.get("kind") or "").upper() == "COLUMNS":
        names = [n for t in action.args.get("tables") or [] for n in (t.expressions if isinstance(t, exp.Schema) else [t])]
        for name in names:
            one = statement.copy()
            one.set("actions", [exp.Drop(kind="COLUMN", exists=action.args.get("exists"),
                                         tables=[exp.Column(this=name.copy()) if isinstance(name, exp.Identifier)
                                                 else name.copy()])])
            written.append(one)
    return [*written, *comments] if written else [statement]


def other_statements(statement: exp.Expression) -> list[exp.Expression]:
    """``statement`` as Databricks answers it, for statements table_statements leaves as they are: DESCRIBE TABLE
    with Databricks' columns, and a MERGE that stops when more than one source row matches a target row. What
    Databricks reads another way is refused by name: UPDATE ... FROM (Databricks' UPDATE has no FROM), INSERT
    OVERWRITE and REPLACE WHERE, session variables, CACHE TABLE, OPTIMIZE and VACUUM."""
    for kind, (what, why, instead) in _NOT_HERE.items():
        if isinstance(statement, kind):
            raise _statement_refusal(what, why, instead)
    if any(call.name.lower() == "table_changes" for call in statement.find_all(exp.Anonymous)):
        raise _statement_refusal("table_changes", f"it reads a Delta table's change data feed, and {_LOCAL_TABLE}",
                                 "read the changes in a notebook cell on this PC with spark.read.option("
                                 "\"readChangeFeed\", \"true\").option(\"startingVersion\", 0).table(...)")
    if isinstance(statement, exp.Set) and any(i.args.get("kind") == "VARIABLE" for i in statement.expressions):
        raise _statement_refusal("SET VARIABLE", _VARIABLES, "write the value into the query")
    if isinstance(statement, exp.Command) and statement.name.upper() in _MAINTENANCE:
        word = statement.name.upper()
        raise _statement_refusal(word, f"{_MAINTENANCE[word]}, and {_LOCAL_TABLE}", _LEAVE_OUT)
    if isinstance(statement, exp.Update) and statement.args.get("from_") is not None:
        raise _databricks_stops("UPDATE ... FROM isn't Databricks SQL", "as a syntax error, because its UPDATE has "
                                "no FROM clause", "Write it as MERGE INTO ... USING ... WHEN MATCHED THEN UPDATE")
    if isinstance(statement, exp.Insert) and statement.args.get("overwrite"):
        raise _statement_refusal("INSERT OVERWRITE", "DuckDB has no INSERT OVERWRITE", "empty the table with "
                                 "TRUNCATE TABLE (or DELETE FROM ... WHERE for a partition), then INSERT INTO")
    if isinstance(statement, exp.Insert) and statement.args.get("where") is not None:
        raise _statement_refusal("INSERT INTO ... REPLACE WHERE", "DuckDB has no REPLACE WHERE", "DELETE FROM the "
                                 "table WHERE the same condition, then INSERT INTO")
    if isinstance(statement, exp.Alter) and str(statement.args.get("kind") or "").upper() == "TABLE":
        return _alter_columns(statement)
    if isinstance(statement, exp.Describe):
        return [_describe(statement)]
    if isinstance(statement, exp.Merge):
        return [_merge_matches_once(statement)]
    return [statement]


# Unity Catalog names (WR-316). A Databricks SQL cell here runs in one session database, which stands for the
# workspace's default catalog: main (and workspace, hive_metastore and spark_catalog, the other names a notebook
# uses for it) is this session, and its schema default is DuckDB's main schema. Any other catalog is a Unity
# Catalog catalog this PC doesn't hold, so a name in it is refused by name rather than read from somewhere else.
SESSION_CATALOGS = ("main", "workspace", "hive_metastore", "spark_catalog")
_DUCKDB_CATALOGS = {"system", "temp", "memory"}  # USE x in DuckDB would switch to one of its own catalogs


def _catalog_refusal(name: str, what: str | None = None) -> ValueError:
    others = SESSION_CATALOGS[1:]
    kind = ("a catalog of this PC's own engine, not one of the session's" if name.lower() in _DUCKDB_CATALOGS
            else "a Unity Catalog catalog")
    return _statement_refusal(what or f"A table in the catalog {name}",
                              f"{name} is {kind}, and this session holds only its own tables, in "
                              f"the catalog main ({', '.join(others[:-1])} and {others[-1]} name it too)",
                              "name the table as main.schema.table, schema.table or table")


def _main_schema_refusal(what: str) -> ValueError:
    """A schema named main: DuckDB's own name for the schema this session shows as default, so it can't be
    another schema here, and reading it as default would answer where Databricks finds no such schema."""
    return _statement_refusal(what, "this PC's engine already uses the name main for the schema shown as default",
                              "use default or another schema name")


def _default_schema(identifier: exp.Identifier | None) -> exp.Identifier | None:
    if identifier is not None and identifier.name.lower() == "default":
        return exp.to_identifier("main")
    return identifier


def _current_schema() -> exp.Expression:
    """Databricks' current_schema() (and current_database(), the same thing there): default for DuckDB's main."""
    return exp.Case(this=exp.CurrentSchema(), ifs=[exp.If(this=exp.Literal.string("main"),
                                                          true=exp.Literal.string("default"))],
                    default=exp.CurrentSchema())


def unity_names(statement: exp.Expression) -> list[exp.Expression]:
    """``statement`` with Unity Catalog names read as Databricks reads them: catalog.schema.table in the session's
    catalog, the schema default, current_catalog(), current_schema() and current_database(), and USE CATALOG,
    USE SCHEMA, USE DATABASE and USE. A name in another catalog is refused by name."""
    if isinstance(statement, exp.Use):
        return _use(statement)
    for table in list(statement.find_all(exp.Table)):
        if table.args.get("catalog") is not None:
            catalog = table.catalog.lower()
            if catalog == "system" and table.db.lower() == "information_schema":
                pass  # the session's own information_schema
            elif catalog in SESSION_CATALOGS:
                table.set("catalog", None)
            else:
                raise _catalog_refusal(table.catalog)
        if table.args.get("db") is not None and table.db.lower() == "main":
            raise _main_schema_refusal(f"The schema main in {'.'.join(p.name for p in table.parts)}")
        table.set("db", _default_schema(table.args.get("db")))
    for column in list(statement.find_all(exp.Column)):
        # main.default.t.id: the session's catalog and schema in front of a table's name. Only those are touched:
        # any other leading parts may be a table, an alias or a struct's fields (t.s.a.b), so they stay as written.
        column_catalog = column.args.get("catalog")
        if column_catalog is not None and column_catalog.name.lower() in SESSION_CATALOGS:
            column.set("catalog", None)
            column.set("db", _default_schema(column.args.get("db")))
        elif column_catalog is None and column.args.get("db") is not None and column.args["db"].name.lower() == "default":
            column.set("db", _default_schema(column.args.get("db")))
    for node in list(statement.find_all(exp.CurrentSchema, exp.CurrentDatabase, exp.CurrentCatalog)):
        node.replace(exp.Literal.string("main") if isinstance(node, exp.CurrentCatalog) else _current_schema())
    return [statement]


def _use(statement: exp.Use) -> list[exp.Expression]:
    """USE CATALOG main (or another name for this session's catalog) makes default the current schema, as on
    Databricks. USE [SCHEMA | DATABASE] [catalog.]schema makes it current for the cells after it, and a schema
    the session doesn't have stops the cell with Databricks' SCHEMA_NOT_FOUND."""
    kind = str(statement.args.get("kind") or "").upper()
    target = statement.this
    parts = [p for p in (target.args.get("catalog"), target.args.get("db"), target.this) if p is not None] \
        if isinstance(target, exp.Table) else [target]
    names = [p.name for p in parts]
    said = f"USE {kind + ' ' if kind else ''}{'.'.join(names)}"
    if kind == "CATALOG":
        if len(names) != 1:
            raise ValueError(f"[PARSE_SYNTAX_ERROR] {said}: USE CATALOG takes one catalog name.")
        if names[0].lower() not in SESSION_CATALOGS:
            raise _catalog_refusal(names[0], said)
        schema = "main"
    else:
        if len(names) > 2:
            raise ValueError(f"[PARSE_SYNTAX_ERROR] {said}: a schema is named as schema or catalog.schema.")
        if len(names) == 2 and names[0].lower() not in SESSION_CATALOGS:
            raise _catalog_refusal(names[0], said)
        if names[-1].lower() == "main":  # on Databricks USE main is USE SCHEMA main, not the catalog
            raise _main_schema_refusal(said)
        schema = "main" if names[-1].lower() == "default" else names[-1]
        if schema.lower() in _DUCKDB_CATALOGS:
            raise _statement_refusal(said, f"DuckDB keeps the name {schema} for one of its own catalogs",
                                     "give the schema another name")
    shown = "default" if schema == "main" else schema
    check = cast(exp.Expression, parse_one(
        "SELECT error(" + _quoted(f"[SCHEMA_NOT_FOUND] The schema `main`.`{shown}` cannot be found. Verify the "
                                  "spelling and correctness of the schema and catalog.") + ") "
        "WHERE NOT EXISTS (SELECT 1 FROM duckdb_schemas() WHERE database_name = current_database() "
        f"AND lower(schema_name) = lower({_quoted(schema)}))", read="duckdb"))
    return [check, exp.Use(this=exp.Table(this=exp.to_identifier(schema, quoted=True)))]
