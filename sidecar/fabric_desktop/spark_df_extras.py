"""PySpark DataFrame calls about a DataFrame rather than its rows, which DuckDB's ``spark`` lacks:
storage levels, comparing plans and observed metrics. Nothing here starts Spark.

- ``pyspark.storagelevel.StorageLevel`` (and ``pyspark.StorageLevel``) with Spark's levels, printed
  as Spark prints them. ``df.persist(level)``, ``df.cache()`` and ``df.unpersist()`` record the level,
  and ``df.storageLevel`` reads it back. One PC has no executors to keep blocks on, so the level is
  only recorded: ``cache()`` still runs the query once, as before, and ``persist`` keeps nothing.
- ``df.sameSemantics(other)`` and ``df.semanticHash()``: two DataFrames are the same when their
  DuckDB plans are, apart from the names DuckDB makes up for each DataFrame and the names of the
  output columns (Spark ignores those too). Like Spark's, the check can say False for two
  DataFrames that give the same rows (two ``createDataFrame`` calls with the same data, say), and
  never True for two that differ. The hash is this PC's own number, not the one Spark prints.
- ``pyspark.sql.Observation`` and ``df.observe(observation, *exprs)``: ``observation.get`` gives each
  metric over the observed DataFrame's rows. Spark computes them during the DataFrame's first action,
  and its ``get`` waits for that action; here ``get`` runs one aggregate query when first read and
  keeps the answer. ``df.observe("name", ...)`` with a name, whose metrics only reach a listener,
  is refused: listeners run in Fabric. ``StreamingQueryListener`` imports, so a notebook that defines
  one still runs, and ``spark.streams`` refuses by name (it is DuckDB's not-implemented error without it).

spark_local.install() calls ``install()`` after spark_api's and spark_stream's.
"""

from __future__ import annotations

import re
import sys
import types
import zlib
from typing import Any, cast

from duckdb.experimental.spark.sql.dataframe import DataFrame

_LEVEL = "_fd_storage_level"


class StorageLevel:
    """Where Spark keeps a persisted DataFrame's blocks: as PySpark 3.5's, with its levels and text."""

    DISK_ONLY: StorageLevel
    DISK_ONLY_2: StorageLevel
    DISK_ONLY_3: StorageLevel
    MEMORY_ONLY: StorageLevel
    MEMORY_ONLY_2: StorageLevel
    MEMORY_AND_DISK: StorageLevel
    MEMORY_AND_DISK_2: StorageLevel
    OFF_HEAP: StorageLevel
    MEMORY_AND_DISK_DESER: StorageLevel
    NONE: StorageLevel

    def __init__(self, useDisk: bool, useMemory: bool, useOffHeap: bool, deserialized: bool,  # noqa: N803
                 replication: int = 1):
        self.useDisk = useDisk  # noqa: N815 - PySpark's names
        self.useMemory = useMemory  # noqa: N815
        self.useOffHeap = useOffHeap  # noqa: N815
        self.deserialized = deserialized
        self.replication = replication

    def _key(self) -> tuple[bool, bool, bool, bool, int]:
        return (self.useDisk, self.useMemory, self.useOffHeap, self.deserialized, self.replication)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, StorageLevel) and self._key() == other._key()

    __hash__ = None  # type: ignore[assignment]  # as PySpark's: equal levels compare, but don't hash

    def __repr__(self) -> str:
        return "StorageLevel({}, {}, {}, {}, {})".format(*self._key())

    def __str__(self) -> str:
        text = "Disk " if self.useDisk else ""
        text += "Memory " if self.useMemory else ""
        text += "OffHeap " if self.useOffHeap else ""
        text += "Deserialized " if self.deserialized else "Serialized "
        return text + f"{self.replication}x Replicated"


StorageLevel.DISK_ONLY = StorageLevel(True, False, False, False)
StorageLevel.DISK_ONLY_2 = StorageLevel(True, False, False, False, 2)
StorageLevel.DISK_ONLY_3 = StorageLevel(True, False, False, False, 3)
StorageLevel.MEMORY_ONLY = StorageLevel(False, True, False, False)
StorageLevel.MEMORY_ONLY_2 = StorageLevel(False, True, False, False, 2)
StorageLevel.MEMORY_AND_DISK = StorageLevel(True, True, False, False)
StorageLevel.MEMORY_AND_DISK_2 = StorageLevel(True, True, False, False, 2)
StorageLevel.OFF_HEAP = StorageLevel(True, True, True, False, 1)
StorageLevel.MEMORY_AND_DISK_DESER = StorageLevel(True, True, False, True)
StorageLevel.NONE = StorageLevel(False, False, False, False)


def _storage_level(self: DataFrame) -> StorageLevel:
    return getattr(self, _LEVEL, StorageLevel.NONE)


def persist(self: DataFrame, storageLevel: StorageLevel = StorageLevel.MEMORY_AND_DISK_DESER) -> DataFrame:  # noqa: N803
    if not isinstance(storageLevel, StorageLevel):
        raise TypeError(f"persist() needs a StorageLevel, such as StorageLevel.MEMORY_AND_DISK, "
                        f"not {type(storageLevel).__name__}.")
    setattr(self, _LEVEL, storageLevel)
    return self


def _cache(original: Any) -> Any:
    def cache(self: DataFrame) -> DataFrame:
        out = original(self)
        setattr(self, _LEVEL, StorageLevel.MEMORY_AND_DISK_DESER)
        setattr(out, _LEVEL, StorageLevel.MEMORY_AND_DISK_DESER)
        return out
    cache.__doc__ = original.__doc__
    cache._fd_level = True  # type: ignore[attr-defined]  # installed once
    return cache


def unpersist(self: DataFrame, blocking: bool = False) -> DataFrame:  # noqa: ARG001 - nothing to wait for here
    setattr(self, _LEVEL, StorageLevel.NONE)
    return self


# ---- sameSemantics and semanticHash

# The names DuckDB and this PC make up for a DataFrame's relation; they differ for the same plan.
_MADE_UP = re.compile(r"\b(?:unnamed_relation|arrow_object|fd_right)_[0-9a-f]+\b")


def _canonical_plan(df: DataFrame) -> str:
    """``df``'s query with made-up relation names numbered in the order they appear and the output
    columns' names left out (only the outermost SELECT's: an inner name is how an outer column
    refers to it, so leaving it out could make two different plans look the same)."""
    seen: dict[str, str] = {}
    text = _MADE_UP.sub(lambda m: seen.setdefault(m.group(0), f"fd_rel_{len(seen)}"), df.relation.sql_query())
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(text, read="duckdb")
    except Exception:  # noqa: BLE001  # silent-ok: an unparsed plan compares by its text, which only adds False answers
        return text
    if isinstance(tree, exp.Select):
        tree.set("expressions", [e.this if isinstance(e, exp.Alias) else e for e in tree.expressions])
    from .sql import duckdb_sql
    try:
        return duckdb_sql(tree)  # never a plan with a part left out, which could match another plan
    except ValueError:  # silent-ok: a plan sqlglot can't write whole compares by its text, which only adds False answers
        return text


def sameSemantics(self: DataFrame, other: DataFrame) -> bool:  # noqa: N802
    if not isinstance(other, DataFrame):
        raise TypeError(f"Argument `other` should be a DataFrame, got {type(other).__name__}.")
    return _canonical_plan(self) == _canonical_plan(other)


def semanticHash(self: DataFrame) -> int:  # noqa: N802
    """A number that is the same for DataFrames sameSemantics calls the same (a signed 32-bit int,
    as Spark's): this PC's own, so it won't match the number Spark gives."""
    value = zlib.crc32(_canonical_plan(self).encode("utf-8"))
    return value - (1 << 32) if value >= 1 << 31 else value


# ---- Observation and observe

class Observation:
    """Named metrics over a DataFrame, as PySpark 3.5's ``pyspark.sql.Observation``."""

    def __init__(self, name: str | None = None):
        if name is not None:
            if not isinstance(name, str):
                raise TypeError(f"Observation's name should be a str, got {type(name).__name__}.")
            if name == "":
                raise ValueError("Observation's name should not be empty.")
        self._name = name
        self._df: DataFrame | None = None
        self._exprs: tuple[Any, ...] = ()
        self._result: dict[str, Any] | None = None

    def _on(self, df: DataFrame, exprs: tuple[Any, ...]) -> DataFrame:
        if self._df is not None:
            raise RuntimeError("An Observation can be used with a DataFrame only once.")
        self._df, self._exprs = df, exprs
        return df

    @property
    def get(self) -> dict[str, Any]:
        """Each metric by its name, over every row of the observed DataFrame."""
        if self._df is None:
            raise RuntimeError("This Observation isn't on a DataFrame yet: call df.observe(observation, ...) "
                               "and an action on the DataFrame it returns.")
        if self._result is None:
            row = cast(Any, self._df).agg(*self._exprs).collect()[0]  # spark_compat's agg; DuckDB's stub types it as a Column
            self._result = row.asDict(recursive=False)
        return dict(self._result)


def observe(self: DataFrame, observation: Any, *exprs: Any) -> DataFrame:
    from duckdb.experimental.spark.sql.column import Column
    if not exprs:
        raise ValueError("observe() needs at least one metric, such as count(lit(1)).alias('rows').")
    if not all(isinstance(e, Column) for e in exprs):
        raise TypeError("observe()'s metrics should be Columns, such as max(col('age')).")
    if isinstance(observation, Observation):
        return observation._on(self, exprs)
    if isinstance(observation, str):
        raise NotImplementedError("observe() with a name sends its metrics to a listener, and listeners run "
                                  "in Fabric. On this PC, pass an Observation and read observation.get.")
    raise TypeError(f"observe() needs an Observation or a name, got {type(observation).__name__}.")


class StreamingQueryListener:
    """PySpark's base class for a streaming listener. A notebook can define one here; adding it with
    ``spark.streams.addListener`` is refused, because listeners run in Fabric."""

    def onQueryStarted(self, event: Any) -> None:  # noqa: N802, B027 - PySpark's hooks, empty by default
        pass

    def onQueryProgress(self, event: Any) -> None:  # noqa: N802, B027
        pass

    def onQueryIdle(self, event: Any) -> None:  # noqa: N802, B027
        pass

    def onQueryTerminated(self, event: Any) -> None:  # noqa: N802, B027
        pass


class _Streams:
    """``spark.streams``: every call is refused by name."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        raise NotImplementedError(f"spark.streams.{name} runs in Fabric: stream listeners and the query manager "
                                  "aren't on this PC. A stream started here is managed by the query that "
                                  "writeStream.start() returns.")


ADDED: dict[str, Any] = {
    "persist": persist,
    "unpersist": unpersist,
    "sameSemantics": sameSemantics,
    "semanticHash": semanticHash,
    "observe": observe,
}


def install() -> None:
    """Add the calls to DuckDB's DataFrame and the classes to ``pyspark`` (once)."""
    for name, fn in ADDED.items():
        setattr(DataFrame, name, fn)  # replaces spark_compat's do-nothing persist and unpersist
    if not getattr(DataFrame.cache, "_fd_level", False):
        DataFrame.cache = _cache(DataFrame.cache)  # type: ignore[method-assign]
    if "storageLevel" not in DataFrame.__dict__:
        DataFrame.storageLevel = property(_storage_level)  # type: ignore[attr-defined]
    import duckdb.experimental.spark as spark_pkg
    import duckdb.experimental.spark.sql as spark_sql
    import duckdb.experimental.spark.sql.streaming as spark_streaming

    from .spark_local import LocalSparkSession
    setattr(LocalSparkSession, "streams", property(lambda self: _Streams()))  # noqa: B010 - a patch mypy refuses as an assignment
    spark_streaming.StreamingQueryListener = StreamingQueryListener  # type: ignore[attr-defined]
    spark_sql.Observation = Observation  # type: ignore[attr-defined]
    spark_pkg.StorageLevel = StorageLevel  # type: ignore[attr-defined]
    module = sys.modules.get("duckdb.experimental.spark.storagelevel") or types.ModuleType("pyspark.storagelevel")
    module.StorageLevel = StorageLevel  # type: ignore[attr-defined]
    spark_pkg.storagelevel = module  # type: ignore[attr-defined]
    if sys.modules.get("pyspark") is spark_pkg:  # DuckDB's API stands in for PySpark (spark_local._alias_pyspark)
        sys.modules.setdefault("pyspark.storagelevel", module)
        sys.modules.setdefault("pyspark.sql.streaming", spark_streaming)  # else imported afresh, without the listener
