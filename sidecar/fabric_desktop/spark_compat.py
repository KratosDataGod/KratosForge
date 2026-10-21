"""PySpark calls DuckDB's Spark API doesn't have yet, so Fabric and Databricks notebooks run here.

``spark`` on this PC is DuckDB's PySpark-compatible API (spark_local). It covers most DataFrame
work but leaves out window functions, ``df.agg``, ``selectExpr``, ``fillna``/``na``, ``pivot``
and a set of functions that are everyday Spark (``row_number``, ``explode``, ``date_format``...).
This module adds them on top of DuckDB SQL: each function is written as the Spark SQL call it
is and translated with sqlglot (the translator the SQL page uses), so its meaning follows Spark's.
Nothing here writes anywhere; it only builds queries. ``install()`` is called by spark_local.
"""

from __future__ import annotations

import itertools
import re
import sys
import types
from typing import Any, Callable

import duckdb
import sqlglot
from duckdb.experimental.spark.sql import functions as F
from duckdb.experimental.spark.sql.column import Column
from duckdb.experimental.spark.sql.dataframe import DataFrame
from duckdb.experimental.spark.sql.group import GroupedData
from sqlglot import exp

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
    return sqlglot.transpile(text, read="spark", write="duckdb")[0]


def _call(spark_sql: str, *cols: Any) -> Column:
    """A Spark SQL call with ``{0}``, ``{1}``... for columns, translated to DuckDB SQL."""
    holes = [f"__fdc{i}__" for i in range(len(cols))]
    text = spark_expr(spark_sql.format(*holes))
    for hole, c in zip(holes, cols):
        text = re.sub(rf'"?{hole}"?', lambda _m, c=c: f"({_col_sql(c)})" if _col_sql(c) != "*" else "*", text)
    return _sql(text)


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


def months_between(date1: Any, date2: Any, roundOff: bool = True) -> Column:  # noqa: N803, ARG001
    return _call("months_between({0}, {1})", date1, date2)


def from_unixtime(timestamp: Any, format: str = "yyyy-MM-dd HH:mm:ss") -> Column:  # noqa: A002
    return _call(f"from_unixtime({{0}}, {_lit(format)})", timestamp)


def unix_timestamp(timestamp: Any = None, format: str = "yyyy-MM-dd HH:mm:ss") -> Column:  # noqa: A002
    if timestamp is None:
        return _sql("CAST(epoch(current_timestamp) AS BIGINT)")
    return _sql(f"CAST(({_call(f'unix_timestamp({{0}}, {_lit(format)})', timestamp).expr}) AS BIGINT)")


def size(col: Any) -> Column:
    return _call("size({0})", col)


def element_at(col: Any, extraction: Any) -> Column:
    return _call(f"element_at({{0}}, {_lit(extraction)})", col)


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


def uuid() -> Column:
    return _sql("CAST(uuid() AS VARCHAR)")


def nanvl(col1: Any, col2: Any) -> Column:
    return _sql(f"CASE WHEN isnan({_col_sql(col1)}) THEN {_col_sql(col2)} ELSE {_col_sql(col1)} END")


def format_number(col: Any, d: int) -> Column:
    # Spark: #,###,###.## rounded half-even to d places (2.345 -> 2.34, 0.125 -> 0.12).
    return _sql(f"printf('%,.{int(d)}f', round_even({_col_sql(col)}, {int(d)}))")


def to_json(col: Any, options: Any = None) -> Column:
    if options:
        print(f"Note: to_json options ({', '.join(options)}) are ignored on this PC.")
    return _call("to_json({0})", col)


def expr(text: str) -> Column:
    """A Spark SQL expression. ``AS name`` names the column, as in Spark."""
    parsed = sqlglot.parse_one(text, read="spark")
    if isinstance(parsed, exp.Alias):
        return _sql(parsed.this.sql("duckdb")).alias(parsed.alias)
    return _sql(parsed.sql("duckdb"))


_count = F.count


def count(col: Any) -> Column:
    """``count("*")`` counts rows, as in Spark."""
    return _sql("count(*)") if isinstance(col, str) and col == "*" else _count(col)


FUNCTIONS = {name: obj for name, obj in list(globals().items())
             if callable(obj) and not name.startswith("_") and getattr(obj, "__module__", "") == __name__
             and name not in ("spark_expr", "install")}
FUNCTIONS |= {"countDistinct": count_distinct, "sumDistinct": sum_distinct, "count": count}


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
            parts.append("ORDER BY " + ", ".join(f"{_col_sql(c)} {getattr(c, '_fd_order', 'ASC NULLS FIRST')}"
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


def _ordered(name: str, fn: Any) -> Any:
    def order(self: Column) -> Column:
        out = fn(self)
        out._fd_order = _ORDERS[name]
        return out
    return order


def _over(self: Column, window: WindowSpec) -> Column:
    return _sql(f"{self.expr} OVER ({window.sql()})")


# ---- DataFrame

_view_ids = itertools.count(1)


def _named(df: DataFrame, sql: Callable[[str], str]) -> DataFrame:
    """A DataFrame from SQL over this one; ``sql(name)`` is the query, reading this one as ``name``.

    DuckDB keeps the name as a view on the session, and a later query under the same name would
    change what this DataFrame returns (or hide a temp view the notebook named the same), so each
    gets a name of its own."""
    name = f"__fd_df_{next(_view_ids)}"
    return DataFrame(df.relation.query(name, sql(name)), df.session)


def _agg(self: DataFrame, *exprs: Any) -> DataFrame:
    if len(exprs) == 1 and isinstance(exprs[0], dict):
        exprs = tuple(_dict_agg(exprs[0]))
    return DataFrame(self.relation.aggregate([c.expr for c in exprs]), self.session)


def _dict_agg(spec: dict[str, str]) -> list[Column]:
    names = {"avg": "avg", "mean": "avg", "sum": "sum", "min": "min", "max": "max", "count": "count", "first": "first",
             "last": "last", "stddev": "stddev_samp", "variance": "var_samp"}
    out = []
    for column, fn in spec.items():
        if fn.lower() not in names:
            raise ValueError(f"agg doesn't know {fn!r}. Use one of: {', '.join(sorted(names))}.")
        out.append(_sql(f"{names[fn.lower()]}({_col_sql(column)})").alias(f"{fn.lower()}({column})"))
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


def _fillna(self: DataFrame, value: Any, subset: Any = None) -> DataFrame:
    if isinstance(value, dict):
        fills = value
    else:
        if not isinstance(value, (bool, int, float, str)):
            raise TypeError("fillna takes a number, text, a boolean or a dict of column to value.")
        wanted = [subset] if isinstance(subset, str) else list(subset or [c for c, _ in _types(self)])
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
    return [(name, str(t)) for name, t in zip(df.relation.columns, df.relation.types)]


def _dropna(self: DataFrame, how: str = "any", thresh: int | None = None, subset: Any = None) -> DataFrame:
    cols = [subset] if isinstance(subset, str) else list(subset or self.columns)
    if not cols:
        return self
    present = " + ".join(f"CAST({_col_sql(c)} IS NOT NULL AS INT)" for c in cols)
    need = thresh if thresh is not None else (len(cols) if how == "any" else 1)
    return _named(self, lambda t: f"SELECT * FROM {t} WHERE ({present}) >= {int(need)}")


def _replace(self: DataFrame, to_replace: Any, value: Any = None, subset: Any = None) -> DataFrame:
    mapping = to_replace if isinstance(to_replace, dict) else (
        dict(zip(to_replace, value if isinstance(value, (list, tuple)) else [value] * len(to_replace)))
        if isinstance(to_replace, (list, tuple)) else {to_replace: value})
    wanted = [subset] if isinstance(subset, str) else list(subset or self.columns)
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
    names = list(_flat(cols)) or [c for c, t in _types(self) if _NUMERIC.match(t) or t.upper() == "VARCHAR"]
    types_ = dict(_types(self))
    rows = []
    for stat in ("count", "mean", "stddev", "min", "max"):
        parts = []
        for c in names:
            q = _col_sql(c)
            numeric = bool(_NUMERIC.match(types_[c]))
            sql = {"count": f"count({q})", "mean": f"avg({q})" if numeric else "NULL",
                   "stddev": f"stddev_samp({q})" if numeric else "NULL", "min": f"min({q})", "max": f"max({q})"}[stat]
            parts.append(f"CAST({sql} AS VARCHAR) AS {q}")
        rows.append(f"SELECT '{stat}' AS summary, {', '.join(parts)}")
    return _named(self, lambda t: " UNION ALL ".join(f"{row} FROM {t}" for row in rows))


def _sample(self: DataFrame, withReplacement: Any = None, fraction: float | None = None,  # noqa: N803
            seed: int | None = None) -> DataFrame:
    if withReplacement is not None and not isinstance(withReplacement, bool):  # sample(0.1) and sample(0.1, 42)
        withReplacement, fraction, seed = None, withReplacement, fraction  # noqa: N806
    if withReplacement:
        raise NotImplementedError("sample(withReplacement=True) isn't supported on this PC. Leave it out to sample without replacement.")
    pct = max(0.0, min(100.0, float(fraction or 0) * 100))
    repeat = f", {int(seed)}" if seed is not None else ""
    return _named(self, lambda t: f"SELECT * FROM {t} USING SAMPLE {pct}% (bernoulli{repeat})")


def _subtract(self: DataFrame, other: DataFrame) -> DataFrame:
    return self.distinct().exceptAll(other)


def _unpivot(self: DataFrame, ids: Any, values: Any, variableColumnName: str, valueColumnName: str) -> DataFrame:  # noqa: N803
    ids = [ids] if isinstance(ids, (str, Column)) else list(ids)
    values = [values] if isinstance(values, (str, Column)) else list(values or [c for c in self.columns if c not in ids])
    return _named(self, lambda t: f"SELECT {', '.join(_col_sql(c) for c in ids)}, {_col_sql(variableColumnName)}, "
                                  f"{_col_sql(valueColumnName)} FROM (UNPIVOT {t} ON {', '.join(_col_sql(c) for c in values)} "
                                  f"INTO NAME {_col_sql(variableColumnName)} VALUE {_col_sql(valueColumnName)})")


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


def install() -> None:
    """Add the calls above to DuckDB's Spark API (once per process)."""
    if getattr(DataFrame, "_fd_compat", False):
        return
    for name, fn in FUNCTIONS.items():
        if name == "count" or not hasattr(F, name):
            setattr(F, name, fn)
    F.expr = expr  # Spark SQL, translated (DuckDB's own expr takes DuckDB SQL)
    Column.over = _over  # type: ignore[attr-defined]
    for name in _ORDERS:
        setattr(Column, name, _ordered(name, getattr(Column, name)))
    DataFrame.agg = _agg  # type: ignore[attr-defined]
    DataFrame.select = _global_select(DataFrame.select)  # type: ignore[method-assign]
    DataFrame.selectExpr = _select_expr  # type: ignore[attr-defined]
    DataFrame.fillna = _fillna  # type: ignore[attr-defined]
    DataFrame.dropna = _dropna  # type: ignore[attr-defined]
    DataFrame.replace = _replace  # type: ignore[attr-defined]
    DataFrame.na = property(DataFrameNaFunctions)  # type: ignore[attr-defined]
    DataFrame.describe = _describe  # type: ignore[attr-defined]
    DataFrame.summary = _describe  # type: ignore[attr-defined]
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
    # spark.conf is spark_api.LocalRuntimeConfig, set on each session (spark_local).
    DataFrame._fd_compat = True  # type: ignore[attr-defined]
