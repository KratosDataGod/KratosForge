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
    text = str(c.expr)
    if len(text) > 1 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text.strip('"').replace('""', '"')


def _lit(value: Any) -> str:
    return exp.convert(value).sql("spark")


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
    """A Spark SQL expression. ``AS name`` names the column, as in Spark."""
    parsed = spark_sql.spark_legacy_times(spark_sql.spark_substring(spark_sql.spark_integer_casts(_parse(text))))
    if isinstance(parsed, exp.Alias):
        return _sql(spark_sql.duckdb_sql(parsed.this)).alias(parsed.alias)
    return _sql(spark_sql.duckdb_sql(parsed))


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


def _get_item(self: Column, key: Any) -> Column:
    """``col.getItem(key)`` and ``col[key]``: a whole number is an array's 0-based position (NULL
    outside the array, as Spark gives without ANSI mode); text is a map's key or a struct's field."""
    if isinstance(key, Column):
        raise NotImplementedError("getItem with a column doesn't run on this PC yet: "
                                  "use F.element_at(col, position + 1) for an array, or a map's key as text")
    if isinstance(key, bool) or not isinstance(key, (int, str)):
        raise TypeError(f"getItem takes a whole number or text, not {type(key).__name__}")
    if isinstance(key, int):
        return _sql(f"({_col_sql(self)})[{key + 1 if key >= 0 else 0}]")
    return _sql(f"({_col_sql(self)})[{_lit(key)}]")


def _getitem(original: Any) -> Any:
    def getitem(self: Column, key: Any) -> Column:
        return original(self, key) if isinstance(key, builtins.slice) else _get_item(self, key)
    return getitem


def _get_field(self: Column, name: str) -> Column:
    """``col.getField(name)``: a struct's field."""
    if not isinstance(name, str):
        raise TypeError(f"getField takes a field name, not {type(name).__name__}")
    return _sql(f"({_col_sql(self)})[{_lit(name)}]")


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
    cols = list(cols)
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
_PERCENT = re.compile(r"^(\d+(?:\.\d+)?)%$")


def _summary(self: DataFrame, *statistics: str) -> DataFrame:
    """Spark's summary(): the statistics it names (default count, mean, stddev, min, quartiles, max) of every
    number and string column. It takes statistics, not columns; select the columns first."""
    stats = tuple(_flat(statistics)) or _SUMMARY
    for stat in stats:
        pct = _PERCENT.match(str(stat))
        if not (stat in ("count", "mean", "stddev", "min", "max", "count_distinct", "approx_count_distinct")
                or (pct and float(pct.group(1)) <= 100)):
            raise ValueError(f"{stat} is not a recognised statistic. Use count, mean, stddev, min, max, "
                             "count_distinct, approx_count_distinct or a percentile such as 75%.")
    names = [c for c, t in _types(self) if _NUMERIC.match(t) or t.upper() == "VARCHAR"]
    return _stats(self, names, stats)


def _stats(self: DataFrame, names: list[str], stats: Any) -> DataFrame:
    types_ = dict(_types(self))
    rows = []
    for stat in stats:
        parts = []
        for c in names:
            q = _col_sql(c)
            numeric = bool(_NUMERIC.match(types_[c]))
            if pct := _PERCENT.match(stat):  # Spark's approximate percentile is an actual value, as quantile_disc
                sql = f"quantile_disc({q}, {float(pct.group(1)) / 100})" if numeric else "NULL"
            else:
                sql = {"count": f"count({q})", "mean": f"avg({q})" if numeric else "NULL",
                       "stddev": f"stddev_samp({q})" if numeric else "NULL", "min": f"min({q})", "max": f"max({q})",
                       "count_distinct": f"count(DISTINCT {q})",
                       "approx_count_distinct": f"approx_count_distinct({q})"}[stat]
            parts.append(f"CAST({sql} AS VARCHAR) AS {q}")
        rows.append(self.relation.aggregate(f"'{stat}' AS summary, {', '.join(parts)}"))
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
    parts = [numbered.project(f"{id_cols + ', ' if ids else ''}{_lit(_name_of(v))} AS {_col_sql(variableColumnName)}, "
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
        if name in ("count", "substring", "concat", "initcap", "first", "last", "slice",
                    "add_months", "months_between", "element_at", "to_date", "to_timestamp") or not hasattr(F, name):
            setattr(F, name, fn)
    vars(F).update(expr=expr)  # Spark SQL, translated (DuckDB's own expr takes DuckDB SQL)
    Column.over = _over  # type: ignore[attr-defined]
    Column.getItem = _get_item  # type: ignore[attr-defined]
    Column.getField = _get_field  # type: ignore[attr-defined]
    Column.__getitem__ = _getitem(Column.__getitem__)  # type: ignore[method-assign]
    Column.__truediv__ = Column.__div__ = _spark_divide(False)  # type: ignore[method-assign,assignment]
    Column.__rtruediv__ = Column.__rdiv__ = _spark_divide(True)  # type: ignore[method-assign,assignment]
    # Each wraps the other: a whole-number target goes to _int_cast, a date or timestamp to _time_cast.
    Column.cast = _time_cast(Column.cast)  # type: ignore[method-assign]
    Column.cast = _int_cast(Column.cast)  # type: ignore[method-assign]
    for name in _ORDERS:
        setattr(Column, name, _ordered(name, getattr(Column, name)))
    DataFrame.agg = _agg  # type: ignore[attr-defined]
    DataFrame.show = _show  # type: ignore[method-assign,assignment]
    DataFrame.cube = _grouping_sets("cube")  # type: ignore[attr-defined]
    DataFrame.rollup = _grouping_sets("rollup")  # type: ignore[attr-defined]
    vars(F).update(grouping=_grouping, grouping_id=_grouping_id)
    DataFrame.dtypes = property(_dtypes)  # type: ignore[assignment,method-assign]
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
    DataFrame.unpivot = DataFrame.melt = _unpivot  # type: ignore[attr-defined]
    DataFrame.join = _same_self(DataFrame.join)  # type: ignore[method-assign]
    DataFrame.crossJoin = _same_self(DataFrame.crossJoin)  # type: ignore[method-assign]
    for name in ("repartition", "repartitionByRange", "coalesce", "persist", "unpersist", "checkpoint",
                 "localCheckpoint", "hint"):
        if not hasattr(DataFrame, name):
            setattr(DataFrame, name, _same)
    GroupedData.pivot = _pivot  # type: ignore[attr-defined]
    GroupedData.agg = _grouped_agg(GroupedData.agg)  # type: ignore[method-assign]
    # spark.conf is spark_api.LocalRuntimeConfig, set on each session (spark_local).
    DataFrame._fd_compat = True  # type: ignore[attr-defined]
