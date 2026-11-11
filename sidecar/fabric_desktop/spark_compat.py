"""PySpark calls DuckDB's Spark API doesn't have yet, so Fabric and Databricks notebooks run here.

``spark`` on this PC is DuckDB's PySpark-compatible API (spark_local). It covers most DataFrame
work but leaves out window functions, ``df.agg``, ``selectExpr``, ``fillna``/``na``, ``pivot``
and a set of functions that are everyday Spark (``row_number``, ``explode``, ``date_format``...).
This module adds them on top of DuckDB SQL: each function is written as the Spark SQL call it
is and translated with sqlglot (the translator the SQL page uses), so its meaning follows Spark's.
Nothing here writes anywhere; it only builds queries. ``install()`` is called by spark_local.
"""

from __future__ import annotations

import builtins
import functools
import inspect
import itertools
import re
import sys
import types
from typing import Any, Callable, cast

import duckdb
import sqlglot
from duckdb.experimental.spark.sql import functions as F
from duckdb.experimental.spark.sql.column import Column
from duckdb.experimental.spark.sql.dataframe import DataFrame
from duckdb.experimental.spark.sql.group import GroupedData
from sqlglot import exp
from . import spark_builtins_sql
from . import sql as spark_sql
from .duckdb_rows import one_row

_ids = itertools.count()


def _sql(text: str) -> Column:
    return Column(duckdb.SQLExpression(text))


def _col_sql(c: Any) -> str:
    """DuckDB SQL for a column or a column name ("*" stays a star)."""
    if isinstance(c, Column):
        return str(c.expr)
    if c == "*":
        return "*"
    return '"' + str(c).replace('"', '""') + '"'


def spark_expr(text: str) -> str:
    """One Spark SQL expression as DuckDB SQL."""
    tree = spark_builtins_sql.mark(_parse(text))
    return spark_sql.duckdb_sql(spark_sql.spark_legacy_times(spark_builtins_sql.rewrite(spark_sql.spark_substring(
        spark_sql.spark_integer_casts(tree)))))


def _parse(text: str) -> exp.Expression:
    """One Spark SQL expression as sqlglot reads it (it types the result as its base Expr)."""
    return cast(exp.Expression, sqlglot.parse_one(text, read="spark"))


def _literal(text: str) -> Callable[[re.Match[str]], str]:
    """A re.sub replacement that puts ``text`` in as it is (a string would read its backslashes)."""
    return lambda _m: text


def _call(spark_sql: str, *cols: Any) -> Column:
    """A Spark SQL call with ``{0}``, ``{1}``... for columns, translated to DuckDB SQL."""
    holes = [f"__fdc{i}__" for i in range(len(cols))]
    text = spark_expr(spark_sql.format(*holes))
    for hole, c in zip(holes, cols, strict=True):
        text = re.sub(rf'"?{hole}"?', _literal(f"({_col_sql(c)})" if _col_sql(c) != "*" else "*"), text)
    return _sql(text)


def _name_of(c: Any) -> str:
    """A column's name, given its name or a plain column; a string literal is named by its text, as in Spark."""
    if isinstance(c, str):
        return c
    return _spark_text(c)


def _lit(value: Any) -> str:
    """A literal in Spark SQL, for _call (Spark escapes a quote as \\')."""
    return exp.convert(value).sql("spark")


def _duck_lit(value: Any) -> str:
    """A literal in DuckDB SQL (a quote doubled; a backslash is just a backslash)."""
    return exp.convert(value).sql("duckdb")


# ---- functions

def row_number() -> Column:
    return _sql("row_number()")


def rank() -> Column:
    return _sql("rank()")


def dense_rank() -> Column:
    return _sql("dense_rank()")


def percent_rank() -> Column:
    return _sql("percent_rank()")


def cume_dist() -> Column:
    return _sql("cume_dist()")


def ntile(n: int) -> Column:
    return _sql(f"ntile({int(n)})")


def lag(col: Any, offset: int = 1, default: Any = None) -> Column:
    return _call(f"lag({{0}}, {int(offset)}, {_lit(default)})", col)


def lead(col: Any, offset: int = 1, default: Any = None) -> Column:
    return _call(f"lead({{0}}, {int(offset)}, {_lit(default)})", col)


def explode(col: Any) -> Column:
    return _call("explode({0})", col)


def explode_outer(col: Any) -> Column:
    # A null or empty array gives one row with null, where explode gives none.
    return _sql(f"unnest(CASE WHEN len({_col_sql(col)}) > 0 THEN {_col_sql(col)} ELSE [NULL] END)")


def current_timestamp() -> Column:
    # The session's time zone is UTC (spark_local), as in Fabric; Spark's is a plain timestamp.
    return _sql("CAST(current_timestamp AS TIMESTAMP)")


def date_format(col: Any, format: str) -> Column:  # noqa: A002 - PySpark's name
    return _call(f"date_format({{0}}, {_lit(format)})", col)


def date_add(col: Any, days: Any) -> Column:
    n = _col_sql(days) if isinstance(days, Column) else int(days)
    return _sql(f"CAST(CAST({_col_sql(col)} AS DATE) + to_days(CAST({n} AS INTEGER)) AS DATE)")


def date_sub(col: Any, days: Any) -> Column:
    n = _col_sql(days) if isinstance(days, Column) else int(days)
    return _sql(f"CAST(CAST({_col_sql(col)} AS DATE) - to_days(CAST({n} AS INTEGER)) AS DATE)")


def datediff(end: Any, start: Any) -> Column:
    return _call("datediff({0}, {1})", end, start)


def months_between(date1: Any, date2: Any, roundOff: bool = True) -> Column:  # noqa: N803
    """Months from ``date2`` to ``date1``, counting the time of day and rounded to 8 digits unless
    ``roundOff`` is False, as in Spark."""
    flag = "true" if roundOff else "false"
    return _call(f"months_between({{0}}, {{1}}, {flag})", date1, date2).alias(
        f"months_between({_name_of(date1)}, {_name_of(date2)}, {flag})")


def from_unixtime(timestamp: Any, format: str = "yyyy-MM-dd HH:mm:ss") -> Column:  # noqa: A002
    return _call(f"from_unixtime({{0}}, {_lit(format)})", timestamp)


def unix_timestamp(timestamp: Any = None, format: str = "yyyy-MM-dd HH:mm:ss") -> Column:  # noqa: A002
    """Seconds since 1970 of a date, a timestamp or text in ``format``, as Spark names it."""
    if timestamp is None:
        return _sql("CAST(epoch(current_timestamp) AS BIGINT)").alias(f"unix_timestamp(current_timestamp(), {format})")
    return _call(f"unix_timestamp({{0}}, {_lit(format)})", timestamp).alias(f"unix_timestamp({_name_of(timestamp)}, {format})")


def size(col: Any) -> Column:
    return _call("size({0})", col)


def element_at(col: Any, extraction: Any) -> Column:
    """An array's element (1-based; index 0 is refused) or a map's value, as in Spark."""
    if isinstance(extraction, Column):
        return _call("element_at({0}, {1})", col, extraction).alias(
            f"element_at({_name_of(col)}, {_name_of(extraction)})")
    return _call(f"element_at({{0}}, {_lit(extraction)})", col).alias(f"element_at({_name_of(col)}, {extraction})")


def get_json_object(col: Any, path: str) -> Column:
    return _call(f"get_json_object({{0}}, {_lit(path)})", col)


def collect_set(col: Any) -> Column:
    return _call("collect_set({0})", col)


def count_distinct(col: Any, *cols: Any) -> Column:
    if cols:  # rows where any of the columns is null aren't counted, as in Spark
        every = [col, *cols]
        return _sql(f"count(DISTINCT CASE WHEN {' AND '.join(f'{_col_sql(c)} IS NOT NULL' for c in every)} "
                    f"THEN row({', '.join(_col_sql(c) for c in every)}) END)")
    return _call("count(DISTINCT {0})", col)


def sum_distinct(col: Any) -> Column:
    return _call("sum(DISTINCT {0})", col)


def max_by(col: Any, ord: Any) -> Column:  # noqa: A002 - PySpark's name
    return _call("max_by({0}, {1})", col, ord)


def min_by(col: Any, ord: Any) -> Column:  # noqa: A002
    return _call("min_by({0}, {1})", col, ord)


def monotonically_increasing_id() -> Column:
    # Increasing and unique, as Spark promises; Spark's values have gaps between partitions.
    return _sql("(row_number() OVER () - 1)")


def percentile_approx(col: Any, percentage: Any, accuracy: int = 10000) -> Column:
    if accuracy != 10000:
        print("Note: percentile_approx's accuracy is ignored on this PC; DuckDB's approximation is used.")
    return _call(f"percentile_approx({{0}}, {_lit(percentage)})", col)


def kurtosis(col: Any) -> Column:
    return _call("kurtosis({0})", col)


_TRUNC = {"year": "year", "yyyy": "year", "yy": "year", "quarter": "quarter", "month": "month", "mon": "month",
          "mm": "month", "week": "week"}


def trunc(date: Any, format: str) -> Column:  # noqa: A002
    unit = _TRUNC.get(format.lower())
    if unit is None:  # Spark gives null for a format it doesn't know
        return _sql("CAST(NULL AS DATE)")
    return _sql(f"CAST(date_trunc('{unit}', CAST({_col_sql(date)} AS DATE)) AS DATE)")


def next_day(date: Any, dayOfWeek: str) -> Column:  # noqa: N803
    return _call(f"next_day({{0}}, {_lit(dayOfWeek)})", date)


def from_utc_timestamp(timestamp: Any, tz: str) -> Column:
    return _call(f"from_utc_timestamp({{0}}, {_lit(tz)})", timestamp)


def to_utc_timestamp(timestamp: Any, tz: str) -> Column:
    return _call(f"to_utc_timestamp({{0}}, {_lit(tz)})", timestamp)


def sha1(col: Any) -> Column:
    return _call("sha1({0})", col)


def crc32(col: Any) -> Column:
    return _call("crc32({0})", col)


def xxhash64(*cols: Any) -> Column:
    return _call(f"xxhash64({', '.join('{%d}' % i for i in range(len(cols)))})", *cols)


def _spark_hash(*cols: Any) -> Column:
    """F.hash: Spark's hash (named apart here so this module keeps Python's own hash)."""
    return _call(f"hash({', '.join('{%d}' % i for i in range(len(cols)))})", *cols)


def uuid() -> Column:
    return _sql("CAST(uuid() AS VARCHAR)")


def nanvl(col1: Any, col2: Any) -> Column:
    return _sql(f"CASE WHEN isnan({_col_sql(col1)}) THEN {_col_sql(col2)} ELSE {_col_sql(col1)} END")


def format_number(col: Any, d: int) -> Column:
    """#,###,###.## rounded half-even to d places, as Spark's DecimalFormat does. A decimal or an
    integer is rounded by its value. A double is rounded from the digits Java prints for it, padded with
    zeros past them: its shortest digits (2.345, not 2.34500000000000019...), but the exact whole number for
    a whole double from 2**53 to 2**62 (Java prints those). When those digits fall on a tie at
    the last place, the double's exact binary value decides (2.345 is a little above 2.345, so 2.35;
    2.675 a little below, so 2.67; 0.125 is exactly a tie, so 0.12). DuckDB's round_even does none of
    this the same on 1.x and 2.0, so it is done on exact decimals by hand and printed from their digits."""
    x, d = _col_sql(col), int(d)
    if not 0 <= d <= 18:
        return _sql(f"printf('%,.{max(d, 0)}f', {x})")
    double = f"CAST({x} AS DOUBLE)"
    negative = f"({x} < 0 OR ({x} = 0 AND CAST({double} AS VARCHAR) LIKE '-%'))"  # -0.0 prints -0, as in Java
    unit = f"CAST('1e-{d}' AS DECIMAL(38, 20))"
    # printf prints a double's exact digits; a VARCHAR, its shortest ones.
    exact = f"CAST(printf('%.20f', {double}) AS DECIMAL(38, 20))"
    shortest = f"CAST(CAST({double} AS VARCHAR) AS DECIMAL(38, 20))"
    # Which side of its shortest digits the exact value lies (-1, 0, 1). Below 1, 37 places show it.
    side = (f"CASE WHEN abs({double}) < 1 THEN sign(CAST(printf('%.37f', {double}) AS DECIMAL(38, 37)) - "
            f"CAST(CAST({double} AS VARCHAR) AS DECIMAL(38, 37))) ELSE sign({exact} - {shortest}) END")
    near = f"round({shortest}, {d})"  # half away from zero
    tie = f"abs({shortest} - {near}) * 2 = {unit}"
    from_shortest = (f"CASE WHEN NOT ({tie}) THEN {near} WHEN {side} * sign({double}) > 0 THEN {near} "
                     f"WHEN {side} * sign({double}) < 0 THEN {near} - sign({double}) * {unit} "
                     f"ELSE {_half_even(shortest, d)} END")
    whole = f"CAST(printf('%.0f', {double}) AS DECIMAL(38, 0))"  # from 2**53 to 2**62 (a whole double), its exact digits
    whole_shortest = f"CAST(CAST({double} AS VARCHAR) AS DECIMAL(38, 0))"  # from 2**62, its shortest digits
    # Beyond the exact decimals DuckDB's own rounding prints it, as before. NaN and infinity print as
    # DecimalFormat prints them.
    big = f"printf('%,.{d}f', round_even({x}, {d}))"
    special = (f"WHEN isnan({double}) THEN 'NaN' WHEN isinf({double}) THEN "
               f"CASE WHEN {x} < 0 THEN '-∞' ELSE '∞' END")
    return _sql(f"CASE WHEN typeof({x}) IN ('DOUBLE', 'FLOAT') THEN (CASE {special} "
                f"WHEN abs({x}) >= 9007199254740992 AND abs({x}) < 4611686018427387904 THEN {_printed(whole, d, negative)} "
                f"WHEN abs({x}) < 1e17 THEN {_printed(from_shortest, d, negative)} "
                f"WHEN abs({x}) < 1e37 THEN {_printed(whole_shortest, d, negative)} ELSE {big} END) "
                f"WHEN abs({double}) < 1e20 THEN "
                f"{_printed(_half_even(f'CAST({x} AS DECIMAL(38, 18))', d), d, negative)} ELSE {big} END")


def _half_even(v: str, d: int) -> str:
    """``v``, an exact decimal, rounded half-even to ``d`` places (a decimal). A NULL stays NULL."""
    unit = f"CAST('1e-{d}' AS DECIMAL(38, 20))"
    r = f"round({v}, {d})"  # half away from zero, exact on a decimal
    tie_to_odd = f"abs({v} - {r}) * 2 = {unit} AND CAST({r} * CAST('1e{d}' AS DECIMAL(38, 0)) AS HUGEINT) % 2 <> 0"
    return f"CASE WHEN {tie_to_odd} THEN {r} - sign({v}) * {unit} ELSE {r} END"


def _printed(rounded: str, d: int, negative: str) -> str:
    """``rounded``, a decimal with no more than ``d`` places, with thousands separators and ``d``
    places, from its digits rather than through a double, so all of them are exact (BIGINT's largest is
    9,223,372,036,854,775,807). A negative value that rounds to zero keeps its sign, as DecimalFormat
    prints it (-0.25 -> -0)."""
    whole = f"trunc(abs({rounded}))"
    places = "" if d == 0 else (f" || '.' || lpad(CAST(CAST((abs({rounded}) - {whole}) * CAST('1e{d}' AS "
                                f"DECIMAL(38, 0)) AS HUGEINT) AS VARCHAR), {d}, '0')")
    return f"CASE WHEN {negative} THEN '-' ELSE '' END || format('{{:,}}', CAST({whole} AS HUGEINT)){places}"


def to_json(col: Any, options: Any = None) -> Column:
    if options:
        print(f"Note: to_json options ({', '.join(options)}) are ignored on this PC.")
    return _call("to_json({0})", col)


def substring(str: Any, pos: int, len: int) -> Column:  # noqa: A002
    """Position 0 counts as 1, as in Spark."""
    return _call(f"substring({{0}}, {int(pos)}, {int(len)})", str).alias(f"substring({_name_of(str)}, {int(pos)}, {int(len)})")


def concat(*cols: Any) -> Column:
    """NULL when any input is NULL, as in Spark (DuckDB's concat skips NULLs)."""
    return _call(f"concat({', '.join('{%d}' % i for i in range(len(cols)))})", *cols).alias(
        f"concat({', '.join(_name_of(c) for c in cols)})")


def slice(x: Any, start: Any, length: Any) -> Column:  # noqa: A001
    """``length`` elements from ``start`` (1-based; negative counts from the end), as in Spark."""
    cols = [x] + [v for v in (start, length) if not isinstance(v, int)]
    holes = iter(f"{{{i}}}" for i in range(1, len(cols)))
    args = ", ".join(str(v) if isinstance(v, int) else next(holes) for v in (start, length))
    return _call(f"slice({{0}}, {args})", *cols).alias(
        f"slice({_name_of(x)}, {', '.join(str(v) if isinstance(v, int) else _name_of(v) for v in (start, length))})")


def add_months(start: Any, months: Any) -> Column:
    """A date ``months`` months after ``start``, also for text or a timestamp, as in Spark."""
    if isinstance(months, int):
        return _call(f"add_months({{0}}, {months})", start).alias(f"add_months({_name_of(start)}, {months})")
    return _call("add_months({0}, {1})", start, months).alias(
        f"add_months({_name_of(start)}, {_name_of(months)})")


def initcap(col: Any) -> Column:
    """Each word's first letter upper case and the rest lower case, as in Spark (DuckDB's initcap
    only upper-cases first letters)."""
    return _call("initcap({0})", col).alias(f"initcap({_name_of(col)})")


def expr(text: str) -> Column:
    """A Spark SQL expression, read as spark.sql reads one. ``AS name`` names the column;
    otherwise it is named as Spark names the expression, not after its DuckDB SQL."""
    read = _parse(text)
    # as spark.sql reads it, in a SELECT (a rewrite replaces a node in its parent)
    select = spark_sql.spark_legacy_times(spark_sql.spark_fixes(spark_sql.spark_legacy_casts(exp.select(read.copy()))))
    item = cast(exp.Select, select).expressions[0]
    body = item.this if isinstance(item, exp.Alias) else item
    if isinstance(read, exp.Alias):
        return _sql(spark_sql.duckdb_sql(body)).alias(read.alias)
    col = _sql(spark_sql.duckdb_sql(body))
    if isinstance(read, (exp.Column, exp.Star)):
        return col
    shown = read.copy()  # Spark's name drops a string literal's quotes
    for literal in list(shown.find_all(exp.Literal)):
        if literal.is_string:
            if literal is shown:
                return _named(col, literal.this)
            literal.replace(exp.Var(this=literal.this))
    return _named(col, spark_sql.spark_name(shown))


def to_date(col: Any, format: str | None = None) -> Column:  # noqa: A002 - PySpark's name
    """A date from a date, timestamp or text, in ``format`` when given. Text that isn't a date gives
    NULL, as in Spark with ANSI off (EA-015); DuckDB's own to_date stops the query, and refuses a format."""
    named = f"to_date({_name_of(col)}" + ("" if format is None else f", {format}") + ")"
    call = "to_date({0})" if format is None else f"to_date({{0}}, {_lit(format)})"
    return _call(call, col).alias(named)


def to_timestamp(col: Any, format: str | None = None) -> Column:  # noqa: A002
    """A timestamp from a date, timestamp or text, in ``format`` when given. Text that isn't one gives
    NULL, as in Spark with ANSI off (EA-015)."""
    named = f"to_timestamp({_name_of(col)}" + ("" if format is None else f", {format}") + ")"
    call = "to_timestamp({0})" if format is None else f"to_timestamp({{0}}, {_lit(format)})"
    return _call(call, col).alias(named)


_count = F.count
_first, _last = F.first, F.last


def first(col: Any, ignorenulls: bool = False) -> Column:
    """``ignorenulls=True`` gives the first value that isn't NULL, as in Spark."""
    if not ignorenulls:
        return _first(col)
    q = _col_sql(col)
    return _sql(f"first({q}) FILTER (WHERE {q} IS NOT NULL)").alias(f"first({_name_of(col)})")


def last(col: Any, ignorenulls: bool = False) -> Column:
    """``ignorenulls=True`` gives the last value that isn't NULL, as in Spark."""
    if not ignorenulls:
        return _last(col)
    q = _col_sql(col)
    return _sql(f"last({q}) FILTER (WHERE {q} IS NOT NULL)").alias(f"last({_name_of(col)})")


def count(col: Any) -> Column:
    """``count("*")`` counts rows, as in Spark."""
    return _sql("count(*)") if isinstance(col, str) and col == "*" else _count(col)


def _arg(value: Any) -> str:
    """DuckDB SQL for a function argument: a name is a column, as in PySpark; a number is a literal."""
    return _col_sql(value) if isinstance(value, str) else _operand_sql(value)


def _text(value: str) -> str:
    """A Python string as a DuckDB string literal."""
    return str(duckdb.ConstantExpression(str(value)))


def bool_and(col: Any) -> Column:
    return _sql(f"bool_and({_arg(col)})")


def every(col: Any) -> Column:
    return bool_and(col)


def bool_or(col: Any) -> Column:
    return _sql(f"bool_or({_arg(col)})")


def some(col: Any) -> Column:
    return bool_or(col)


def count_if(col: Any) -> Column:
    return _sql(f"CAST(coalesce(count_if({_arg(col)}), 0) AS BIGINT)")


def bit_and(col: Any) -> Column:
    return _sql(f"bit_and({_arg(col)})")


def bit_or(col: Any) -> Column:
    return _sql(f"bit_or({_arg(col)})")


def bit_xor(col: Any) -> Column:
    return _sql(f"bit_xor({_arg(col)})")


def _positive_log(col: Any, sql: str, above: str = "0") -> Column:
    """A logarithm, NULL where Spark's is (at or below ``above``); DuckDB's stops the query there."""
    x = f"CAST({_arg(col)} AS DOUBLE)"
    return _sql(f"(CASE WHEN {x} > {above} THEN {sql.replace('{x}', x)} END)")


def ln(col: Any) -> Column:
    return _positive_log(col, "ln({x})")


def log(arg1: Any, arg2: Any = None) -> Column:
    """``log(col)`` is the natural logarithm; ``log(base, col)`` the logarithm in ``base``. NULL at or
    below zero, as in Spark."""
    if arg2 is None:
        return ln(arg1)
    base = f"CAST({_arg(arg1)} AS DOUBLE)"
    return _positive_log(arg2, f"(CASE WHEN {base} > 0 THEN ln({{x}}) / ln({base}) END)")


def log10(col: Any) -> Column:
    return _positive_log(col, "log10({x})")


def log2(col: Any) -> Column:
    return _positive_log(col, "(ln({x}) / ln(2.0))")  # Spark's: log(x) / log(2)


def log1p(col: Any) -> Column:
    # ln(1 + x), accurate for a tiny x too: ln(u) * x / (u - 1) with u = 1 + x
    return _positive_log(col, "(CASE WHEN 1 + {x} = 1 THEN {x} ELSE ln(1 + {x}) * {x} / ((1 + {x}) - 1) END)", "-1")


def pow(col1: Any, col2: Any) -> Column:  # noqa: A001 - PySpark's name
    return _sql(f"pow(CAST({_arg(col1)} AS DOUBLE), CAST({_arg(col2)} AS DOUBLE))")


def power(col1: Any, col2: Any) -> Column:
    return pow(col1, col2)


def factorial(col: Any) -> Column:
    """n! as a BIGINT, NULL below 0 or above 20, as in Spark."""
    x = _arg(col)
    return _sql(f"(CASE WHEN {x} BETWEEN 0 AND 20 THEN CAST(factorial(CAST({x} AS INTEGER)) AS BIGINT) END)")


def bin(col: Any) -> Column:  # noqa: A001 - PySpark's name
    """The value as a whole number in binary; a negative one in 64-bit two's complement, as in Spark."""
    return _sql(f"bin(CAST(trunc({_arg(col)}) AS BIGINT))")


def locate(substr: str, str: Any, pos: int = 1) -> Column:  # noqa: A002 - PySpark's name
    """The 1-based place of ``substr`` in ``str`` from ``pos`` on, 0 when it isn't there, as in Spark."""
    s, pos = _arg(str), int(pos)
    if pos < 1 or substr == "":  # Spark: 0 before the first character; an empty substr is found at 1
        return _sql(f"(CASE WHEN {s} IS NOT NULL THEN {0 if pos < 1 else 1} END)")
    found = f"instr(substring({s}, {pos}), {_text(substr)})"
    return _sql(f"CAST((CASE WHEN {found} = 0 THEN 0 ELSE {found} + {pos - 1} END) AS INTEGER)")


def substring_index(str: Any, delim: str, count: int) -> Column:  # noqa: A002 - PySpark's name
    """The text before the ``count``-th ``delim`` (after it, counting from the end, when negative)."""
    s, count = _arg(str), int(count)
    if count == 0 or delim == "":
        return _sql(f"(CASE WHEN {s} IS NOT NULL THEN '' END)")
    parts = f"string_split({s}, {_text(delim)})"
    picked = f"{parts}[1:{count}]" if count > 0 else f"{parts}[{count}:]"
    return _sql(f"array_to_string({picked}, {_text(delim)})")


def translate(srcCol: Any, matching: str, replace: str) -> Column:  # noqa: N803 - PySpark's name
    return _sql(f"translate({_arg(srcCol)}, {_text(matching)}, {_text(replace)})")


def char_length(str: Any) -> Column:  # noqa: A002 - PySpark's name
    return _sql(f"CAST(length(CAST({_arg(str)} AS VARCHAR)) AS INTEGER)")


def character_length(str: Any) -> Column:  # noqa: A002
    return char_length(str)


def split(str: Any, pattern: str, limit: int = -1) -> Column:  # noqa: A002 - PySpark's name
    """``str`` split around matches of the regex ``pattern``; with ``limit`` above 0, at most that many
    parts, the last holding the rest, as in Spark. An empty pattern splits into characters."""
    s, limit = _arg(str), int(limit)
    if pattern == "":
        chars = f"string_split({s}, '')"
        return _sql(chars if limit <= 0 else f"{chars}[1:{limit}]")
    parts = f"regexp_split_to_array({s}, {_text(pattern)})"
    if limit <= 0:
        return _sql(parts)
    seps = f"regexp_extract_all({s}, {_text(pattern)})"
    before = (f"coalesce(list_sum(list_transform({parts}[1:{limit - 1}], lambda kf_p: length(kf_p))), 0) + "
              f"coalesce(list_sum(list_transform({seps}[1:{limit - 1}], lambda kf_p: length(kf_p))), 0)")
    return _sql(f"(CASE WHEN {s} IS NULL THEN NULL WHEN len({parts}) <= {limit} THEN {parts} "
                f"ELSE list_concat({parts}[1:{limit - 1}], [substring({s}, CAST({before} + 1 AS BIGINT))]) END)")


def isnan(col: Any) -> Column:
    """Whether the value is NaN: false, not NULL, for a NULL, as in Spark."""
    return _sql(f"coalesce(isnan(CAST({_arg(col)} AS DOUBLE)), false)")


def _over_columns(template: exp.Expression, cols: Any) -> Column:
    """A Spark SQL rewrite (fj_arg0, fj_arg1, ... for the columns) written as DuckDB SQL."""
    args = [cast(exp.Expression, sqlglot.parse_one(_col_sql(c), read="duckdb")) for c in cols]
    return _sql(spark_sql.duckdb_sql(spark_builtins_sql._with_args(template, args)))


def format_string(format: str, *cols: Any) -> Column:  # noqa: A002 - PySpark's name
    """Java's String.format over the columns, as in Spark: a NULL prints as null (false for %b)."""
    return _over_columns(cast(exp.Expression, sqlglot.parse_one(
        spark_builtins_sql.java_format_sql(format, len(cols)), read="duckdb")), cols)


def printf(format: Any, *cols: Any) -> Column:  # noqa: A002 - PySpark's name
    """format_string, with the format as a literal Column (F.lit("%s")) or as text."""
    if isinstance(format, Column):
        text = str(format.expr)
        if not (text.startswith("'") and text.endswith("'")):
            raise TypeError("printf() takes its format as text or F.lit(...) on this PC, not a column.")
        format = text[1:-1].replace("''", "'")  # noqa: A001
    return format_string(format, *cols)


def conv(col: Any, fromBase: int, toBase: int) -> Column:  # noqa: N803 - PySpark's names
    """A number written in ``fromBase`` rewritten in ``toBase`` (negative: signed), as in Spark."""
    template = spark_sql._from_template(spark_sql._ANON_TEMPLATES["CONV"],
                                        [exp.column("fj_arg0"), exp.Literal.number(int(fromBase)),
                                         exp.Literal.number(int(toBase))])
    return _over_columns(template, [col])


def _seeded(seed: int, salt: int) -> str:
    """A number in [0, 1) for each row, the same each run for a seed: the row's place hashed with it."""
    return f"(CAST(hash(row_number() OVER (), {int(seed)}, {salt}) >> 11 AS DOUBLE) / 9007199254740992.0)"


def rand(seed: int | None = None) -> Column:
    """Uniform in [0, 1). A seed gives the same numbers each run on this PC (not Spark's numbers)."""
    return _sql("random()" if seed is None else _seeded(seed, 0))


def randn(seed: int | None = None) -> Column:
    """Standard normal. A seed gives the same numbers each run on this PC (not Spark's numbers)."""
    u1, u2 = ("(1.0 - random())", "random()") if seed is None else (f"(1.0 - {_seeded(seed, 1)})", _seeded(seed, 2))
    return _sql(f"(sqrt(-2.0 * ln({u1})) * cos(2.0 * pi() * {u2}))")


FUNCTIONS = {name: obj for name, obj in list(globals().items())
             if callable(obj) and not name.startswith("_") and getattr(obj, "__module__", "") == __name__
             and name not in ("spark_expr", "install")}
FUNCTIONS |= {"countDistinct": count_distinct, "sumDistinct": sum_distinct, "count": count, "hash": _spark_hash}


# ---- windows

class WindowSpec:
    """``Window.partitionBy(...).orderBy(...).rowsBetween(...)``, used with ``Column.over``."""

    def __init__(self, partition: tuple = (), order: tuple = (), frame: str = ""):
        self._partition, self._order, self._frame = partition, order, frame

    def partitionBy(self, *cols: Any) -> WindowSpec:  # noqa: N802
        return WindowSpec(_flat(cols), self._order, self._frame)

    def orderBy(self, *cols: Any) -> WindowSpec:  # noqa: N802
        return WindowSpec(self._partition, _flat(cols), self._frame)

    def rowsBetween(self, start: int, end: int) -> WindowSpec:  # noqa: N802
        return WindowSpec(self._partition, self._order, f"ROWS BETWEEN {_bound(start)} AND {_bound(end)}")

    def rangeBetween(self, start: int, end: int) -> WindowSpec:  # noqa: N802
        return WindowSpec(self._partition, self._order, f"RANGE BETWEEN {_bound(start)} AND {_bound(end)}")

    def sql(self) -> str:
        parts = []
        if self._partition:
            parts.append("PARTITION BY " + ", ".join(_col_sql(c) for c in self._partition))
        if self._order:
            parts.append("ORDER BY " + ", ".join(f"{_col_sql(c)} {_order_of(c)}"
                                                 for c in self._order))
        if self._frame:
            parts.append(self._frame)
        return " ".join(parts)


class Window:
    unboundedPreceding = -sys.maxsize - 1  # noqa: N815 - PySpark's names
    unboundedFollowing = sys.maxsize  # noqa: N815
    currentRow = 0  # noqa: N815

    @staticmethod
    def partitionBy(*cols: Any) -> WindowSpec:  # noqa: N802
        return WindowSpec().partitionBy(*cols)

    @staticmethod
    def orderBy(*cols: Any) -> WindowSpec:  # noqa: N802
        return WindowSpec().orderBy(*cols)

    @staticmethod
    def rowsBetween(start: int, end: int) -> WindowSpec:  # noqa: N802
        return WindowSpec().rowsBetween(start, end)

    @staticmethod
    def rangeBetween(start: int, end: int) -> WindowSpec:  # noqa: N802
        return WindowSpec().rangeBetween(start, end)


def _flat(cols: tuple) -> tuple:
    return tuple(cols[0]) if len(cols) == 1 and isinstance(cols[0], (list, tuple)) else cols


def _bound(n: int) -> str:
    if n <= Window.unboundedPreceding:
        return "UNBOUNDED PRECEDING"
    if n >= Window.unboundedFollowing:
        return "UNBOUNDED FOLLOWING"
    if n == 0:
        return "CURRENT ROW"
    return f"{abs(int(n))} {'PRECEDING' if n < 0 else 'FOLLOWING'}"


# Spark's sort orders. DuckDB's sort expressions don't print their direction, so a window's
# ORDER BY reads it from here.
_ORDERS = {"asc": "ASC NULLS FIRST", "desc": "DESC NULLS LAST", "asc_nulls_first": "ASC NULLS FIRST",
           "asc_nulls_last": "ASC NULLS LAST", "desc_nulls_first": "DESC NULLS FIRST", "desc_nulls_last": "DESC NULLS LAST"}


def _order_of(c: Any) -> str:
    """The sort order ``.asc()``/``.desc()`` set on ``c``. Read from the instance itself: DuckDB's
    ``Column.__getattr__`` answers any other name with a struct-field column, so getattr's default
    would never be used."""
    return vars(c).get("_fd_order", "ASC NULLS FIRST") if isinstance(c, Column) else "ASC NULLS FIRST"


# A column's name with DuckDB's relation qualifier (unnamed_relation_<hex>.age) left out, as Spark shows it.
# DuckDB's own name for a frame: unnamed_relation_<hex>, or arrow_object_<hex> for one read from Arrow
# (a polars or pyarrow frame, or inferred rows)
_RELATION = re.compile(r'"?\b(?:unnamed_relation|arrow_object)_[0-9a-f]+"?\.')  # quoted on DuckDB 2.0
# A quoted identifier ("name", quoted as a keyword) or a string literal ('x') in DuckDB's name, read
# left to right in one pass so a quote inside one never pairs with the next: Spark writes each bare.
_QUOTED = re.compile(r'"((?:[^"]|"")*)"|\'((?:[^\']|\'\')*)\'')


def _named(col: Column, name: str) -> Column:
    """``col`` carrying Spark's name for it, which select and agg give it (see _spark_named_cols). An
    alias would name it too, but DuckDB writes an alias inside a function's arguments as name := ...,
    which would then show in the name of any function around it."""
    col._fd_name = name  # type: ignore[attr-defined]
    return col


def _spark_named_cols(cols: Any) -> Any:
    """Columns given to select or agg, each one that carries Spark's name aliased to it."""
    return [c.alias(vars(c).get("_fd_top") or vars(c)["_fd_name"]) if isinstance(c, Column) and vars(c).get("_fd_name")
            else c for c in cols]


def _field_names(frame: Any, cols: Any) -> Any:
    """``col[key]`` and ``col.key`` are named col.key when col is a struct (its field) and col[key] when
    it's a map, as in Spark; which one is known only from the frame's types, here."""
    relation = getattr(frame, "relation", None) or getattr(getattr(frame, "_df", None), "relation", None)
    for c in cols:
        field = vars(c).get("_fd_field") if isinstance(c, Column) else None
        if field is None or relation is None or "_fd_top" in vars(c):
            continue
        base, key = field
        try:
            kind = str(relation.project(_col_sql(base)).types[0])
        except duckdb.Error:  # the frame can't read it; the select itself says why
            continue
        if kind.upper().startswith("STRUCT"):
            c._fd_name = f"{_spark_text(base)}.{key}"
    return cols


def _naming(fn: Any) -> Any:
    """DataFrame.select, DataFrame.agg and GroupedData.agg, giving their columns Spark's names."""
    def call(self: Any, *cols: Any) -> Any:
        if len(cols) == 1 and isinstance(cols[0], list):
            return fn(self, _spark_named_cols(_field_names(self, cols[0])))
        return fn(self, *_spark_named_cols(_field_names(self, list(cols))))
    call.__name__ = getattr(fn, "__name__", "call")
    call.__doc__ = fn.__doc__
    return call


def _unquote(m: re.Match[str]) -> str:
    return m.group(1).replace('""', '"') if m.group(1) is not None else m.group(2).replace("''", "'")


def _spark_text(value: Any) -> str:
    """``value`` as Spark writes it inside a generated column name: a column by its name, a string as
    is, None as NULL, a double as Java writes it."""
    if isinstance(value, Column):
        if (name := vars(value).get("_fd_name")) is not None:
            return str(name)
        return _QUOTED.sub(_unquote, _RELATION.sub("", value.expr.get_name()))
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _java_double(value)
    return str(value)


def _operand_sql(value: Any) -> str:
    """``value`` as DuckDB SQL: a column's own expression, or a literal (NaN and infinities spelled out,
    which DuckDB's constant would print as NULL)."""
    if isinstance(value, Column):
        return _col_sql(value)
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return f"CAST('{value!r}' AS DOUBLE)"
    return str(duckdb.ConstantExpression(value))


def _between(self: Column, lowerBound: Any, upperBound: Any) -> Column:  # noqa: N803 - PySpark's names
    """Column.between: lower <= x <= upper, both ends included, named as Spark names it."""
    me, lo, hi = _operand_sql(self), _operand_sql(lowerBound), _operand_sql(upperBound)
    name = f"(({_spark_text(self)} >= {_spark_text(lowerBound)}) AND ({_spark_text(self)} <= {_spark_text(upperBound)}))"
    return _named(_sql(f"(({me} >= {lo}) AND ({me} <= {hi}))"), name)


def _substr(self: Column, startPos: Any, length: Any) -> Column:  # noqa: N803 - PySpark's names
    """Column.substr: ``length`` characters from 1-based ``startPos`` (Spark's substring), both ints or
    both Columns, as PySpark requires."""
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (startPos, length))
    if not ints and not (isinstance(startPos, Column) and isinstance(length, Column)):
        raise TypeError(f"startPos and length must both be ints or both be Columns, got "
                        f"{type(startPos).__name__} and {type(length).__name__}")
    name = f"substring({_spark_text(self)}, {_spark_text(startPos)}, {_spark_text(length)})"
    if ints:
        return _named(_call(f"substring({{0}}, {startPos}, {length})", self), name)
    return _named(_call("substring({0}, {1}, {2})", self, startPos, length), name)


def _bitwise(operator: str) -> Any:
    """Column.bitwiseAND, bitwiseOR and bitwiseXOR, named as Spark names them: (a & b)."""
    sql = {"&": "({a} & {b})", "|": "({a} | {b})", "^": "xor({a}, {b})"}[operator]

    def bitwise(self: Column, other: Any) -> Column:
        text = sql.format(a=_operand_sql(self), b=_operand_sql(other))
        return _named(_sql(text), f"({_spark_text(self)} {operator} {_spark_text(other)})")
    return bitwise


def _eq_null_safe(self: Column, other: Any) -> Column:
    """Column.eqNullSafe: equal, with NULL equal to NULL (Spark's <=>), never NULL itself."""
    text = f"({_operand_sql(self)} IS NOT DISTINCT FROM {_operand_sql(other)})"
    return _named(_sql(text), f"({_spark_text(self)} <=> {_spark_text(other)})")


def _get_item(self: Column, key: Any) -> Column:
    """``col.getItem(key)`` and ``col[key]``: a whole number is an array's 0-based position (NULL
    outside the array, as Spark gives without ANSI mode); text is a map's key or a struct's field."""
    if isinstance(key, Column):
        raise NotImplementedError("getItem with a column doesn't run on this PC yet: "
                                  "use F.element_at(col, position + 1) for an array, or a map's key as text")
    if isinstance(key, bool) or not isinstance(key, (int, str)):
        raise TypeError(f"getItem takes a whole number or text, not {type(key).__name__}")
    if isinstance(key, int):
        return _named(_sql(f"({_col_sql(self)})[{key + 1 if key >= 0 else 0}]"), f"{_spark_text(self)}[{key}]")
    if "_fd_struct" in vars(self):  # a field of a withField/dropFields struct, worked out with it
        from .spark_struct_fields import field_of
        return field_of(self, key)
    out = _named(_sql(f"({_col_sql(self)})[{_duck_lit(key)}]"), f"{_spark_text(self)}[{key}]")
    out._fd_field = (self, key)  # type: ignore[attr-defined]  # a struct's field is named col.key; see _field_names
    return out


def _getitem(original: Any) -> Any:
    def getitem(self: Column, key: Any) -> Column:
        return original(self, key) if isinstance(key, builtins.slice) else _get_item(self, key)
    return getitem


def _get_field(self: Column, name: str) -> Column:
    """``col.getField(name)``: a struct's field, named ``col.name`` as in Spark."""
    if not isinstance(name, str):
        raise TypeError(f"getField takes a field name, not {type(name).__name__}")
    if "_fd_struct" in vars(self):
        from .spark_struct_fields import field_of
        return field_of(self, name)
    return _named(_sql(f"({_col_sql(self)})[{_duck_lit(name)}]"), f"{_spark_text(self)}.{name}")


def _ordered(name: str, fn: Any) -> Any:
    def order(self: Column) -> Column:
        out = fn(self)
        out._fd_order = _ORDERS[name]
        return out
    return order


# DuckDB 1.x prints "first"(x) FILTER (WHERE (x IS NOT NULL)); 2.0 drops the quotes and the inner parentheses.
_IGNORING_NULLS = re.compile(r'^"?(first|last)"?\((.*)\) FILTER \(WHERE (\()?\2 IS NOT NULL(?(3)\))\)$', re.S)


def _over(self: Column, window: WindowSpec) -> Column:
    text = str(self.expr)
    if m := _IGNORING_NULLS.match(text):  # F.first(x, True).over(w): a window takes IGNORE NULLS, not FILTER
        text = f"{m.group(1)}_value({m.group(2)} IGNORE NULLS)"
    return _sql(f"{text} OVER ({window.sql()})")


# ---- Spark's generated column names
# A Column made by an operator, a CASE or a null check is named as Spark names it ((age > 3),
# CASE WHEN (age > 3) THEN 1 ELSE 0 END, (age IS NULL)); DuckDB's own names carry its relation
# (unnamed_relation_<hex>.age) and its SQL spelling. Only the name changes: str(col.expr), which
# the SQL is built from, leaves the alias out.

# Spark's text for each operator: how the two sides (self, other) are written.
_OPERATORS = {
    "__eq__": "({a} = {b})", "__ne__": "(NOT ({a} = {b}))", "__lt__": "({a} < {b})", "__le__": "({a} <= {b})",
    "__gt__": "({a} > {b})", "__ge__": "({a} >= {b})", "__add__": "({a} + {b})", "__sub__": "({a} - {b})",
    "__mul__": "({a} * {b})", "__truediv__": "({a} / {b})", "__div__": "({a} / {b})", "__mod__": "({a} % {b})",
    "__pow__": "POWER({a}, {b})", "__and__": "({a} AND {b})", "__or__": "({a} OR {b})",
    # PySpark's reverse operators: + * AND OR keep the column first, the others put the value first
    "__radd__": "({a} + {b})", "__rmul__": "({a} * {b})", "__rand__": "({a} AND {b})", "__ror__": "({a} OR {b})",
    "__rsub__": "({b} - {a})", "__rtruediv__": "({b} / {a})", "__rdiv__": "({b} / {a})", "__rmod__": "({b} % {a})",
    "__rpow__": "POWER({b}, {a})",
}
_UNARY = {"__neg__": "(- {a})", "__invert__": "(NOT {a})", "isNull": "({a} IS NULL)",
          "isNotNull": "({a} IS NOT NULL)"}


def _spark_named(name: str, fn: Any) -> Any:
    template = _OPERATORS.get(name) or _UNARY[name]

    def named(self: Column, *other: Any) -> Any:
        out = fn(self, *other)
        if not isinstance(out, Column):  # NotImplemented, for Python to try the other side
            return out
        return _named(out, template.format(a=_spark_text(self), b=_spark_text(other[0]) if other else ""))
    named.__name__ = name
    named.__doc__ = fn.__doc__
    return named


def _isin_named(fn: Any) -> Any:
    def isin(self: Column, *cols: Any) -> Column:
        values = cols[0] if len(cols) == 1 and isinstance(cols[0], (list, set, tuple)) else cols
        return _named(fn(self, *cols), f"({_spark_text(self)} IN ({', '.join(_spark_text(v) for v in values)}))")
    isin.__doc__ = fn.__doc__
    return isin


def _case_named(fn: Any) -> Any:
    """F.when: CASE WHEN c THEN v END."""
    def when(condition: Any, value: Any) -> Column:
        return _named(fn(condition, value), f"CASE WHEN {_spark_text(condition)} THEN {_spark_text(value)} END")
    when.__doc__ = fn.__doc__
    return when


def _case_step(fn: Any, kind: str) -> Any:
    """Column.when and Column.otherwise on a CASE: its name gains WHEN c THEN v, or ELSE v."""
    def step(self: Column, *args: Any) -> Column:
        out = fn(self, *args)
        name = _spark_text(self)
        if not (name.startswith("CASE WHEN ") and name.endswith(" END")):
            return out
        part = f"WHEN {_spark_text(args[0])} THEN {_spark_text(args[1])}" if kind == "when" else f"ELSE {_spark_text(args[0])}"
        return _named(out, f"{name[:-4]} {part} END")
    step.__doc__ = fn.__doc__
    return step


# F functions whose argument at this place Spark casts to text (upper(14) is '14'), and its name.
_TEXT_ARGS = {"upper": 0, "lower": 0, "trim": 0, "ltrim": 0, "rtrim": 0, "initcap": 0, "ascii": 0, "length": 0,
              "char_length": 0, "character_length": 0, "instr": 0, "locate": 1, "split": 0, "regexp_replace": 0,
              "translate": 0, "lpad": 0, "rpad": 0, "repeat": 0, "left": 0, "right": 0, "replace": 0}


def _text_argument(name: str, at: int, fn: Any) -> Any:
    """F.<name> with its text argument cast as Spark casts it (spark_builtins_sql.spark_text_arg); the
    argument keeps its own name, so the column is still named upper(i)."""
    @functools.wraps(fn)
    def call(*args: Any, **kwargs: Any) -> Any:
        if len(args) > at and isinstance(args[at], (Column, str)):
            given = F.col(args[at]) if isinstance(args[at], str) else args[at]
            text = _named(_sql(spark_builtins_sql.spark_text_arg_sql(_col_sql(given), name)), _spark_text(given))
            args = (*args[:at], text, *args[at + 1:])
        return fn(*args, **kwargs)
    return call


def _refusing_struct_edits(fn: Any) -> Any:
    """groupBy, orderBy, sort and agg refuse an edited struct inside an expression by name, as select
    does (spark_struct_fields), before DuckDB's own error about it."""
    @functools.wraps(fn)
    def call(self: Any, *cols: Any, **kwargs: Any) -> Any:
        from .spark_struct_fields import refuse_unresolved
        refuse_unresolved(list(_flat(cols)) + [c for c in kwargs.values() if isinstance(c, Column)])
        return fn(self, *cols, **kwargs)
    return call


def _install_spark_names() -> None:
    for name in (*_OPERATORS, *_UNARY):
        if hasattr(Column, name):
            setattr(Column, name, _spark_named(name, getattr(Column, name)))
    Column.isin = _isin_named(Column.isin)  # type: ignore[method-assign]
    Column.when = _case_step(Column.when, "when")  # type: ignore[method-assign]
    Column.otherwise = _case_step(Column.otherwise, "otherwise")  # type: ignore[method-assign]
    F.when = _case_named(F.when)
    for name, template in _TEXT_METHODS.items():
        if hasattr(Column, name):
            setattr(Column, name, _text_method_named(template, getattr(Column, name)))
    Column.cast = _cast_named(Column.cast)  # type: ignore[method-assign]
    for name, fn in list(vars(F).items()):
        if (inspect.isfunction(fn) and not name.startswith("_") and name not in _UNNAMED_FUNCTIONS
                and not getattr(fn, "_fd_named", False)):
            setattr(F, name, _function_named(name, fn))


# Column methods that Spark names as text, not as an operator.
_TEXT_METHODS = {"like": "{a} LIKE {b}", "rlike": "RLIKE({a}, {b})", "ilike": "ilike({a}, {b})",
                 "contains": "contains({a}, {b})", "startswith": "startswith({a}, {b})",
                 "endswith": "endswith({a}, {b})"}


def _text_method_named(template: str, fn: Any) -> Any:
    def method(self: Column, other: Any, *rest: Any) -> Any:
        out = fn(self, other, *rest)
        return _named(out, template.format(a=_spark_text(self), b=_spark_text(other))) if isinstance(out, Column) else out
    method.__doc__ = fn.__doc__
    return method


# A plain column, as DuckDB writes one: relation.name ("relation"."name" on DuckDB 2.0), "name" or name.
_PLAIN_COLUMN = re.compile(r'^(?:"?(?:unnamed_relation|arrow_object)_[0-9a-f]+"?\.)?(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_]*)$')
# Spark's SQL name for a type, from its simpleString (DuckDB's spells some types Spark's long way).
_TYPE_SQL = {"integer": "INT", "long": "BIGINT", "short": "SMALLINT", "byte": "TINYINT"}


def _cast_named(fn: Any) -> Any:
    """Column.cast: named CAST(x AS TYPE), as in Spark, but a plain column selected as it is keeps its name."""
    def cast(self: Column, dataType: Any) -> Column:  # noqa: N803 - PySpark's name
        out = fn(self, dataType)
        simple = dataType if isinstance(dataType, str) else dataType.simpleString()
        type_sql = _TYPE_SQL.get(simple.lower(), simple.upper())
        named = _named(out, f"CAST({_spark_text(self)} AS {type_sql})")
        if "_fd_name" not in vars(self) and _PLAIN_COLUMN.match(str(self.expr)):
            named._fd_top = _spark_text(self)  # type: ignore[attr-defined]  # selected, it keeps its name
        return named
    cast.__doc__ = fn.__doc__
    return cast


# Functions that keep the name they give: a column or literal as it is, SQL text, sort orders,
# window and grouping functions (named with their window or grouping), UDF makers.
_UNNAMED_FUNCTIONS = {"col", "column", "expr", "when", "udf", "pandas_udf", "broadcast", "asc", "desc",
                      "asc_nulls_first", "asc_nulls_last", "desc_nulls_first", "desc_nulls_last", "grouping",
                      "grouping_id", "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag",
                      "lead", "nth_value", "posexplode", "posexplode_outer", "inline", "inline_outer", "window",
                      "session_window", "call_function", "call_udf", "struct_field"}

# Spark's name for a function where it isn't the function's own name over its arguments: {0}, {1}...
# are the arguments, {args} all of them. Captured from Apache Spark 3.5.3.
_FUNCTION_NAMES = {
    "mean": "avg({args})", "variance": "var_samp({args})", "array_agg": "collect_list({args})",
    "sumDistinct": "sum(DISTINCT {args})", "sum_distinct": "sum(DISTINCT {args})",
    "countDistinct": "count(DISTINCT {args})", "count_distinct": "count(DISTINCT {args})",
    "pow": "POWER({args})", "power": "POWER({args})", "positive": "(+ {0})", "isnull": "({0} IS NULL)",
    "isnotnull": "({0} IS NOT NULL)", "explode": "col", "explode_outer": "col", "lit": "{0}",
    **{f: f.upper() + "({args})" for f in ("sqrt", "exp", "log10", "log2", "log1p", "expm1", "ceil", "ceiling",
                                           "floor", "signum", "cbrt", "sin", "cos", "tan", "asin", "acos", "atan",
                                           "sinh", "cosh", "tanh", "degrees", "radians", "rint", "cot", "sec", "csc")},
}
# Arguments Spark shows that PySpark leaves out, by function: (number of arguments given, text added).
_FUNCTION_DEFAULTS = {"round": (1, "0"), "bround": (1, "0"), "split": (2, "-1"), "regexp_replace": (3, "1"),
                      "percentile_approx": (2, "10000"), "locate": (2, "1"),
                      "from_unixtime": (1, "yyyy-MM-dd HH:mm:ss")}


def _aliased(col: Column) -> bool:
    """Whether ``col`` was given a name with alias (by spark_compat's own function): DuckDB's name for
    an expression can differ from its SQL text without one (DuckDB 2.0 names count(*) count_star())."""
    return bool(vars(col).get("_fd_alias", False))


def _alias_marked(fn: Any) -> Any:
    """Column.alias, marking the Column it gives as aliased (see _aliased)."""
    def alias(self: Column, *names: Any, **kwargs: Any) -> Column:
        out = fn(self, *names, **kwargs)
        out._fd_alias = True
        if "_fd_struct" in vars(self):  # a withField/dropFields column, worked out at select (spark_struct_fields)
            out._fd_struct = vars(self)["_fd_struct"]
            out._fd_struct_alias = names[0] if names else None
            out._fd_struct_named = vars(self).get("_fd_name")
        out._fd_alias_of = self  # Spark writes it "x AS name" inside update_fields' name (spark_struct_fields)
        return cast(Column, out)
    alias.__doc__ = fn.__doc__
    return alias


def _function_named(name: str, fn: Any) -> Any:
    """An F function whose Column carries Spark's name: the function over its arguments' names."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):  # silent-ok: a builtin without a signature keeps DuckDB's name
        return fn

    def call(*args: Any, **kwargs: Any) -> Any:
        out = fn(*args, **kwargs)
        if not isinstance(out, Column):
            return out
        if "_fd_name" not in vars(out) and _aliased(out):  # named by the function itself (spark_compat's own)
            return _named(out, out.expr.get_name())  # already Spark's text: unquoting it again would pair its quotes
        # otherwise its own name, also over one a function it calls gave (nvl over coalesce)
        try:
            bound = signature.bind(*args, **kwargs)
        except TypeError:  # silent-ok: fn already took these; only the name is skipped
            return out
        values: list[Any] = []
        for value in bound.arguments.values():
            values.extend(value if isinstance(value, (list, tuple)) else [value])
        texts = [_spark_text(v) for v in values]
        if name == "count" and texts == ["*"]:
            return _named(out, "count(1)")
        if name in ("first", "last", "first_value", "last_value"):  # Spark leaves ignorenulls out
            return _named(out, f"{name}({texts[0]})")
        if name == "log":
            return _named(out, f"ln({texts[0]})" if len(texts) == 1 else f"LOG({', '.join(texts)})")
        if name == "sort_array":
            asc = bound.arguments.get("asc", True)
            return _named(out, f"sort_array({texts[0]}, {'true' if asc else 'false'})")
        if (default := _FUNCTION_DEFAULTS.get(name)) and len(texts) == default[0]:
            texts.append(default[1])
        template = _FUNCTION_NAMES.get(name, f"{name}({{args}})")
        return _named(out, template.format(*texts, args=", ".join(texts)))
    call.__name__ = name
    call.__doc__ = fn.__doc__
    call._fd_named = True  # type: ignore[attr-defined]
    return call


# ---- DataFrame

_SAMPLE_ROW = "__fd_row"


def _frame(df: DataFrame, relation: Any) -> DataFrame:
    """A DataFrame on ``relation``, built from ``df``'s with DuckDB's relation API. No view is
    named for it: a named view lives on the session and keeps its data in memory until the
    kernel restarts, and a notebook's own view could share the name."""
    return DataFrame(relation, df.session)


def _spark_decimals(df: DataFrame, cols: Any) -> list[Any]:
    """``cols`` (columns and column names over ``df``) with Spark's decimal result types for ``+ - * /``,
    ``sum`` and ``avg`` of decimals (EA-004, #1682), by the rules Spark SQL on this PC follows
    (spark_decimals). Each column keeps the name it had; one whose types can't be worked out, a name
    and a star are left as they were."""
    from .spark_struct_fields import resolved
    cols = resolved(df.relation, list(cols))
    at = [i for i, c in enumerate(cols) if isinstance(c, Column)]
    texts = [str(cols[i].expr) for i in at]
    if not at or any(t.strip() == "*" or t.endswith(".*") for t in texts):
        return cols
    from .spark_decimals import spark_decimal_columns
    for i, new in zip(at, spark_decimal_columns(texts, _types(df)), strict=True):
        if new is not None:
            # The rewritten column keeps the column's name: its alias, or the name DuckDB gives the expression.
            cols[i] = Column(duckdb.SQLExpression(new[0]).alias(new[1] or _bound_name(df, cols[i].expr)))
    return cols


def _bound_name(df: DataFrame, expression: Any) -> str:
    """The name DuckDB gives ``expression`` as a column of ``df``: binding it runs nothing. An aggregate
    binds only in aggregate(); DuckDB 2.0 quotes column names in get_name() but not in this name."""
    for bind in (lambda: df.relation.select(expression), lambda: df.relation.aggregate([expression])):
        try:
            return str(bind().columns[0])
        except duckdb.Error:
            continue
    return str(expression.get_name())


def _decimal_select(fn: Any) -> Any:
    def select(self: DataFrame, *cols: Any) -> DataFrame:
        return fn(self, *_spark_decimals(self, _flat(cols)))
    select.__doc__ = fn.__doc__
    return select


def _decimal_with_column(fn: Any) -> Any:
    def withColumn(self: DataFrame, colName: str, col: Any) -> DataFrame:  # noqa: N802, N803
        return fn(self, colName, *_spark_decimals(self, [col]))
    withColumn.__doc__ = fn.__doc__
    return withColumn


def _decimal_with_columns(fn: Any) -> Any:
    def withColumns(self: DataFrame, *colsMap: Any) -> DataFrame:  # noqa: N802, N803
        if len(colsMap) == 1 and isinstance(colsMap[0], dict):
            names = list(colsMap[0])
            colsMap = (dict(zip(names, _spark_decimals(self, [colsMap[0][n] for n in names]), strict=True)),)
        return fn(self, *colsMap)
    withColumns.__doc__ = fn.__doc__
    return withColumns


def _agg(self: DataFrame, *exprs: Any) -> DataFrame:
    if len(exprs) == 1 and isinstance(exprs[0], dict):
        exprs = tuple(_dict_agg(self, exprs[0]))
    return DataFrame(self.relation.aggregate([c.expr for c in _spark_decimals(self, exprs)]), self.session)


def _grouped_agg(fn: Any) -> Any:
    """``df.groupBy("k").agg({"x": "avg"})``: DuckDB's GroupedData takes only columns."""
    def agg(self: GroupedData, *exprs: Any) -> DataFrame:
        if len(exprs) == 1 and isinstance(exprs[0], dict):
            exprs = tuple(_dict_agg(self._df, exprs[0]))
        if not self._grouping._type and any(_is_grouping(e) for e in exprs):
            from duckdb.experimental.spark.errors import AnalysisException
            raise AnalysisException("grouping()/grouping_id() can only be used with GroupingSets/Cube/Rollup")
        if not self._grouping._cols:  # groupBy() with no columns: one row over the whole frame, as in Spark
            return _agg(self._df, *exprs)
        exprs = tuple(_all_grouping_id(self, e) for e in exprs)
        return fn(self, *_spark_decimals(self._df, exprs))
    agg.__doc__ = fn.__doc__
    return agg


_GROUPING_ID_ALL = "grouping_id()"  # F.grouping_id() with no columns: every grouping column, known at agg
# What F.grouping_id() holds until agg knows the grouping columns: used anywhere else, it stops the query.
_GROUPING_ID_MARK = ("CAST(error('grouping_id() without columns works only in agg after groupBy, cube or rollup')"
                     " AS BIGINT)")


def _is_grouping(expr: Any) -> bool:
    """Whether ``expr`` uses grouping() or grouping_id(), which Spark allows only under cube or rollup
    (DuckDB gives 0 under a plain GROUP BY)."""
    if not isinstance(expr, Column):
        return False
    text = str(expr.expr)
    return "GROUPING(" in text.upper() or text == str(_sql(_GROUPING_ID_MARK).expr)


def _all_grouping_id(grouped: GroupedData, expr: Any) -> Any:
    """F.grouping_id() in ``agg``: Spark's grouping_id over all the grouping columns, named as Spark names it."""
    if isinstance(expr, Column) and str(expr.expr) == str(_sql(_GROUPING_ID_MARK).expr):
        # the name it was given: grouping_id(), or an alias of the user's
        return _sql(f"CAST(GROUPING({grouped._grouping.get_columns()}) AS BIGINT)").alias(expr.expr.get_name())
    return expr


def _grouping(col: Any) -> Column:
    """F.grouping: 1 on a row that a cube or rollup totals over ``col``, else 0 (Spark's tinyint)."""
    name = col if isinstance(col, str) else str(col.expr).strip('"')
    return _sql(f"CAST(GROUPING({_col_sql(col)}) AS TINYINT)").alias(f"grouping({name})")


def _grouping_id(*cols: Any) -> Column:
    """F.grouping_id: the grouping level as a bit vector (Spark's bigint), over ``cols`` or, with none,
    over every grouping column."""
    if not cols:
        return _sql(_GROUPING_ID_MARK).alias(_GROUPING_ID_ALL)
    names = ", ".join(c if isinstance(c, str) else str(c.expr).strip('"') for c in cols)
    return _sql(f"CAST(GROUPING({', '.join(_col_sql(c) for c in cols)}) AS BIGINT)").alias(f"grouping_id({names})")


def _grouping_sets(kind: str) -> Any:
    """DataFrame.cube and rollup: groupBy over every combination (cube) or every prefix (rollup) of the
    columns, with NULL where a row totals over a column."""
    def grouped(self: DataFrame, *cols: Any) -> GroupedData:
        from duckdb.experimental.spark.sql.group import Grouping
        columns = cols[0] if len(cols) == 1 and isinstance(cols[0], list) else cols
        return GroupedData(Grouping(*columns, special=kind) if columns else Grouping(), self)
    grouped.__name__ = kind
    return grouped


def _dict_agg(df: DataFrame, spec: dict[str, str]) -> list[Column]:
    """Spark's {column: function} form, its columns named as Spark names them (``avg(x)``, also for
    "mean"; ``count(1)`` for {"*": "count"}), the column in the frame's own spelling."""
    names = {"avg": "avg", "mean": "avg", "sum": "sum", "min": "min", "max": "max", "count": "count", "first": "first",
             "last": "last", "stddev": "stddev_samp", "variance": "var_samp"}
    shown = {"mean": "avg"}
    out = []
    for column, fn in spec.items():
        if fn.lower() not in names:
            raise ValueError(f"agg doesn't know {fn!r}. Use one of: {', '.join(sorted(names))}.")
        label = shown.get(fn.lower(), fn.lower())
        if column == "*" and fn.lower() == "count":
            out.append(_sql("count(*)").alias("count(1)"))
            continue
        column = _known(df, column)[0]
        out.append(_sql(f"{names[fn.lower()]}({_col_sql(column)})").alias(f"{label}({column})"))
    return out


def _global_select(fn: Any) -> Any:
    """``df.select(F.max("x"))``: Spark aggregates the whole frame; DuckDB needs an aggregate for that."""
    def select(self: DataFrame, *cols: Any) -> DataFrame:
        try:
            return fn(self, *cols)
        except duckdb.BinderException as exc:
            if "Aggregates cannot be present" not in str(exc):
                raise
            return _agg(self, *[F.col(c) if isinstance(c, str) else c for c in _flat(cols)])
    select.__doc__ = fn.__doc__
    return select


def _select_expr(self: DataFrame, *exprs: str) -> DataFrame:
    return self.select(*[expr(e) for e in _flat(exprs)])


_NUMERIC = re.compile(r"^(tinyint|smallint|int|integer|bigint|hugeint|float|double|real|decimal.*|utinyint|usmallint|uinteger|ubigint)$", re.I)


def _fillable(dtype: str, value: Any) -> bool:
    """Spark fills a column only with a value of its own kind."""
    if isinstance(value, bool):
        return dtype.lower() == "boolean"
    if isinstance(value, (int, float)):
        return bool(_NUMERIC.match(dtype))
    if isinstance(value, str):
        return dtype.lower() in ("varchar", "string")
    return False


def _known(df: DataFrame, names: Any) -> list[str]:
    """Column names as Spark resolves them, ignoring case, in the frame's own spelling; one that
    isn't there is an error, as in Spark, not skipped."""
    from duckdb.experimental.spark.errors import AnalysisException
    by_lower = {c.lower(): c for c in df.columns}
    out = []
    for name in [names] if isinstance(names, str) else list(names):
        if name in df.columns:
            out.append(name)
        elif (found := by_lower.get(str(name).lower())) is not None:
            out.append(found)
        else:
            raise AnalysisException(message=f"[UNRESOLVED_COLUMN.WITH_SUGGESTION] A column or function parameter "
                                    f"with name `{name}` cannot be resolved. Did you mean one of the following? "
                                    f"{df.columns}.")
    return out


_WHOLE = re.compile(r"^(tinyint|smallint|int|integer|bigint)$", re.I)


def _number_aggregate(fn: str, col: str, type_: str) -> Column:
    """``fn(col)`` as GroupedData names it; the sum of whole numbers is a bigint, as in Spark (DuckDB 1.x
    makes it a hugeint)."""
    out = getattr(F, fn)(F.col(col))
    return out.cast("bigint").alias(f"sum({col})") if fn == "sum" and _WHOLE.match(type_) else out


def _numeric_aggregate(name: str) -> Any:
    """GroupedData.max/min/sum/avg/mean as Spark's: the named number columns, or every number column
    (grouping ones too) when none is named, each as ``fn(col)``; a column that isn't a number is an error."""
    fn = "avg" if name == "mean" else name

    def aggregate(self: GroupedData, *cols: str) -> DataFrame:
        from duckdb.experimental.spark.errors import AnalysisException
        df = self._df
        types_ = dict(_types(df))
        names = _known(df, list(_flat(cols)))
        for c in names:
            if not _NUMERIC.match(types_[c]):
                raise AnalysisException(message=f'"{c}" is not a numeric column. Aggregation function can only '
                                        "be applied on a numeric column.")
        names = names or [c for c, t in _types(df) if _NUMERIC.match(t)]
        return self.agg(*(_number_aggregate(fn, c, types_[c]) for c in names)) if names else self.agg()

    aggregate.__name__ = name
    return aggregate


def _fillna(self: DataFrame, value: Any, subset: Any = None) -> DataFrame:
    if isinstance(value, dict):
        fills = dict(zip(_known(self, value), value.values(), strict=True))
    else:
        if not isinstance(value, (bool, int, float, str)):
            raise TypeError("fillna takes a number, text, a boolean or a dict of column to value.")
        wanted = _known(self, subset) if subset else [c for c, _ in _types(self)]
        fills = {c: value for c, t in _types(self) if c in wanted and _fillable(t, value)}
    cols = []
    for c, t in _types(self):
        if c in fills:
            v = fills[c]
            # An int into a double column stays a double; a float into an int column is truncated, as in Spark.
            cols.append(_sql(f"coalesce({_col_sql(c)}, CAST({exp.convert(v).sql('duckdb')} AS {t}))").alias(c))
        else:
            cols.append(F.col(c))
    return self.select(*cols)


def _types(df: DataFrame) -> list[tuple[str, str]]:
    return [(name, str(t)) for name, t in zip(df.relation.columns, df.relation.types, strict=True)]


def _dropna(self: DataFrame, how: str | None = "any", thresh: int | None = None, subset: Any = None) -> DataFrame:
    if how is not None and how not in ("any", "all"):  # None counts as "all", as in PySpark
        raise ValueError(f"how ({how!r}) should be 'any' or 'all'")  # PySpark's own check
    cols = _known(self, subset) if subset else self.columns
    if not cols:
        return self
    present = " + ".join(f"CAST({_col_sql(c)} IS NOT NULL AS INT)" for c in cols)
    need = thresh if thresh is not None else (len(cols) if how == "any" else 1)
    return _frame(self, self.relation.filter(f"({present}) >= {int(need)}"))


def _replace(self: DataFrame, to_replace: Any, value: Any = None, subset: Any = None) -> DataFrame:
    if isinstance(to_replace, (list, tuple)) and isinstance(value, (list, tuple)) and len(to_replace) != len(value):
        raise ValueError("to_replace and value lists should be of the same length. "
                         f"Got {len(to_replace)} and {len(value)}")  # PySpark's own check
    mapping = to_replace if isinstance(to_replace, dict) else (
        dict(zip(to_replace, value if isinstance(value, (list, tuple)) else [value] * len(to_replace), strict=True))
        if isinstance(to_replace, (list, tuple)) else {to_replace: value})
    wanted = _known(self, subset) if subset else self.columns
    sample = next(iter(mapping))
    cols = []
    for c, t in _types(self):
        if c in wanted and _fillable(t, sample):
            whens = " ".join(f"WHEN {_col_sql(c)} = {exp.convert(k).sql('duckdb')} THEN {exp.convert(v).sql('duckdb')}"
                             for k, v in mapping.items())
            cols.append(_sql(f"CASE {whens} ELSE {_col_sql(c)} END").alias(c))
        else:
            cols.append(F.col(c))
    return self.select(*cols)


class DataFrameNaFunctions:
    """``df.na.fill``, ``df.na.drop`` and ``df.na.replace``."""

    def __init__(self, df: DataFrame):
        self._df = df

    def fill(self, value: Any, subset: Any = None) -> DataFrame:
        return _fillna(self._df, value, subset)

    def drop(self, how: str = "any", thresh: int | None = None, subset: Any = None) -> DataFrame:
        return _dropna(self._df, how, thresh, subset)

    def replace(self, to_replace: Any, value: Any = None, subset: Any = None) -> DataFrame:
        return _replace(self._df, to_replace, value, subset)


def _describe(self: DataFrame, *cols: str) -> DataFrame:
    """count, mean, stddev, min and max of each column, as text, as Spark's describe() shows them."""
    names = _known(self, list(_flat(cols))) or [c for c, t in _types(self) if _NUMERIC.match(t) or t.upper() == "VARCHAR"]
    return _stats(self, names, ("count", "mean", "stddev", "min", "max"))


_SUMMARY = ("count", "mean", "stddev", "min", "25%", "50%", "75%", "max")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")  # used with fullmatch, so "25%\n" is refused as in Spark


def _summary(self: DataFrame, *statistics: str) -> DataFrame:
    """Spark's summary(): the statistics it names (default count, mean, stddev, min, quartiles, max) of every
    number and string column. It takes statistics, not columns; select the columns first."""
    stats = tuple(_flat(statistics)) or _SUMMARY
    for stat in stats:
        pct = _PERCENT.fullmatch(str(stat))
        if not (stat in ("count", "mean", "stddev", "min", "max", "count_distinct", "approx_count_distinct")
                or (pct and float(pct.group(1)) <= 100)):
            raise ValueError(f"{stat} is not a recognised statistic. Use count, mean, stddev, min, max, "
                             "count_distinct, approx_count_distinct or a percentile such as 75%.")
    names = [c for c, t in _types(self) if _NUMERIC.match(t) or t.upper() == "VARCHAR"]
    return _stats(self, names, stats)


# Spark's stddev: the running (Welford) update its aggregate makes row by row, which can differ from DuckDB's
# stddev_samp in the last digits. It folds a list of the column's values, so it is used only up to this many
# rows: fewer than one DuckDB row group (122,880), which one thread reads in order, so the order is the
# rows' own; above it, stddev_samp answers as fast as before (Spark's own last digit then depends on its
# partitions anyway).
_WELFORD_ROWS = 100_000
_WELFORD = ("lambda kf_m, kf_v: {'n': kf_m.n + 1, 'avg': kf_m.avg + (kf_v.avg - kf_m.avg) / (kf_m.n + 1), "
            "'m2': kf_m.m2 + (kf_v.avg - kf_m.avg) * ((kf_v.avg - kf_m.avg) - (kf_v.avg - kf_m.avg) / (kf_m.n + 1))}")


def _list_stddev(values: str) -> str:
    """DuckDB SQL for Spark's stddev of a DOUBLE[] in row order: NULL for fewer than two values."""
    moments = (f"LIST_REDUCE(LIST_TRANSFORM(LIST_FILTER({values}, lambda kf_v: kf_v IS NOT NULL), "
               f"lambda kf_v: {{'n': 1::DOUBLE, 'avg': kf_v, 'm2': 0::DOUBLE}}), {_WELFORD}, "
               "{'n': 0::DOUBLE, 'avg': 0::DOUBLE, 'm2': 0::DOUBLE})")
    return f"SQRT({moments}.m2 / NULLIF(GREATEST({moments}.n - 1, 0), 0))"


def _stats(self: DataFrame, names: list[str], stats: Any) -> DataFrame:
    types_ = dict(_types(self))
    welford = "stddev" in stats and int(one_row(self.relation.aggregate("count(*)"))[0]) <= _WELFORD_ROWS
    rows = []
    for stat in stats:
        parts = []
        for c in names:
            q = _col_sql(c)
            numeric = bool(_NUMERIC.match(types_[c]))
            # Spark takes the mean and stddev of text as numbers (NULL where it isn't one)
            x = f"CAST({q} AS DOUBLE)" if numeric else f"TRY_CAST({q} AS DOUBLE)"
            if pct := _PERCENT.fullmatch(stat):  # Spark's approximate percentile is an actual value, as quantile_disc
                sql = f"quantile_disc({q}, {float(pct.group(1)) / 100})" if numeric else "max(NULL)"
            else:
                sql = {"count": f"count({q})", "mean": f"avg({x})", "stddev": f"list({x})" if welford else f"stddev_samp({x})",
                       "min": f"min({q})", "max": f"max({q})", "count_distinct": f"count(DISTINCT {q})",
                       "approx_count_distinct": f"approx_count_distinct({q})"}[stat]
            parts.append(f"{sql} AS {q}")
        row = self.relation.aggregate(f"'{stat}' AS summary, {', '.join(parts)}")
        quoted = [_col_sql(c) for c in names]
        if stat == "stddev" and welford:
            row = row.project(", ".join(["summary", *(f"{_list_stddev(q)} AS {q}" for q in quoted)]))
        # each value as Spark writes it as text (a double as Java writes it)
        rows.append(row.project(", ".join(
            ["summary", *(f"CAST({spark_builtins_sql.java_value_text(q)} AS VARCHAR) AS {q}" for q in quoted)])))
    out = rows[0]
    for row in rows[1:]:
        out = out.union(row)
    return _frame(self, out)


def _drop_cols(fn: Any) -> Any:
    """``df.drop("ID")`` drops ``id``: Spark resolves names ignoring case (a name that isn't there is skipped).
    Dropping every column is refused: Spark keeps a frame with no columns, which DuckDB can't hold."""
    def drop(self: DataFrame, *cols: Any) -> DataFrame:
        by_lower = {c.lower(): c for c in self.columns}

        def resolve(c: Any) -> Any:  # F.col("ID") names the column too; another expression is left as given
            name = c if isinstance(c, str) else c.expr.get_name() if isinstance(c, Column) else None
            if isinstance(c, Column) and name is not None and len(name) > 1 and name[0] == name[-1] == "`":
                name = name[1:-1]
            return by_lower.get(name.lower(), c) if name is not None else c
        cols = tuple(resolve(c) for c in cols)
        gone = {c if isinstance(c, str) else c.expr.get_name() for c in cols if isinstance(c, (str, Column))}
        if self.columns and not set(self.columns) - gone:
            raise ValueError(f"drop would leave no columns ({', '.join(self.columns)}). Spark keeps an empty "
                             "frame here, but this PC's engine can't hold one; keep at least one column.")
        return fn(self, *cols)
    drop.__doc__ = fn.__doc__
    return drop


def _with_column_renamed(fn: Any) -> Any:
    """Spark: "a no-op if the schema doesn't contain the given column name"."""
    def rename(self: DataFrame, existing: str, new: str) -> DataFrame:
        if existing.lower() not in (c.lower() for c in self.columns):
            return self
        return fn(self, existing, new)
    rename.__doc__ = fn.__doc__
    return rename


def _drop_duplicates(fn: Any) -> Any:
    """An unknown column in ``subset`` is Spark's error, not DuckDB's binder error."""
    def dedupe(self: DataFrame, subset: Any = None) -> DataFrame:
        return fn(self, _known(self, subset) if subset else subset)
    dedupe.__doc__ = fn.__doc__
    return dedupe


def _sample(self: DataFrame, withReplacement: Any = None, fraction: float | None = None,  # noqa: N803
            seed: int | None = None) -> DataFrame:
    if withReplacement is not None and not isinstance(withReplacement, bool):  # sample(0.1) and sample(0.1, 42)
        withReplacement, fraction, seed = None, withReplacement, cast("int | None", fraction)  # noqa: N806
    if withReplacement:
        raise NotImplementedError("sample(withReplacement=True) isn't supported on this PC. Leave it out to sample without replacement.")
    if fraction is None:
        raise TypeError("sample() needs a fraction, like df.sample(0.1).")
    if not 0.0 <= float(fraction) <= 1.0:  # Spark refuses this too: the fraction is a probability per row
        raise ValueError(f"Sampling fraction ({fraction}) must be on interval [0, 1] without replacement.")
    keep = float(fraction)
    if seed is None:
        return _frame(self, self.relation.filter(f"random() < {keep!r}"))
    # A seed picks the same rows each time: each row's place, hashed with the seed, against the fraction.
    cols = ", ".join(_col_sql(c) for c in self.columns)
    numbered = self.relation.project(f"*, row_number() OVER () AS {_SAMPLE_ROW}")
    picked = numbered.filter(f"hash({_SAMPLE_ROW}, {int(seed)}) % 1000000 < {int(keep * 1000000)}")
    return _frame(self, picked.project(cols))


_KEEP_ROW = "__fd_keep"


def _row_filter(fn: Any) -> Any:
    """DataFrame.filter and where, also on a row's place (a seeded rand, monotonically_increasing_id),
    which this PC numbers with a window: DuckDB refuses one in WHERE, so it's worked out first."""
    def filter(self: DataFrame, condition: Any) -> DataFrame:  # noqa: A001 - PySpark's name
        from .spark_struct_fields import refuse_unresolved
        refuse_unresolved([condition])
        if isinstance(condition, Column) and "row_number() OVER ()" in str(condition.expr):
            cols = ", ".join(_col_sql(c) for c in self.columns)
            numbered = self.relation.project(f"*, ({condition.expr}) AS {_KEEP_ROW}")
            return _frame(self, numbered.filter(_KEEP_ROW).project(cols))
        return cast(DataFrame, fn(self, condition))
    filter.__doc__ = fn.__doc__
    return filter


def _subtract(self: DataFrame, other: DataFrame) -> DataFrame:
    return self.distinct().exceptAll(other)


def _unpivot(self: DataFrame, ids: Any, values: Any, variableColumnName: str, valueColumnName: str) -> DataFrame:  # noqa: N803
    ids = [ids] if isinstance(ids, (str, Column)) else list(ids)
    # values=None is every column that isn't an id, as in Spark; an empty list names none, which Spark refuses.
    values = [values] if isinstance(values, (str, Column)) else list(
        [c for c in self.columns if c not in ids] if values is None else values)
    if not values:
        raise ValueError("[UNPIVOT_REQUIRES_VALUE_COLUMNS] At least one value column needs to be specified for "
                         "UNPIVOT, all columns specified as ids.")
    # One projection per value column, stacked, then put back in row order (each row's values together),
    # as Spark's DataFrame.unpivot returns them. Unlike SQL's UNPIVOT, it keeps a null value.
    id_cols = ", ".join(_col_sql(c) for c in ids)
    numbered = self.relation.project(f"*, row_number() OVER () AS {_SAMPLE_ROW}")
    parts = [numbered.project(f"{id_cols + ', ' if ids else ''}{_duck_lit(_name_of(v))} AS {_col_sql(variableColumnName)}, "
                              f"{_col_sql(v)} AS {_col_sql(valueColumnName)}, {_SAMPLE_ROW}, {i} AS {_SAMPLE_ROW}_col")
             for i, v in enumerate(values)]
    out = parts[0]
    for part in parts[1:]:
        out = out.union(part)
    out = out.order(f"{_SAMPLE_ROW}, {_SAMPLE_ROW}_col")
    keep = [c for c in (*map(_name_of, ids), variableColumnName, valueColumnName)]
    return _frame(self, out.project(", ".join(_col_sql(c) for c in keep)))


def _same_self(fn: Any) -> Any:
    """A join of a DataFrame with itself: DuckDB needs the two sides named apart."""
    def join(self: DataFrame, other: DataFrame, *args: Any, **kwargs: Any) -> DataFrame:
        if other.relation.alias == self.relation.alias:
            other = DataFrame(other.relation.set_alias(f"fd_right_{next(_ids)}"), other.session)
        return fn(self, other, *args, **kwargs)
    join.__doc__ = fn.__doc__
    return join


def _same(self: DataFrame, *_args: Any, **_kwargs: Any) -> DataFrame:
    """repartition, coalesce, persist...: how Spark spreads the work; one PC has nothing to spread."""
    return self


# ---- pivot

class PivotedData:
    """``df.groupBy(...).pivot(column, values)`` then an aggregate: one column per value, each the
    aggregate over that value's rows (``sum(x) FILTER (WHERE column = value)``)."""

    def __init__(self, grouped: GroupedData, column: str, values: list[Any] | None):
        self._grouped, self._column, self._values = grouped, column, values

    def agg(self, *exprs: Column) -> DataFrame:
        df = self._grouped._df
        if len(exprs) == 1 and isinstance(exprs[0], dict):
            exprs = tuple(_dict_agg(df, exprs[0]))
        values = self._values
        if values is None:  # Spark finds them, sorted
            values = sorted(r[0] for r in df.select(self._column).distinct().collect() if r[0] is not None)
        cols = []
        for v in values:
            for e in exprs:
                name = str(v) if len(exprs) == 1 else f"{v}_{e.expr.get_name()}"
                cols.append(_sql(f"{e.expr} FILTER (WHERE {_col_sql(self._column)} = "
                                 f"{exp.convert(v).sql('duckdb')})").alias(name))
        return self._grouped.agg(*cols) if self._grouped._grouping._cols else _agg(df, *cols)

    def _simple(self, fn: str, *cols: str) -> DataFrame:
        return self.agg(*[_sql(f"{fn}({_col_sql(c)})").alias(f"{fn}({c})") for c in cols])

    def sum(self, *cols: str) -> DataFrame:
        return self._simple("sum", *cols)

    def avg(self, *cols: str) -> DataFrame:
        return self._simple("avg", *cols)

    mean = avg

    def min(self, *cols: str) -> DataFrame:
        return self._simple("min", *cols)

    def max(self, *cols: str) -> DataFrame:
        return self._simple("max", *cols)

    def count(self) -> DataFrame:
        return self.agg(_sql("count(*)").alias("count"))


def _pivot(self: GroupedData, pivot_col: str, values: list[Any] | None = None) -> PivotedData:
    return PivotedData(self, pivot_col, values)


# ---- spark.conf


# ---- install

window_module = types.ModuleType("pyspark.sql.window")
window_module.Window = Window  # type: ignore[attr-defined]
window_module.WindowSpec = WindowSpec  # type: ignore[attr-defined]


def sql_names(spark_sql: Any) -> None:
    """The names ``from pyspark.sql import ...`` gives in Spark that DuckDB's pyspark.sql lacks: Row
    (DuckDB keeps it in types), Column and GroupedData, and Window."""
    from duckdb.experimental.spark.sql import column, group
    from duckdb.experimental.spark.sql import types as spark_types
    spark_sql.Row = spark_types.Row
    spark_sql.Column = column.Column
    spark_sql.GroupedData = group.GroupedData
    spark_sql.Window = Window


def spark_type_name(dt: Any) -> str:
    """``dt`` as Spark's ``DataType.simpleString()`` writes it, which is what ``DataFrame.dtypes`` shows:
    int, timestamp, timestamp_ntz. DuckDB 1.x's Spark API says integer and timestamptz, and DuckDB 2.0
    says what Spark does, so the engine answers the same on both (#1341)."""
    from duckdb.experimental.spark.sql import types as T
    if isinstance(dt, T.ArrayType):
        return f"array<{spark_type_name(dt.elementType)}>"
    if isinstance(dt, T.MapType):
        return f"map<{spark_type_name(dt.keyType)},{spark_type_name(dt.valueType)}>"
    if isinstance(dt, T.StructType):
        return "struct<" + ",".join(f"{f.name}:{spark_type_name(f.dataType)}" for f in dt.fields) + ">"
    if isinstance(dt, T.IntegerType):
        return "int"
    if isinstance(dt, T.TimestampNTZType):
        return "timestamp_ntz"
    if isinstance(dt, T.TimestampType):
        return "timestamp"
    return str(dt.simpleString())


def _tree_type_name(dt: Any) -> str:
    """``dt`` as Spark's printSchema names it (its typeName): long, integer, short, byte, and a nested
    type by its kind alone, as its parts follow on their own lines."""
    from duckdb.experimental.spark.sql import types as T
    for kind, name in ((T.ArrayType, "array"), (T.MapType, "map"), (T.StructType, "struct"), (T.LongType, "long"),
                       (T.IntegerType, "integer"), (T.ShortType, "short"), (T.ByteType, "byte")):
        if isinstance(dt, kind):
            return name
    return spark_type_name(dt)


_SCHEMA_ESCAPES = str.maketrans({"\n": "\\n", "\r": "\\r", "\t": "\\t", "\f": "\\f", "\b": "\\b",
                                  "\v": "\\v", "\a": "\\a"})


def _tree_lines(dt: Any, prefix: str, depth: int, out: list[str]) -> None:
    """Spark's buildFormattedString: the parts of a nested type, each a level further in."""
    from duckdb.experimental.spark.sql import types as T
    if depth <= 0:
        return
    inner = prefix + "    |"
    if isinstance(dt, T.StructType):
        for f in dt.fields:
            out.append(f"{prefix}-- {f.name.translate(_SCHEMA_ESCAPES)}: {_tree_type_name(f.dataType)} "
                       f"(nullable = {str(bool(f.nullable)).lower()})")
            _tree_lines(f.dataType, inner, depth - 1, out)
    elif isinstance(dt, T.ArrayType):
        out.append(f"{prefix}-- element: {_tree_type_name(dt.elementType)} "
                   f"(containsNull = {str(bool(dt.containsNull)).lower()})")
        _tree_lines(dt.elementType, inner, depth - 1, out)
    elif isinstance(dt, T.MapType):
        out.append(f"{prefix}-- key: {_tree_type_name(dt.keyType)}")
        _tree_lines(dt.keyType, inner, depth - 1, out)
        out.append(f"{prefix}-- value: {_tree_type_name(dt.valueType)} "
                   f"(valueContainsNull = {str(bool(dt.valueContainsNull)).lower()})")
        _tree_lines(dt.valueType, inner, depth - 1, out)


def tree_string(schema: Any, level: int | None = None) -> str:
    """Spark's StructType.treeString: root, then each field and its nested parts down to ``level``
    levels (all of them when level is None, 0 or less), each line ending in a newline."""
    out = ["root"]
    _tree_lines(schema, " |", level if level and level > 0 else sys.maxsize, out)
    return "\n".join(out) + "\n"


def _print_schema(self: DataFrame, level: int | None = None) -> None:
    """``df.printSchema()`` as Spark prints it: Spark's type names (long, not bigint) and nested types
    one part per line (every level when ``level`` is None, 0 or less, as in Spark)."""
    print(tree_string(self.schema, level))


def _dtypes(self: DataFrame) -> list[tuple[str, str]]:
    return [(f.name, spark_type_name(f.dataType)) for f in self.schema.fields]


def _struct_rows(value: Any, dt: Any) -> Any:
    """A struct value as Spark's collect() gives it: a Row, also inside arrays and maps. DuckDB 1.x's
    Spark API gives a dict; DuckDB 2.0's gives a Row (#1341)."""
    from duckdb.experimental.spark.sql import types as T
    from duckdb.experimental.spark.sql.types import Row
    if value is None:
        return None
    if isinstance(dt, T.StructType):
        if not isinstance(value, dict):
            return value
        row = tuple.__new__(Row, [_struct_rows(value.get(f.name), f.dataType) for f in dt.fields])
        row.__fields__ = [f.name for f in dt.fields]
        return row
    if isinstance(dt, T.ArrayType) and isinstance(value, list):
        return [_struct_rows(v, dt.elementType) for v in value]
    if isinstance(dt, T.MapType) and isinstance(value, dict):
        return {k: _struct_rows(v, dt.valueType) for k, v in value.items()}
    return value


def _has_struct(dt: Any) -> bool:
    from duckdb.experimental.spark.sql import types as T
    if isinstance(dt, T.StructType):
        return True
    if isinstance(dt, T.ArrayType):
        return _has_struct(dt.elementType)
    if isinstance(dt, T.MapType):
        return _has_struct(dt.valueType)
    return False


# DataFrame.show as Spark 3.5 prints it (Dataset.showString): +---+ borders, cells as Spark casts them to
# text for display (NULL, true, [1, 2], {a -> 1}, 1.0E20), right-aligned unless truncate is off, East
# Asian wide characters two columns wide. DuckDB's own show draws its box table instead.
_WIDE = re.compile("[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︐-︙︰-﹯"
                   "＀-｠￠-￦]")
_ESCAPES = str.maketrans({"\n": "\\n", "\r": "\\r", "\t": "\\t", "\f": "\\f", "\b": "\\b", "\x0b": "\\v",
                          "\x07": "\\a"})


def _half_width(text: str) -> int:
    return len(text) + len(_WIDE.findall(text))


def _java_double(value: float, digits: str | None = None) -> str:
    """``value`` as Java's Double.toString writes it: 1.0, 1.0E20, 1.0E-4, NaN, Infinity. ``digits`` are
    the significant digits to write (a float's shortest), else the shortest that read back as the double."""
    import decimal
    if value != value:
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "Infinity" if value > 0 else "-Infinity"
    sign = "-" if str(value).startswith("-") else ""
    if value == 0:
        return f"{sign}0.0"
    number = decimal.Decimal(digits or repr(abs(value)))
    figures = "".join(map(str, number.as_tuple().digits)).lstrip("0").rstrip("0") or "0"
    power = number.adjusted()
    if -3 <= power < 7:
        if power >= 0:
            whole, frac = figures[:power + 1].ljust(power + 1, "0"), figures[power + 1:]
        else:
            whole, frac = "0", "0" * (-power - 1) + figures
        return f"{sign}{whole}.{frac or '0'}"
    return f"{sign}{figures[0]}.{figures[1:] or '0'}E{power}"


def _java_float(value: float) -> str:
    """A FloatType value as Java's Float.toString writes it: the shortest digits that read back as the
    same 32-bit float, not the double DuckDB hands Python."""
    import struct
    if value != value or value in (float("inf"), float("-inf")) or value == 0:
        return _java_double(value)
    single = struct.unpack("f", struct.pack("f", value))[0]
    for places in range(0, 9):
        text = f"{abs(single):.{places}e}"
        try:
            same = struct.unpack("f", struct.pack("f", float(text)))[0] == abs(single)
        except OverflowError:  # silent-ok: rounded past the largest float, so more digits are needed
            same = False
        if same:
            return _java_double(value, text)
    return _java_double(value)


def _interval(value: Any) -> str:
    """A day-time interval as Spark displays one: INTERVAL '1 02:03:04.5' DAY TO SECOND."""
    micros = value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds
    sign, micros = ("-", -micros) if micros < 0 else ("", micros)
    days, rest = divmod(micros, 86_400_000_000)
    hours, rest = divmod(rest, 3_600_000_000)
    minutes, rest = divmod(rest, 60_000_000)
    seconds, fraction = divmod(rest, 1_000_000)
    text = f"{sign}{days} {hours:02d}:{minutes:02d}:{seconds:02d}"
    if fraction:
        text += f".{fraction:06d}".rstrip("0")
    return f"INTERVAL '{text}' DAY TO SECOND"


def _pretty(value: Any, data_type: Any) -> str:
    """One value as Spark's show writes it (ToPrettyString)."""
    import datetime
    import decimal
    from duckdb.experimental.spark.sql import types as T
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(data_type, T.FloatType) and isinstance(value, float):
        return _java_float(value)
    if isinstance(value, float):
        return _java_double(value)
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[" + " ".join(f"{b:02X}" for b in bytes(value)) + "]"
    if isinstance(value, datetime.datetime):
        text = value.replace(tzinfo=None).isoformat(sep=" ")  # a TIMESTAMPTZ is already in the session's zone
        return text.rstrip("0") if "." in text else text
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return _interval(value)
    if isinstance(data_type, T.MapType) and isinstance(value, dict):
        return "{" + ", ".join(f"{_pretty(k, data_type.keyType)} -> {_pretty(v, data_type.valueType)}"
                               for k, v in value.items()) + "}"
    if isinstance(data_type, T.StructType):
        values = list(value.values()) if isinstance(value, dict) else list(value)
        return "{" + ", ".join(_pretty(v, f.dataType) for v, f in zip(values, data_type.fields, strict=False)) + "}"
    if isinstance(value, (list, tuple)):
        element = getattr(data_type, "elementType", None)
        return "[" + ", ".join(_pretty(v, element) for v in value) + "]"
    return str(value)


def show_string(df: DataFrame, n: int = 20, truncate: bool | int = True, vertical: bool = False) -> str:
    """What ``df.show(n, truncate, vertical)`` prints in Spark 3.5."""
    if not isinstance(n, int) or isinstance(n, bool):
        raise TypeError(f"Argument `n` should be an int, got {type(n).__name__}.")
    if not isinstance(truncate, (bool, int)):
        raise TypeError(f"Argument `truncate` should be a bool or an int, got {type(truncate).__name__}.")
    if not isinstance(vertical, bool):
        raise TypeError(f"Argument `vertical` should be a bool, got {type(vertical).__name__}.")
    width = (20 if truncate else 0) if isinstance(truncate, bool) else truncate
    n = max(n, 0)
    fields = df.schema.fields
    taken = df.limit(n + 1).collect()
    more = len(taken) > n

    def cell(value: Any, data_type: Any) -> str:
        text = _pretty(value, data_type).translate(_ESCAPES)
        if width > 0 and len(text) > width:
            text = text[:width] if width < 4 else text[:width - 3] + "..."
        return text

    rows = [[f.name.translate(_ESCAPES) for f in fields]] + [[cell(v, f.dataType) for v, f in zip(r, fields, strict=True)] for r in taken[:n]]
    out: list[str] = []
    if not vertical:
        widths = [max(3, *(_half_width(r[i]) for r in rows)) for i in range(len(fields))]

        def line(row: list[str]) -> str:
            pad = [w - _half_width(c) + len(c) for c, w in zip(row, widths, strict=True)]
            return "|" + "|".join(c.rjust(p) if width > 0 else c.ljust(p) for c, p in zip(row, pad, strict=True)) + "|"
        sep = "+" + "+".join("-" * w for w in widths) + "+"
        out += [sep, line(rows[0]), sep, *(line(r) for r in rows[1:]), sep]
    else:
        names, data = rows[0], rows[1:]
        name_width = max([3, *(_half_width(c) for c in names)])
        data_width = max([3, *(_half_width(c) for r in data for c in r)])
        for i, row in enumerate(data):
            out.append(f"-RECORD {i}".ljust(name_width + data_width + 5, "-"))
            out += [f" {name.ljust(name_width - _half_width(name) + len(name))} | "
                    f"{c.ljust(data_width - _half_width(c) + len(c))} " for name, c in zip(names, row, strict=True)]
    if vertical and not rows[1:]:
        out.append("(0 rows)")
    elif more:
        out.append(f"only showing top {n} {'row' if n == 1 else 'rows'}")
    return "\n".join(out) + "\n"


def _show(self: DataFrame, n: int = 20, truncate: bool | int = True, vertical: bool = False) -> None:
    print(show_string(self, n, truncate, vertical))


def _collect_structs_as_rows(collect: Any) -> Any:
    def collect_rows(self: DataFrame) -> list[Any]:
        rows = collect(self)
        fields = self.schema.fields
        if not any(_has_struct(f.dataType) for f in fields):
            return rows
        out = []
        for r in rows:
            row = tuple.__new__(type(r), [_struct_rows(v, f.dataType) for v, f in zip(r, fields, strict=True)])
            row.__fields__ = list(r.__fields__)
            out.append(row)
        return out
    return collect_rows


_TIME_TYPE_NAMES = ("date", "timestamp")
# Spark's "timestamp" is TimestampType, a moment in time: the same DuckDB type as cast(TimestampType())
# and Spark SQL's CAST AS TIMESTAMP, not DuckDB's own "timestamp" (without a time zone).
_TIME_TARGETS = {"date": "DATE", "timestamp": "TIMESTAMPTZ"}


def _time_cast(original: Any) -> Any:
    """Column.cast to a date or timestamp as Spark's with ANSI off: a value that isn't one gives NULL
    (EA-015), where DuckDB's cast stops the query. The column keeps the name DuckDB's cast gives it."""
    from duckdb.experimental.spark.sql.types import DateType, TimestampNTZType, TimestampType

    def cast(self: Column, dataType: Any) -> Column:  # noqa: N803 - PySpark's name
        plain = original(self, dataType)
        if isinstance(dataType, str):
            if dataType.strip().lower() not in _TIME_TYPE_NAMES:
                return plain
            target = _TIME_TARGETS[dataType.strip().lower()]
        elif isinstance(dataType, (DateType, TimestampType, TimestampNTZType)):
            target = str(dataType.duckdb_type)
        else:
            return plain
        # Only text, a date or a timestamp can fail to be one: those give NULL. Anything else (a number)
        # keeps DuckDB's own cast, so it is never NULL without a word.
        value = _col_sql(self)
        return _sql(f"(LIST_TRANSFORM([{value}], lambda kf_t: CASE WHEN typeof(kf_t) = 'VARCHAR' OR typeof(kf_t) "
                    f"IN ({spark_sql._TIME_TYPES}) THEN TRY_CAST(kf_t AS {target}) ELSE CAST(kf_t AS {target}) END))[1]"
                    ).alias(plain.expr.get_name())
    return cast


def _spark_divide(reverse: bool) -> Any:
    """Column division as Spark's (ANSI off): dividing by zero gives NULL (EA-012); DuckDB gives inf,
    -inf or NaN. Spark's ``/`` is a double division, as DuckDB's is."""
    from duckdb.experimental.spark.sql.column import _get_expr

    def divide(self: Column, other: Any) -> Column:
        left, right = (_get_expr(other), self.expr) if reverse else (self.expr, _get_expr(other))
        return Column(left / duckdb.FunctionExpression("nullif", right, duckdb.ConstantExpression(0)))
    return divide


# Column.cast to a whole-number type truncates as Spark does (EA-003); DuckDB's cast rounds.
_INT_CAST_NAMES = {"tinyint": ("TINYINT", "TINYINT"), "byte": ("TINYINT", "TINYINT"),
                   "smallint": ("SMALLINT", "SMALLINT"), "short": ("SMALLINT", "SMALLINT"),
                   "int": ("INTEGER", "INT"), "integer": ("INTEGER", "INT"),
                   "bigint": ("BIGINT", "BIGINT"), "long": ("BIGINT", "BIGINT")}
_INT_CAST_TYPES = {"ByteType": "tinyint", "ShortType": "smallint", "IntegerType": "int", "LongType": "bigint"}


def _int_cast(original: Any) -> Any:
    def cast(self: Column, dataType: Any) -> Column:
        name = dataType.strip().lower() if isinstance(dataType, str) else _INT_CAST_TYPES.get(type(dataType).__name__)
        target = _INT_CAST_NAMES.get(name or "")
        if isinstance(dataType, str) and name == "timestamp":
            stamp: str | None = _TIME_TARGETS["timestamp"]
        elif type(dataType).__name__ in ("TimestampType", "TimestampNTZType"):
            stamp = str(dataType.duckdb_type)
        else:
            stamp = None
        if target is None and stamp is None:
            return original(self, dataType)
        named = original(self, dataType).expr.get_name()   # the name DuckDB's own cast would give the column
        # A number cast to a timestamp is its seconds since 1970 (EA-014), as spark_timestamp_cast writes it.
        if stamp is not None:
            cast_sql = spark_sql.spark_timestamp_cast("kf_cast", stamp, "legacy")
        else:
            duck_type, spark_type = target  # type: ignore[misc]  # not None here: one of the two is set
            cast_sql = spark_sql.spark_integer_cast("kf_cast", duck_type, spark_type, "legacy")
        return _sql(f"(LIST_TRANSFORM([{_col_sql(self)}], lambda kf_cast: {cast_sql}))[1]").alias(named)
    return cast


def install() -> None:
    """Add the calls above to DuckDB's Spark API (once per process)."""
    if getattr(DataFrame, "_fd_compat", False):
        return
    for name, fn in FUNCTIONS.items():
        if name in ("count", "substring", "concat", "initcap", "first", "last", "slice", "add_months",
                    "months_between", "element_at", "to_date", "to_timestamp", "ln", "log2", "pow", "factorial",
                    "split", "rand", "isnan", "printf") or not hasattr(F, name):
            setattr(F, name, fn)
    vars(F).update(expr=expr)  # Spark SQL, translated (DuckDB's own expr takes DuckDB SQL)
    Column.alias = _alias_marked(Column.alias)  # type: ignore[method-assign]
    Column.over = _over  # type: ignore[attr-defined]
    Column.getItem = _get_item  # type: ignore[attr-defined]
    Column.between = _between  # type: ignore[attr-defined]
    Column.substr = _substr  # type: ignore[attr-defined]
    Column.bitwiseAND = _bitwise("&")  # type: ignore[attr-defined]
    Column.bitwiseOR = _bitwise("|")  # type: ignore[attr-defined]
    Column.bitwiseXOR = _bitwise("^")  # type: ignore[attr-defined]
    Column.eqNullSafe = _eq_null_safe  # type: ignore[attr-defined]
    # Spark's other names for alias and cast, calling whatever alias and cast are by then
    Column.name = lambda self, *names, **kw: self.alias(*names, **kw)  # type: ignore[attr-defined]
    Column.astype = lambda self, dataType: self.cast(dataType)  # type: ignore[attr-defined]
    Column.getField = _get_field  # type: ignore[attr-defined]
    from .spark_struct_fields import drop_fields, with_field
    Column.withField = with_field  # type: ignore[attr-defined]
    Column.dropFields = drop_fields  # type: ignore[attr-defined]
    Column.__getitem__ = _getitem(Column.__getitem__)  # type: ignore[method-assign]
    Column.__truediv__ = Column.__div__ = _spark_divide(False)
    Column.__rtruediv__ = Column.__rdiv__ = _spark_divide(True)
    # Each wraps the other: a whole-number target goes to _int_cast, a date or timestamp to _time_cast.
    Column.cast = _time_cast(Column.cast)  # type: ignore[method-assign]
    Column.cast = _int_cast(Column.cast)  # type: ignore[method-assign]
    for name in _ORDERS:
        setattr(Column, name, _ordered(name, getattr(Column, name)))
    for name, at in _TEXT_ARGS.items():
        if hasattr(F, name):
            setattr(F, name, _text_argument(name, at, getattr(F, name)))
    _install_spark_names()  # after every other Column patch, so it names what they give
    DataFrame.agg = _agg  # type: ignore[attr-defined]
    DataFrame.show = _show  # type: ignore[method-assign,assignment]
    DataFrame.cube = _grouping_sets("cube")  # type: ignore[attr-defined]
    DataFrame.rollup = _grouping_sets("rollup")  # type: ignore[attr-defined]
    vars(F).update(grouping=_grouping, grouping_id=_grouping_id)
    DataFrame.dtypes = property(_dtypes)  # type: ignore[assignment,method-assign]
    DataFrame.printSchema = _print_schema  # type: ignore[method-assign]
    from duckdb.experimental.spark.sql import types as T
    for kind in (T.IntegerType, T.TimestampType, T.TimestampNTZType, T.ArrayType, T.MapType, T.StructType):
        kind.simpleString = spark_type_name  # type: ignore[method-assign,assignment]  # int and timestamp, as DuckDB 2.0 and Spark say
    DataFrame.collect = _collect_structs_as_rows(DataFrame.collect)  # type: ignore[method-assign]
    DataFrame.select = _global_select(_decimal_select(DataFrame.select))  # type: ignore[method-assign]
    DataFrame.withColumn = _decimal_with_column(DataFrame.withColumn)  # type: ignore[method-assign]
    DataFrame.withColumns = _decimal_with_columns(DataFrame.withColumns)  # type: ignore[method-assign]
    DataFrame.selectExpr = _select_expr  # type: ignore[attr-defined]
    DataFrame.fillna = _fillna  # type: ignore[attr-defined]
    DataFrame.dropna = _dropna  # type: ignore[attr-defined]
    DataFrame.replace = _replace  # type: ignore[attr-defined]
    DataFrame.na = property(DataFrameNaFunctions)  # type: ignore[attr-defined]
    DataFrame.describe = _describe  # type: ignore[attr-defined]
    DataFrame.summary = _summary  # type: ignore[attr-defined]
    DataFrame.drop = _drop_cols(DataFrame.drop)  # type: ignore[method-assign]
    DataFrame.withColumnRenamed = _with_column_renamed(DataFrame.withColumnRenamed)  # type: ignore[method-assign]
    DataFrame.dropDuplicates = DataFrame.drop_duplicates = _drop_duplicates(DataFrame.dropDuplicates)  # type: ignore[method-assign]
    DataFrame.sample = _sample  # type: ignore[attr-defined]
    DataFrame.subtract = _subtract  # type: ignore[attr-defined]
    DataFrame.filter = DataFrame.where = _row_filter(DataFrame.filter)  # type: ignore[method-assign]
    DataFrame.unpivot = DataFrame.melt = _unpivot  # type: ignore[attr-defined]
    DataFrame.join = _same_self(DataFrame.join)  # type: ignore[method-assign]
    DataFrame.crossJoin = _same_self(DataFrame.crossJoin)  # type: ignore[method-assign]
    for name in ("repartition", "repartitionByRange", "coalesce", "persist", "unpersist", "checkpoint",
                 "localCheckpoint", "hint"):
        if not hasattr(DataFrame, name):
            setattr(DataFrame, name, _same)
    GroupedData.pivot = _pivot  # type: ignore[attr-defined]
    GroupedData.agg = _grouped_agg(GroupedData.agg)  # type: ignore[method-assign]
    DataFrame.select = _naming(DataFrame.select)  # type: ignore[method-assign]
    DataFrame.agg = _naming(DataFrame.agg)  # type: ignore[attr-defined]
    GroupedData.agg = _naming(GroupedData.agg)  # type: ignore[method-assign]
    for name in ("max", "min", "sum", "avg", "mean"):
        setattr(GroupedData, name, _numeric_aggregate(name))
    # spark.conf is spark_api.LocalRuntimeConfig, set on each session (spark_local).
    for owner, names in ((DataFrame, ("groupBy", "groupby", "orderBy", "sort", "agg")), (GroupedData, ("agg",))):
        for name in names:
            if hasattr(owner, name):
                setattr(owner, name, _refusing_struct_edits(getattr(owner, name)))
    DataFrame._fd_compat = True  # type: ignore[attr-defined]
