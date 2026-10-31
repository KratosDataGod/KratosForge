"""PySpark API that DuckDB's ``spark`` lacks, added so common Fabric and Databricks code runs on
this PC (spark_local). Every addition works on DuckDB relations; nothing here starts Spark.

spark_compat.py (Notebook magics) adds the DataFrame transformations and functions: Window, agg,
selectExpr, fillna/na, pivot, describe, sample and the no-op partitioning calls. This module adds
the session side:

- ``spark.conf``: get, set, unset, getAll and isModifiable over Spark's keys. Keys this PC can honor
  (the session time zone) take effect; the rest are kept and reported by ``spark.conf.explain()``
  as Fabric-only, so a notebook that sets V-Order or shuffle partitions still runs here.
- ``spark.sparkContext``: version, applicationId, defaultParallelism, getConf, setLogLevel...
- ``spark.version``, ``spark.range``, ``SparkSession.builder.config(...)`` into ``spark.conf``.
- ``spark.catalog.tableExists`` against the attached lakehouse (and temp views).
- DataFrame: isEmpty, tail, explain, createTempView, toLocalIterator, offset, corr / cov /
  approxQuantile / stat, randomSplit, sortWithinPartitions, rdd / isLocal / sparkSession.
- RDDs, foreach and row-by-row Python raise with a pointer to Fabric.

COVERAGE lists PySpark 3.5's DataFrame and SparkSession methods and whether each works here;
docs/notebook-spark-on-this-pc.md is generated from it (tests keep the two in step).
"""

from __future__ import annotations

import getpass
import os
import time
from typing import Any

import sqlglot
from duckdb.experimental.spark.sql.dataframe import DataFrame
from .duckdb_rows import first_row, one_row

SPARK_VERSION = "3.5.0"  # the PySpark API this emulates, as in Fabric runtime 1.3

# Spark settings with their default, and what each does on this PC. "honored": applied here;
# "fabric": kept and returned, takes effect when the notebook runs in Fabric.
KNOWN_CONF: dict[str, tuple[str, str]] = {
    "spark.sql.session.timeZone": ("UTC", "honored"),
    "spark.sql.shuffle.partitions": ("200", "fabric"),
    "spark.sql.adaptive.enabled": ("true", "fabric"),
    "spark.sql.ansi.enabled": ("false", "fabric"),
    "spark.sql.caseSensitive": ("false", "fabric"),
    "spark.sql.parquet.vorder.default": ("true", "fabric"),
    "spark.sql.parquet.vorder.enabled": ("true", "fabric"),
    "spark.microsoft.delta.optimizeWrite.enabled": ("true", "fabric"),
    "spark.databricks.delta.optimizeWrite.enabled": ("true", "fabric"),
    "spark.databricks.delta.autoCompact.enabled": ("false", "fabric"),
    "spark.sql.sources.partitionOverwriteMode": ("static", "fabric"),
    "spark.app.name": ("KratosForge", "honored"),
}


class LocalRuntimeConfig:
    """``spark.conf``: every key is kept; see KNOWN_CONF for what takes effect here."""

    def __init__(self, session: Any):
        self._session = session
        self._set: dict[str, str] = {}

    def set(self, key: str, value: Any) -> None:
        text = str(value).lower() if isinstance(value, bool) else str(value)
        if key.lower() == "spark.sql.sources.partitionoverwritemode" and text.strip().lower() == "dynamic":
            # Kept and ignored, an overwrite with partitionBy would replace the whole table, where
            # Fabric replaces only the partitions it writes.
            raise ValueError("partitionOverwriteMode=dynamic isn't supported on this PC, and ignoring it would "
                             "replace the whole table. Run the cell in Fabric.")
        if key == "spark.sql.session.timeZone":
            self._session.conn.execute("SET TimeZone = ?", [text])  # fails, with DuckDB's reason, for an unknown zone
        self._set[key] = text

    def get(self, key: str, default: Any = ...) -> str | None:
        if key in self._set:
            return self._set[key]
        if default is not ...:  # an explicit default, None included, as in Spark
            return default
        if key in KNOWN_CONF:
            return KNOWN_CONF[key][0]
        raise KeyError(f"{key} isn't set. Pass a default: spark.conf.get({key!r}, 'value').")

    def unset(self, key: str) -> None:
        if key == "spark.sql.session.timeZone" and key in self._set:
            self._session.conn.execute("SET TimeZone = 'UTC'")
        self._set.pop(key, None)

    @property
    def getAll(self) -> dict[str, str]:  # noqa: N802 - a property in PySpark 4
        return {**{k: v for k, (v, _) in KNOWN_CONF.items()}, **self._set}

    def isModifiable(self, key: str) -> bool:  # noqa: N802
        return key.startswith(("spark.sql.", "spark.microsoft.", "spark.databricks."))

    def explain(self) -> list[dict[str, str]]:
        """What each setting does on this PC, printed and returned."""
        rows = []
        for key, value in sorted(self.getAll.items()):
            where = KNOWN_CONF.get(key, ("", "fabric"))[1]
            rows.append({"key": key, "value": value,
                         "here": "applied on this PC" if where == "honored" else "kept; takes effect in Fabric"})
        for r in rows:
            print(f"{r['key']} = {r['value']}  ({r['here']})")
        return rows


class LocalSparkConf:
    """``spark.sparkContext.getConf()``."""

    def __init__(self, conf: LocalRuntimeConfig):
        self._conf = conf

    def get(self, key: str, defaultValue: Any = None) -> str | None:  # noqa: N803
        return self._conf.getAll.get(key, defaultValue)

    def getAll(self) -> list[tuple[str, str]]:  # noqa: N802
        return sorted(self._conf.getAll.items())

    def contains(self, key: str) -> bool:
        return key in self._conf.getAll

    def set(self, key: str, value: Any) -> LocalSparkConf:
        self._conf.set(key, value)
        return self


class LocalSparkContext:
    """``spark.sparkContext``: what notebooks read from it. There is no cluster behind it."""

    def __init__(self, session: Any):
        self._session = session
        self.version = SPARK_VERSION
        self.applicationId = f"local-kratosforge-{os.getpid()}"  # noqa: N815
        self.defaultParallelism = os.cpu_count() or 1  # noqa: N815
        self.defaultMinPartitions = 1  # noqa: N815
        self.startTime = int(time.time() * 1000)  # noqa: N815
        self.uiWebUrl = None  # noqa: N815 - no Spark UI on this PC
        self.master = "local[*]"
        self.appName = "KratosForge"  # noqa: N815
        self._properties: dict[str, str] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session._context, name)  # anything else: DuckDB's own context

    def sparkUser(self) -> str:  # noqa: N802
        return getpass.getuser()

    def getConf(self) -> LocalSparkConf:  # noqa: N802
        return LocalSparkConf(self._session.conf)

    def setLogLevel(self, logLevel: str) -> None:  # noqa: N802, N803, ARG002
        return None  # there is no Spark log here

    def setJobDescription(self, value: str) -> None:  # noqa: N802, ARG002
        return None

    def setJobGroup(self, groupId: str, description: str, interruptOnCancel: bool = False) -> None:  # noqa: N802, N803, ARG002
        return None

    def setLocalProperty(self, key: str, value: str) -> None:  # noqa: N802
        self._properties[key] = value

    def getLocalProperty(self, key: str) -> str | None:  # noqa: N802
        return self._properties.get(key)

    def setCheckpointDir(self, dirName: str) -> None:  # noqa: N802, N803, ARG002
        return None

    def _rdd(self, *_a: Any, **_kw: Any) -> Any:
        raise NotImplementedError("RDDs run in Fabric. On this PC, use spark.createDataFrame and DataFrame methods.")

    parallelize = textFile = wholeTextFiles = emptyRDD = range = broadcast = accumulator = _rdd  # noqa: N815


def table_exists(session: Any, tableName: str, dbName: str | None = None) -> bool:  # noqa: N803
    """``spark.catalog.tableExists``: a temp view, or a table in the attached lakehouse."""
    from . import lakehouse
    name = f"{dbName}.{tableName}" if dbName else tableName
    if "." not in name and session._is_local(name):
        return True
    return lakehouse._attached is not None and lakehouse.table_exists(name)


# ---- DataFrame methods DuckDB doesn't have

def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _duckdb_expr(expr: str) -> str:
    """A Spark SQL expression in DuckDB SQL."""
    from . import sql as spark_sql
    return spark_sql.duckdb_sql(spark_sql.spark_substring(sqlglot.parse_one(f"SELECT {expr}", read="spark")))[len("SELECT "):]


def _new(df: DataFrame, rel: Any) -> DataFrame:
    return DataFrame(rel, df.session)


def isEmpty(self: DataFrame) -> bool:  # noqa: N802
    return first_row(self.relation.limit(1)) is None


def tail(self: DataFrame, num: int) -> list[Any]:
    rows = self.collect()
    return rows[-num:] if num else []


def explain(self: DataFrame, extended: Any = None, mode: str | None = None) -> None:  # noqa: ARG001
    print(self.relation.explain())


def createTempView(self: DataFrame, name: str) -> None:  # noqa: N802
    if self.session._is_local(name):
        raise ValueError(f"Temporary view {name!r} already exists. Use createOrReplaceTempView.")
    self.createOrReplaceTempView(name)


def toLocalIterator(self: DataFrame, prefetchPartitions: bool = False) -> Any:  # noqa: N802, N803, ARG001
    return iter(self.collect())


def offset(self: DataFrame, num: int) -> DataFrame:
    return _new(self, self.relation.query("__offset_src", f"SELECT * FROM __offset_src OFFSET {int(num)}"))


def corr(self: DataFrame, col1: str, col2: str, method: str | None = None) -> float:
    return one_row(self.relation.aggregate(f"corr({_q(col1)}, {_q(col2)})", ""))[0]


def cov(self: DataFrame, col1: str, col2: str) -> float:
    return one_row(self.relation.aggregate(f"covar_samp({_q(col1)}, {_q(col2)})", ""))[0]


def approxQuantile(self: DataFrame, col: Any, probabilities: list[float], relativeError: float) -> list[Any]:  # noqa: N802, N803, ARG001
    cols = [col] if isinstance(col, str) else list(col)
    out = []
    for c in cols:
        row = one_row(self.relation.aggregate(", ".join(f"quantile_cont({_q(c)}, {float(p)})" for p in probabilities), ""))
        out.append(list(row))
    return out[0] if isinstance(col, str) else out


def randomSplit(self: DataFrame, weights: list[float], seed: int | None = None) -> list[DataFrame]:  # noqa: N802
    """Splits by a hash of each row and the seed, so the parts never overlap and together hold
    every row, however often each is read (identical rows go to the same part)."""
    total = float(sum(weights))
    cols = ", ".join(_q(c) for c in self.relation.columns)
    salt = int(seed) if seed is not None else int(time.time() * 1000)
    point = f"(hash({cols}, {salt}) % 1000000) / 1000000.0"
    out, low = [], 0.0
    for i, w in enumerate(weights):
        high = 1.0 if i == len(weights) - 1 else low + w / total
        out.append(_new(self, self.relation.filter(f"{point} >= {low} AND {point} < {high}")))
        low = high
    return out


class _Stat:
    def __init__(self, df: DataFrame):
        self._df = df

    def corr(self, col1: str, col2: str, method: str | None = None) -> float:
        return corr(self._df, col1, col2, method)

    def cov(self, col1: str, col2: str) -> float:
        return cov(self._df, col1, col2)

    def approxQuantile(self, col: Any, probabilities: list[float], relativeError: float) -> list[Any]:  # noqa: N802, N803
        return approxQuantile(self._df, col, probabilities, relativeError)


def _rdd(self: DataFrame, *_a: Any, **_kw: Any) -> Any:
    raise NotImplementedError("RDDs and row-by-row Python (rdd, foreach, foreachPartition, mapInPandas) run in "
                              "Fabric. On this PC, use DataFrame methods, SQL or toPandas().")


ADDED: dict[str, Any] = {
    "isEmpty": isEmpty, "tail": tail, "explain": explain, "createTempView": createTempView,
    "registerTempTable": DataFrame.createOrReplaceTempView, "toLocalIterator": toLocalIterator, "offset": offset,
    "corr": corr, "cov": cov, "approxQuantile": approxQuantile, "randomSplit": randomSplit,
    "createOrReplaceGlobalTempView": DataFrame.createOrReplaceTempView, "sortWithinPartitions": DataFrame.orderBy,
    "foreach": _rdd, "foreachPartition": _rdd, "mapInPandas": _rdd, "mapInArrow": _rdd,
}


def install() -> None:
    """Add the methods to DuckDB's DataFrame (once; spark_local calls it), after spark_compat's."""
    for name, fn in ADDED.items():
        if name not in DataFrame.__dict__:
            setattr(DataFrame, name, fn)
    if "stat" not in DataFrame.__dict__:
        DataFrame.stat = property(lambda self: _Stat(self))  # type: ignore[attr-defined]
        DataFrame.sparkSession = property(lambda self: self.session)  # type: ignore[attr-defined]
        DataFrame.isLocal = lambda self: True  # type: ignore[attr-defined]
    if "rdd" not in DataFrame.__dict__:
        DataFrame.rdd = property(_rdd)  # type: ignore[attr-defined]


# ---- coverage: PySpark 3.5's public DataFrame and SparkSession API, and how each works here.

PYSPARK_DATAFRAME = (
    "agg alias approxQuantile cache checkpoint coalesce colRegex collect columns corr count cov "
    "createGlobalTempView createOrReplaceGlobalTempView createOrReplaceTempView createTempView crossJoin crosstab cube "
    "describe distinct drop dropDuplicates dropDuplicatesWithinWatermark dropna dtypes exceptAll explain fillna filter "
    "first foreach foreachPartition freqItems groupBy groupby head hint inputFiles intersect intersectAll isEmpty "
    "isLocal isStreaming join limit localCheckpoint mapInArrow mapInPandas melt na observe offset orderBy "
    "pandas_api persist printSchema randomSplit rdd registerTempTable repartition repartitionByRange replace rollup "
    "sameSemantics sample sampleBy schema select selectExpr semanticHash show sort sortWithinPartitions sparkSession "
    "stat storageLevel subtract summary tail take to toDF toJSON toLocalIterator toPandas transform union unionAll "
    "unionByName unpersist unpivot where withColumn withColumnRenamed withColumns withColumnsRenamed withMetadata "
    "withWatermark write writeStream writeTo"
).split()

PYSPARK_SESSION = (
    "builder catalog conf createDataFrame getActiveSession newSession range read readStream sparkContext sql "
    "stop streams table udf udtf version"
).split()

# Why a method isn't here, when it isn't.
NOT_HERE = {
    "rdd": "RDDs run in Fabric", "foreach": "row-by-row Python runs in Fabric", "foreachPartition": "runs in Fabric",
    "mapInPandas": "runs in Fabric", "mapInArrow": "runs in Fabric",
    "withWatermark": "streaming runs in Fabric", "dropDuplicatesWithinWatermark": "streaming runs in Fabric",
    "streams": "streaming runs in Fabric",
    "udf": "Python UDFs run in Fabric", "udtf": "Python UDTFs run in Fabric", "pandas_api": "pandas-on-Spark runs in Fabric; use toPandas()",
}

# Fabric-only until this PC's streaming (spark_stream.py) adds them; then they're counted as they work.
# (DuckDB has a readStream of its own that reads nothing.)
STREAMING = {"readStream", "writeStream", "isStreaming"}

NO_OP = {"repartition", "repartitionByRange", "coalesce", "persist", "unpersist", "checkpoint", "localCheckpoint",
         "hint", "cache"}


def coverage() -> list[dict[str, str]]:
    """Each PySpark 3.5 method: "works", "no-op" (accepted; nothing to do on one PC) or "Fabric"
    (raises, with why). A method is "works" only if calling it doesn't raise DuckDB's not-implemented error."""
    from duckdb.experimental.spark.exception import ContributionsAcceptedError

    from . import spark_stream
    from .spark_local import LocalSparkSession
    install()
    spark_stream.install()  # readStream, writeStream and isStreaming, as a notebook has them
    rows = []
    for area, names, owner in (("DataFrame", PYSPARK_DATAFRAME, DataFrame), ("SparkSession", PYSPARK_SESSION, LocalSparkSession)):
        for name in names:
            attr = None
            for klass in owner.__mro__:
                if name in klass.__dict__:
                    attr = klass.__dict__[name]
                    break
            ours = getattr(attr.fget if isinstance(attr, property) else attr, "__module__", "") == "fabric_desktop.spark_stream"
            why = NOT_HERE.get(name) or ("streaming runs in Fabric" if name in STREAMING and not ours else "")
            if why:
                state = "Fabric"
            elif attr is None or _not_implemented(attr, ContributionsAcceptedError):
                state = "missing"
            elif name in NO_OP:
                state = "no-op"
            else:
                state = "works"
            rows.append({"area": area, "name": name, "state": state, "why": why})
    return rows


def _not_implemented(attr: Any, error: type) -> bool:
    """DuckDB marks what it won't do with a method whose body raises ContributionsAcceptedError."""
    import inspect
    fn = attr.fget if isinstance(attr, property) else attr
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return False
    return error.__name__ in src and src.count("\n") < 25 and "raise" in src


def coverage_markdown() -> str:
    """The coverage tables for docs/notebook-spark-on-this-pc.md."""
    label = {"works": "Works", "no-op": "Accepted, does nothing on one PC", "Fabric": "Runs in Fabric",
             "missing": "Not yet on this PC"}
    out = []
    for area in ("DataFrame", "SparkSession"):
        rows = [r for r in coverage() if r["area"] == area]
        done = sum(r["state"] in ("works", "no-op") for r in rows)
        out += [f"### {area}: {done} of {len(rows)}", "", "| Method | On this PC |", "| --- | --- |"]
        out += [f"| `{r['name']}` | {label[r['state']]}{': ' + r['why'] if r['why'] else ''} |" for r in rows]
        out.append("")
    return "\n".join(out)
