"""The ``spark`` an Academy PySpark exercise gets (DBA-003): DuckDB's PySpark API with KratosForge's
gap fillers (spark_compat), reading and writing Delta tables only under one workspace's ``tables/``.

It runs in the learner's process, never in the engine, as part of the generated ``kf_learner`` package
(academy/learner_pkg.py), which holds no sign-in, lakehouse or network code.

    spark.read.table("bronze.orders")                    tables/bronze/orders, a Delta table
    spark.table("bronze.orders")                         the same
    df.write.mode("overwrite").saveAsTable("silver.orders")   writes tables/silver/orders (Delta)

Table names are ``schema.table`` or ``table`` of letters, digits and underscores; a name can't leave
``tables/``. Modes: ``error``/``errorifexists`` (the default), ``overwrite``, ``append``, ``ignore``;
option ``overwriteSchema`` replaces the schema on overwrite, ``mergeSchema`` adds new columns on append.
Streams (``spark.readStream``, ``df.writeStream``) run as availableNow (pyspark_stream.py), ``from
delta.tables import DeltaTable`` merges with delta-rs (pyspark_delta.py), and ``notebookutils`` runs the
exercise's other notebooks and reads its ``Files/`` (notebookutils_lab.py).

This is not Apache Spark: there are no partitions, no plans and no Spark UI here.
"""

from __future__ import annotations

import re
import sys
import types as _modules
from pathlib import Path
from typing import Any, Literal

import deltalake
import duckdb
import pyarrow as pa
from duckdb.experimental.spark.context import SparkContext
from duckdb.experimental.spark.sql import dataframe as _dataframe
from duckdb.experimental.spark.sql.dataframe import DataFrame
from duckdb.experimental.spark.sql.session import SparkSession

from .. import spark_compat
from ..duckdb_rows import duckdb_major
from . import notebookutils_lab, pyspark_delta, pyspark_stream
from .notebookutils_lab import NotebookExit  # noqa: F401 - the learner script catches it

TABLES = "tables"
MODES = {"error": "error", "errorifexists": "error", "overwrite": "overwrite", "append": "append", "ignore": "ignore",
         "default": "error"}
_PART = r"[A-Za-z_][A-Za-z0-9_]{0,62}"
_NAME = re.compile(rf"^{_PART}(\.{_PART})?$")


def table_path(work: Path, name: str) -> Path:
    """Where table ``name`` lives under ``work``; refuses any name that isn't ``schema.table`` or ``table``."""
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError(f"{name!r} isn't a table name like silver.orders")
    part = Path(work) / TABLES
    for step in ("", *name.lower().split(".")):
        part = part / step if step else part
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError(f"{name!r} goes through a link; exercise tables are plain folders")
    base = (Path(work) / TABLES).resolve()
    path = (base / Path(*name.lower().split("."))).resolve()
    if base not in path.parents:
        raise ValueError(f"{name!r} isn't a table in this exercise")
    return path


def _arrow(df: DataFrame) -> pa.Table:
    rel = df.relation
    return (getattr(rel, "to_arrow_table", None) or rel.arrow)()


class Writer:
    """``df.write`` for an exercise: Delta tables under the workspace."""

    def __init__(self, df: DataFrame, work: Path):
        self._df, self._work = df, work
        self._mode = "error"
        self._options: dict[str, bool] = {}

    def mode(self, mode: str) -> Writer:
        if str(mode).lower() not in MODES:
            raise ValueError(f"Unknown save mode {mode!r}; use overwrite, append, ignore or error")
        self._mode = MODES[str(mode).lower()]
        return self

    def format(self, fmt: str) -> Writer:
        if str(fmt).lower() != "delta":
            raise ValueError("Exercises write Delta tables: use format('delta') or leave it out")
        return self

    def option(self, key: str, value: Any) -> Writer:
        self._options[str(key).lower()] = str(value).lower() in ("true", "1")
        return self

    def options(self, **opts: Any) -> Writer:
        for k, v in opts.items():
            self.option(k, v)
        return self

    def partitionBy(self, *_cols: Any) -> Writer:  # noqa: N802 - PySpark's name
        return self

    def saveAsTable(self, name: str) -> None:  # noqa: N802 - PySpark's name
        path = table_path(self._work, name)
        exists = (path / "_delta_log").is_dir()
        if exists and self._mode == "error":
            raise ValueError(f"Table {name} already exists; use .mode('overwrite') or .mode('append')")
        if exists and self._mode == "ignore":
            return
        schema_mode: Literal["merge", "overwrite"] | None = (
            "overwrite" if self._options.get("overwriteschema") and self._mode == "overwrite"
            else "merge" if self._options.get("mergeschema") and self._mode == "append" else None)
        mode: Literal["error", "append", "overwrite", "ignore"] = "append" if self._mode == "append" else "overwrite"
        path.mkdir(parents=True, exist_ok=True)
        # A plain local path, never a URI: the write can only land in this folder.
        deltalake.write_deltalake(str(path), _arrow(self._df), mode=mode, schema_mode=schema_mode)


class Reader:
    """``spark.read`` for an exercise: Delta tables under the workspace."""

    def __init__(self, session: AcademySession):
        self._s = session

    def format(self, fmt: str) -> Reader:
        if str(fmt).lower() != "delta":
            raise ValueError("Exercises read Delta tables: use spark.read.table(name)")
        return self

    def table(self, name: str) -> DataFrame:
        path = table_path(self._s.work, name)
        if not (path / "_delta_log").is_dir():
            raise ValueError(f"There's no table {name}")
        data = read_delta(path)
        return DataFrame(self._s.conn.from_arrow(data), self._s)


def _settings() -> dict[str, str | bool | int | float | list[str]]:
    """The session's DuckDB settings, given when it connects: the learner process locks every
    connection's settings as it opens (academy/sandbox.py), so none can be SET afterwards."""
    out: dict[str, str | bool | int | float | list[str]] = {"default_null_order": "nulls_first_on_asc_last_on_desc",   # Spark's NULL order, as SparkContext sets it
           "TimeZone": "UTC"}
    if duckdb_major() >= 2:
        out["disabled_optimizers"] = "type_pushdown"   # as duckdb_rows.keep_try_cast
    return out


class _Context(SparkContext):
    def __init__(self, master: str) -> None:  # noqa: ARG002 - SparkContext's signature
        settings = _settings()
        try:
            self._connection = duckdb.connect(":memory:", config=settings)
        except duckdb.InvalidInputException:
            # No ICU extension (the learner process can't load one): DuckDB then has no time zones,
            # so a TIMESTAMP WITH TIME ZONE is already read as UTC and the setting is left out.
            settings.pop("TimeZone")
            self._connection = duckdb.connect(":memory:", config=settings)


def read_delta(path: Path, version: int | None = None) -> pa.Table:
    """A Delta table's rows (at ``version``, else the current ones), read by delta-rs itself (DataFusion),
    not through a pyarrow file system: the learner process refuses pyarrow's PyFileSystem, which
    to_pyarrow_table uses."""
    table = deltalake.DeltaTable(str(path)) if version is None else deltalake.DeltaTable(str(path), version=version)
    query = deltalake.QueryBuilder().register("t", table)
    return _no_views(pa.table(query.execute("SELECT * FROM t").read_all()))


def _no_views(table: pa.Table) -> pa.Table:
    """DataFusion returns text as string_view. DuckDB pushes a join's key filter into the Arrow scan as
    is_in, which pyarrow has no string_view kernel for, so a join on a text key would fail: read plain
    string and binary instead."""
    plain = {pa.types.is_string_view: pa.string(), pa.types.is_binary_view: pa.binary()}
    fields = [pa.field(f.name, next((t for test, t in plain.items() if test(f.type)), f.type), f.nullable, f.metadata)
              for f in table.schema]
    return table.cast(pa.schema(fields, table.schema.metadata))


class AcademySession(SparkSession):
    def __init__(self, work: Path) -> None:
        super().__init__(_Context("local"))
        self.work = Path(work)

    @property
    def read(self) -> Reader:  # type: ignore[override]  # the exercise's reader, not DuckDB's
        return Reader(self)

    def table(self, name: str) -> DataFrame:  # type: ignore[override]
        return self.read.table(name)

    @property
    def readStream(self) -> pyspark_stream.StreamReader:  # type: ignore[override]  # noqa: N802 - PySpark's name
        return pyspark_stream.StreamReader(self)


class _Builder:
    def __init__(self, spark: AcademySession):
        self._spark = spark

    def appName(self, *_a: Any) -> _Builder:  # noqa: N802 - PySpark's name
        return self

    def config(self, *_a: Any, **_k: Any) -> _Builder:
        return self

    def getOrCreate(self) -> AcademySession:  # noqa: N802 - PySpark's name
        return self._spark


def _alias_pyspark(spark: AcademySession) -> None:
    """``from pyspark.sql import functions as F`` and friends resolve to DuckDB's API in this process,
    and ``SparkSession.builder.getOrCreate()`` returns the exercise's session."""
    import duckdb.experimental.spark as spark_pkg
    import duckdb.experimental.spark.sql as spark_sql
    import duckdb.experimental.spark.sql.functions as functions
    import duckdb.experimental.spark.sql.types as types
    sys.modules.update({"pyspark": spark_pkg, "pyspark.sql": spark_sql, "pyspark.sql.functions": functions,
                        "pyspark.sql.types": types, "pyspark.sql.window": spark_compat.window_module})
    spark_compat.sql_names(spark_sql)  # Row, Column, GroupedData, Window
    AcademySession.builder = _Builder(spark)  # type: ignore[assignment]
    spark_sql.SparkSession = AcademySession  # type: ignore[misc,assignment]
    delta, tables = _modules.ModuleType("delta"), _modules.ModuleType("delta.tables")
    tables.DeltaTable = pyspark_delta.DeltaTable  # type: ignore[attr-defined]
    delta.tables = tables  # type: ignore[attr-defined]
    sys.modules.update({"delta": delta, "delta.tables": tables})
    pyspark_delta.DeltaTable._session = spark


def session(work: Path) -> AcademySession:
    """A ``spark`` bound to ``work``. In this process it also makes ``df.write`` write there and makes
    ``import pyspark`` resolve to DuckDB's API: call it only in the learner's process."""
    spark_compat.install()
    spark = AcademySession(work)
    _dataframe.DataFrame.write = property(lambda self: Writer(self, spark.work))  # type: ignore[assignment]
    _dataframe.DataFrame.writeStream = property(  # type: ignore[attr-defined]
        lambda self: pyspark_stream.StreamWriter(self, spark))
    _dataframe.DataFrame.isStreaming = property(pyspark_stream.is_streaming)  # type: ignore[attr-defined]
    _alias_pyspark(spark)
    return spark


_UTILS: _modules.ModuleType | None = None


def namespace(spark: AcademySession) -> dict[str, object]:
    """The variables a notebook of the exercise starts with: ``spark`` and ``notebookutils`` (also as
    ``mssparkutils``), as in a Fabric notebook. The first call makes ``notebookutils`` importable."""
    global _UTILS
    if _UTILS is None:
        _UTILS = notebookutils_lab.install(spark.work, lambda: namespace(spark))
    utils = _UTILS
    return {"__name__": "__main__", "spark": spark, "notebookutils": utils, "mssparkutils": utils}
