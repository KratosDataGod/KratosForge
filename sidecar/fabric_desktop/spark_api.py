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
import re
import time
from typing import Any, cast

import sqlglot
from duckdb.experimental.spark.sql.dataframe import DataFrame
from . import spark_builtins_sql
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
    if dbName is not None and dbName.lower() == GLOBAL_TEMP and "." not in tableName:
        return bool(session._is_view(GLOBAL_PREFIX + tableName))
    return lakehouse._attached is not None and lakehouse.table_exists(name)


# ---- DataFrame methods DuckDB doesn't have

def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _duckdb_expr(expr: str) -> str:
    """A Spark SQL expression in DuckDB SQL."""
    from . import sql as spark_sql
    return spark_sql.duckdb_sql(spark_sql.spark_substring(cast(sqlglot.exp.Expression, sqlglot.parse_one(f"SELECT {expr}", read="spark"))))[len("SELECT "):]


def _new(df: DataFrame, rel: Any) -> DataFrame:
    return DataFrame(rel, df.session)


def isEmpty(self: DataFrame) -> bool:  # noqa: N802
    return first_row(self.relation.limit(1)) is None


def tail(self: DataFrame, num: int) -> list[Any]:
    rows = self.collect()
    return rows[-num:] if num else []


def explain(self: DataFrame, extended: Any = None, mode: str | None = None) -> None:  # noqa: ARG001
    print(self.relation.explain())


# A global temp view (global_temp.name in Spark SQL) is a temp view of this name on the connection.
GLOBAL_TEMP = "global_temp"
GLOBAL_PREFIX = "__kf_global_temp_"


def _view_name(name: str) -> str:
    """``name`` as a temp view's name, refused as Spark refuses one with more than one part."""
    from duckdb.experimental.spark.errors import AnalysisException
    if not isinstance(name, str) or "." in name:
        raise AnalysisException(message="[TEMP_VIEW_NAME_TOO_MANY_NAME_PARTS] CREATE TEMPORARY VIEW or the "
                                        "corresponding Dataset APIs only accept single-part view names, but got: "
                                        f"{name}.")
    return name


def _create_view(self: DataFrame, name: str, replace: bool, shown: str) -> None:
    from duckdb.experimental.spark.errors import AnalysisException
    if not replace and self.session._is_view(name):  # type: ignore[attr-defined]  # LocalSparkSession's own
        raise AnalysisException(message=f"[TEMP_TABLE_OR_VIEW_ALREADY_EXISTS] Cannot create the temporary view "
                                        f"{shown} because it already exists.")
    self.relation.create_view(name, replace=True)


def createTempView(self: DataFrame, name: str) -> None:  # noqa: N802
    """``createTempView``: Spark's error when the name has a view already, whatever its case."""
    _create_view(self, _view_name(name), False, f"`{name}`")


def createOrReplaceTempView(self: DataFrame, name: str) -> None:  # noqa: N802
    _create_view(self, _view_name(name), True, f"`{name}`")


def createGlobalTempView(self: DataFrame, name: str) -> None:  # noqa: N802
    """``createGlobalTempView``: read as global_temp.name in Spark SQL and spark.table."""
    _create_view(self, GLOBAL_PREFIX + _view_name(name), False, f"`{name}`")


def createOrReplaceGlobalTempView(self: DataFrame, name: str) -> None:  # noqa: N802
    _create_view(self, GLOBAL_PREFIX + _view_name(name), True, f"`{name}`")


def drop_view(session: Any, name: str) -> bool:
    """``spark.catalog.dropTempView`` and ``dropGlobalTempView``: whether a view was there to drop. Only
    a view a DataFrame made counts, never a lakehouse table's."""
    if not isinstance(name, str) or not session._is_view(name) or name.lower().startswith("__lakehouse_table_"):
        return False
    session.conn.execute(f"DROP VIEW {_q(name)}")
    return True


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
    every row, however often each is read (identical rows go to the same part). Spark's own checks
    on the weights come first."""
    from duckdb.experimental.spark.errors import IllegalArgumentException, PySparkValueError
    for w in weights:
        if w < 0.0:
            raise PySparkValueError(error_class="VALUE_NOT_POSITIVE",
                                    message_parameters={"arg_name": "weights", "arg_value": str(w)})
    total = float(sum(weights))
    if total <= 0:
        raise IllegalArgumentException(f"requirement failed: Sum of weights must be positive, but got {weights}")
    cols = ", ".join(_q(c) for c in self.relation.columns)
    salt = int(seed) if seed is not None else int(time.time() * 1000)
    point = f"(hash({cols}, {salt}) % 1000000) / 1000000.0"
    out, low = [], 0.0
    for i, w in enumerate(weights):
        high = 1.0 if i == len(weights) - 1 else low + w / total
        out.append(_new(self, self.relation.filter(f"{point} >= {low} AND {point} < {high}")))
        low = high
    return out


def _text_sql(col: str) -> str:
    """A column's value as Spark writes it as text (Java's toString), null as 'null'."""
    q = _q(col)
    return f"(CASE WHEN {q} IS NULL THEN 'null' ELSE {spark_builtins_sql.java_value_text(q)} END)"


def crosstab(self: DataFrame, col1: str, col2: str) -> DataFrame:
    """A count of each pair of values: a row per value of ``col1`` (named ``col1_col2``) and a column
    per value of ``col2``, as text (null for NULL) in sorted order, as Spark's."""
    pairs = self.relation.aggregate(f"{_text_sql(col1)} AS kf_a, {_text_sql(col2)} AS kf_b, count(*) AS kf_n",
                                    "kf_a, kf_b")
    # one past Spark's limit is enough to refuse, without holding every value
    heads = sorted({row[0] for row in pairs.project("kf_b").distinct().limit(10001).fetchall()})
    if len(heads) > 10000:  # Spark's own limit, so the frame stays a table someone can read
        raise ValueError(f"crosstab: {col2} has more than 10000 distinct values; Spark allows up to 10000.")
    cells = ", ".join(f"CAST(coalesce(sum(kf_n) FILTER (WHERE kf_b = {_text(h)}), 0) AS BIGINT) AS {_q(h)}"
                      for h in heads)
    return _new(self, pairs.aggregate(f"kf_a AS {_q(f'{col1}_{col2}')}" + (f", {cells}" if cells else ""), "kf_a"))


def _text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_RE2_SPECIAL = re.compile(r"([\\.^$|?*+()\[\]{}])")


def colRegex(self: DataFrame, colName: str) -> Any:  # noqa: N802, N803
    """The columns whose names match the regex in backticks (`(id|name)`), whole names, ignoring case
    as Spark does; a name without backticks is that column. The names are matched here, so the regex
    is Python's, which reads Java's syntax."""
    from duckdb import SQLExpression
    from duckdb.experimental.spark.sql.column import Column
    if not (len(colName) >= 2 and colName.startswith("`") and colName.endswith("`")):
        return Column(SQLExpression(_q(colName)))
    try:  # Python's re reads Java's syntax closer than DuckDB's RE2 (possessive ?+ *+ ++, for one)
        pattern = re.compile(colName[1:-1], re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"colRegex: {colName} isn't a regex this PC can read: {exc}.") from None
    names = [c for c in self.columns if pattern.fullmatch(c)]
    if not names:
        raise ValueError(f"colRegex: no column matches {colName}.")
    exact = "|".join(_RE2_SPECIAL.sub(r"\\\1", n) for n in names)  # each name as itself, in DuckDB's RE2
    return Column(SQLExpression(f"COLUMNS({_text('^(?:' + exact + ')$')})"))


class _JsonRows(list):
    """toJSON's rows: Spark returns an RDD of JSON text; here it is a list with collect, first, take
    and count, which is how a notebook reads it."""

    def collect(self) -> list[str]:
        return list(self)

    def first(self) -> str:
        if not self:
            raise ValueError("RDD is empty")
        return str(self[0])

    def take(self, num: int) -> list[str]:
        return list(self[:num])

    def count(self) -> int:  # type: ignore[override]
        return len(self)


def _json_value(value: Any) -> str:
    """``value`` as Spark's JSON writes it: no NULL fields in a row or struct, doubles as Java writes
    them, timestamps in ISO form with milliseconds, binary as base64."""
    import base64
    import datetime
    import decimal
    import json
    from .spark_compat import _java_double
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return json.dumps("NaN" if value != value else ("Infinity" if value > 0 else "-Infinity"))
        return _java_double(value)
    if isinstance(value, (int, decimal.Decimal)):
        return str(value)
    if isinstance(value, datetime.datetime):
        stamp = value if value.tzinfo is None else value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return json.dumps(stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z")
    if isinstance(value, datetime.date):
        return json.dumps(value.isoformat())
    if isinstance(value, (bytes, bytearray, memoryview)):
        return json.dumps(base64.b64encode(bytes(value)).decode())
    if isinstance(value, dict) or hasattr(value, "asDict"):  # a map keeps NULL values; a row or struct doesn't
        fields = value if isinstance(value, dict) else value.asDict()
        return "{" + ",".join(f"{json.dumps(str(k), ensure_ascii=False)}:{_json_value(v)}"
                              for k, v in fields.items() if v is not None or isinstance(value, dict)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_json_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def toJSON(self: DataFrame, use_unicode: bool = True) -> _JsonRows:  # noqa: N802, ARG001
    """Each row as a JSON object, as Spark's toJSON writes it (NULL fields left out)."""
    return _JsonRows(_json_value(row) for row in self.collect())


def sampleBy(self: DataFrame, col: Any, fractions: dict[Any, float], seed: int | None = None) -> DataFrame:  # noqa: N802
    """A sample of each group: a row whose ``col`` is a key of ``fractions`` is kept with that
    probability, any other row is left out, as Spark's. A seed gives the same rows each time on this PC
    (not the rows Spark picks)."""
    if not isinstance(fractions, dict):
        raise TypeError(f"[NOT_DICT] Argument `fractions` should be a dict, got {type(fractions).__name__}.")
    for key, fraction in fractions.items():
        if not 0.0 <= float(fraction) <= 1.0:
            raise ValueError(f"Fractions must be in [0, 1], but got {fraction} for {key!r}.")
    from duckdb import ConstantExpression
    from duckdb.experimental.spark.sql.column import Column
    target = str(col.expr) if isinstance(col, Column) else _q(col)
    point = "random()" if seed is None else f"((hash(row_number() OVER (), {int(seed)}) % 1000000) / 1000000.0)"
    keep = " ".join(f"WHEN {ConstantExpression(k)} THEN {float(f)!r}" for k, f in fractions.items() if k is not None)
    if not keep:
        return _new(self, self.relation.filter("false"))
    cols = ", ".join(_q(c) for c in self.columns)
    marked = self.relation.project(f"*, {point} < (CASE {target} {keep} ELSE 0.0 END) AS kf_keep")
    return _new(self, marked.filter("kf_keep").project(cols))


class _Stat:
    def __init__(self, df: DataFrame):
        self._df = df

    def corr(self, col1: str, col2: str, method: str | None = None) -> float:
        return corr(self._df, col1, col2, method)

    def cov(self, col1: str, col2: str) -> float:
        return cov(self._df, col1, col2)

    def approxQuantile(self, col: Any, probabilities: list[float], relativeError: float) -> list[Any]:  # noqa: N802, N803
        return approxQuantile(self._df, col, probabilities, relativeError)

    def crosstab(self, col1: str, col2: str) -> DataFrame:
        return crosstab(self._df, col1, col2)

    def sampleBy(self, col: Any, fractions: dict[Any, float], seed: int | None = None) -> DataFrame:  # noqa: N802
        return sampleBy(self._df, col, fractions, seed)


def _rdd(self: DataFrame, *_a: Any, **_kw: Any) -> Any:
    raise NotImplementedError("RDDs and row-by-row Python (rdd, foreach, foreachPartition, mapInPandas) run in "
                              "Fabric. On this PC, use DataFrame methods, SQL or toPandas().")


ADDED: dict[str, Any] = {
    "isEmpty": isEmpty, "tail": tail, "explain": explain,
    "createTempView": createTempView,
    "createOrReplaceTempView": createOrReplaceTempView,
    "registerTempTable": createOrReplaceTempView,
    "createGlobalTempView": createGlobalTempView,
    "createOrReplaceGlobalTempView": createOrReplaceGlobalTempView, "toLocalIterator": toLocalIterator, "offset": offset,
    "corr": corr, "cov": cov, "approxQuantile": approxQuantile, "randomSplit": randomSplit,
    "sortWithinPartitions": DataFrame.orderBy,
    "foreach": _rdd, "foreachPartition": _rdd, "mapInPandas": _rdd, "mapInArrow": _rdd,
    "crosstab": crosstab, "colRegex": colRegex, "toJSON": toJSON, "sampleBy": sampleBy,
}


# DuckDB's own, which these replace: Spark's errors for a name with a dot, and global temp views.
_REPLACED = ("createOrReplaceTempView", "createGlobalTempView", "createOrReplaceGlobalTempView")


def install() -> None:
    """Add the methods to DuckDB's DataFrame (once; spark_local calls it), after spark_compat's."""
    for name, fn in ADDED.items():
        if name not in DataFrame.__dict__ or name in _REPLACED:
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
        src = inspect.getsource(fn)  # type: ignore[arg-type]  # None raises TypeError, caught below
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
