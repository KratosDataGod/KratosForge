"""Python over whole batches and groups on this PC's Spark (Fabric notebook parity F2):
``DataFrame.mapInPandas``, ``mapInArrow``, ``foreach``, ``foreachPartition``,
``groupBy(...).applyInPandas`` and ``groupBy(...).cogroup(...).applyInPandas``.

On Spark these run in Python workers next to each partition. Here everything is one process, so the
DataFrame's rows come out of DuckDB in Arrow batches (``spark.sql.execution.arrow.maxRecordsPerBatch``,
10,000 by default, as in Spark) and the function's output is collected into a new DataFrame with the
schema given, as in Spark. The output is held in memory before it becomes a DataFrame, so a function
that turns a large table into a larger one needs the memory for it. Nothing here reaches Fabric.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from typing import Any


def _schema(schema: Any) -> Any:
    from duckdb.experimental.spark.sql import types as T
    if isinstance(schema, str):
        from .spark_local import ddl_schema
        return ddl_schema(schema)
    if isinstance(schema, T.StructType):
        return schema
    raise TypeError("schema is a StructType or a DDL string such as \"id long, name string\".")


def _batch_rows(df: Any) -> int:
    try:
        value = int(df.session.conf.get("spark.sql.execution.arrow.maxRecordsPerBatch", "10000"))
    except (TypeError, ValueError):
        value = 10000
    return value if value > 0 else 10000


def _batches(df: Any) -> Iterator[Any]:
    """The DataFrame's rows as pyarrow RecordBatches."""
    reader = df.relation.to_arrow_reader(_batch_rows(df))
    yield from reader


def _frame(df: Any, pdf: Any, schema: Any) -> Any:
    """A pandas DataFrame as a Spark DataFrame with ``schema``: its columns are matched by name when
    they all are there, else by position, as in Spark."""
    import pandas as pd
    names = [f.name for f in schema.fields]
    if pdf is None or len(pdf.columns) == 0:
        pdf = pd.DataFrame({n: [] for n in names})
    if set(names) <= {str(c) for c in pdf.columns}:
        pdf = pdf[names]
    elif len(pdf.columns) != len(names):
        raise ValueError(f"The function returned {len(pdf.columns)} columns ({', '.join(map(str, pdf.columns))}), "
                         f"but the schema has {len(names)} ({', '.join(names)}).")
    else:
        pdf = pdf.set_axis(names, axis=1)
    if pdf.empty:
        return df.session.createDataFrame([], schema)
    return df.session.createDataFrame(pdf.astype(object).where(pdf.notna(), None), schema)


def map_in_pandas(self: Any, func: Callable[[Iterator[Any]], Iterator[Any]], schema: Any, barrier: bool = False) -> Any:  # noqa: ARG001
    """``func`` gets an iterator of pandas DataFrames (one per batch) and yields pandas DataFrames."""
    import pandas as pd
    out_schema = _schema(schema)
    parts = list(func(b.to_pandas() for b in _batches(self)))
    for p in parts:
        if not isinstance(p, pd.DataFrame):
            raise TypeError(f"mapInPandas' function must yield pandas DataFrames, not {type(p).__name__}.")
    return _frame(self, pd.concat(parts, ignore_index=True) if parts else None, out_schema)


def map_in_arrow(self: Any, func: Callable[[Iterator[Any]], Iterator[Any]], schema: Any, barrier: bool = False) -> Any:  # noqa: ARG001
    """``func`` gets an iterator of pyarrow RecordBatches and yields RecordBatches."""
    import pyarrow as pa
    out_schema = _schema(schema)
    parts = list(func(_batches(self)))
    for p in parts:
        if not isinstance(p, pa.RecordBatch):
            raise TypeError(f"mapInArrow's function must yield pyarrow RecordBatches, not {type(p).__name__}.")
    if not parts:
        return _frame(self, None, out_schema)
    return _frame(self, pa.Table.from_batches(parts).to_pandas(), out_schema)


def _rows(df: Any) -> Iterator[Any]:
    from duckdb.experimental.spark.sql.types import Row
    names = df.columns
    for batch in _batches(df):
        for values in zip(*(c.to_pylist() for c in batch.columns), strict=True):
            yield Row(**dict(zip(names, values, strict=True)))


def foreach(self: Any, f: Callable[[Any], Any]) -> None:
    """``f`` is called with each Row, in this process."""
    for row in _rows(self):
        f(row)


def foreach_partition(self: Any, f: Callable[[Iterator[Any]], Any]) -> None:
    """``f`` is called once with an iterator over every Row: on one PC the whole DataFrame is one partition."""
    f(_rows(self))


def apply_in_pandas(self: Any, func: Callable[..., Any], schema: Any) -> Any:
    """``groupBy(...).applyInPandas(func, schema)``: ``func(pdf)`` or ``func(key, pdf)`` for each group,
    where ``key`` is a tuple of the group's values, as in Spark."""
    import pandas as pd
    df = self._df
    keys = [_key_name(k) for k in self._grouping_names()]
    out_schema = _schema(schema)
    whole = df.relation.df()  # pandas, with DuckDB's own conversion of each type
    missing = [k for k in keys if k not in whole.columns]
    if missing:
        raise ValueError(
            "applyInPandas on this PC groups by column names, and " + _shown(missing) + " isn't a column. "
            "Add it as a column first, for example df.withColumn('k', expr).groupBy('k').applyInPandas(...).")
    try:
        takes_key = len(inspect.signature(func).parameters) == 2
    except (TypeError, ValueError):
        takes_key = False
    parts = []
    if whole.empty:
        return _frame(df, None, out_schema)
    for key, group in whole.groupby(keys, dropna=False, sort=True):
        key_t = key if isinstance(key, tuple) else (key,)
        key_t = tuple(None if pd.isna(k) else k for k in key_t)
        out = func(key_t, group.reset_index(drop=True)) if takes_key else func(group.reset_index(drop=True))
        if not isinstance(out, pd.DataFrame):
            raise TypeError(f"applyInPandas' function must return a pandas DataFrame, not {type(out).__name__}.")
        parts.append(out)
    return _frame(df, pd.concat(parts, ignore_index=True) if parts else None, out_schema)


def _groups(gd: Any) -> tuple[Any, dict[tuple[Any, ...], Any]]:
    """A GroupedData's rows (pandas) and its groups, by key tuple (a null key part as None)."""
    import pandas as pd
    keys = [_key_name(k) for k in gd._grouping_names()]
    whole = gd._df.relation.df()
    missing = [k for k in keys if k not in whole.columns]
    if missing:
        raise ValueError(
            "cogroup on this PC groups by column names, and " + _shown(missing) + " isn't a column. "
            "Add it as a column first, for example df.withColumn('k', expr).groupBy('k').")
    groups = {}
    if not whole.empty:
        for key, group in whole.groupby(keys, dropna=False, sort=True):
            key_t = key if isinstance(key, tuple) else (key,)
            groups[tuple(None if pd.isna(k) else k for k in key_t)] = group.reset_index(drop=True)
    return whole, groups


class CogroupedData:
    """``groupBy(...).cogroup(other.groupBy(...))``, as Spark's PandasCogroupedOps."""

    def __init__(self, gd1: Any, gd2: Any):
        self._gd1, self._gd2 = gd1, gd2

    def applyInPandas(self, func: Callable[..., Any], schema: Any) -> Any:  # noqa: N802
        """``func(left, right)`` or ``func(key, left, right)`` for each key either side has, where a
        side without the key is an empty pandas DataFrame with its columns, as in Spark."""
        import pandas as pd
        out_schema = _schema(schema)
        left, lgroups = _groups(self._gd1)
        right, rgroups = _groups(self._gd2)
        try:
            takes_key = len(inspect.signature(func).parameters) == 3
        except (TypeError, ValueError):
            takes_key = False
        parts = []
        for key in list(lgroups) + [k for k in rgroups if k not in lgroups]:
            lpdf = lgroups.get(key, left.iloc[0:0])
            rpdf = rgroups.get(key, right.iloc[0:0])
            out = func(key, lpdf, rpdf) if takes_key else func(lpdf, rpdf)
            if not isinstance(out, pd.DataFrame):
                raise TypeError(f"cogroup's applyInPandas function must return a pandas DataFrame, not "
                                f"{type(out).__name__}.")
            if not out.empty:  # pandas warns when an empty one is concatenated
                parts.append(out)
        return _frame(self._gd1._df, pd.concat(parts, ignore_index=True) if parts else None, out_schema)


def cogroup(self: Any, other: Any) -> CogroupedData:
    """``groupBy(...).cogroup(other)``: both sides' groups, by key, for applyInPandas."""
    from duckdb.experimental.spark.sql.group import GroupedData
    if not isinstance(other, GroupedData):
        raise TypeError(f"cogroup takes another DataFrame's groupBy(...), not {type(other).__name__}.")
    return CogroupedData(self, other)


def _shown(names: list[str]) -> str:
    """Grouping names for a message, without the name DuckDB gave the DataFrame's relation, which differs
    from run to run."""
    import re
    return ", ".join(re.sub(r"\b[a-z]+_object_[0-9a-f]+\.", "", n) for n in names)


def _key_name(k: Any) -> str:
    return str(k).strip('"')


def _grouping_names(self: Any) -> list[str]:
    """The grouped columns' names, from GroupedData's grouping (column names or Columns)."""
    import duckdb
    from duckdb.experimental.spark.sql.column import Column
    grouping = getattr(self, "_grouping", None)
    cols = getattr(grouping, "_cols", None) or getattr(grouping, "cols", None) or grouping
    names = []
    for c in cols if isinstance(cols, (list, tuple)) else [cols]:
        if isinstance(c, str):
            names.append(c)
        elif isinstance(c, Column):
            names.append(c.expr.get_name().strip('"'))
        elif isinstance(c, duckdb.Expression):
            names.append(c.get_name().strip('"'))
        elif c is not None:
            names.append(str(c).strip('"'))
    if not names:
        raise ValueError("applyInPandas needs groupBy with at least one column.")
    return names


def install() -> None:
    from duckdb.experimental.spark.sql.dataframe import DataFrame
    from duckdb.experimental.spark.sql.group import GroupedData
    DataFrame.mapInPandas = map_in_pandas  # type: ignore[attr-defined]
    DataFrame.mapInArrow = map_in_arrow  # type: ignore[attr-defined]
    DataFrame.foreach = foreach  # type: ignore[attr-defined]
    DataFrame.foreachPartition = foreach_partition  # type: ignore[attr-defined]
    GroupedData._grouping_names = _grouping_names  # type: ignore[attr-defined]
    GroupedData.applyInPandas = apply_in_pandas  # type: ignore[attr-defined]
    GroupedData.cogroup = cogroup  # type: ignore[attr-defined]
