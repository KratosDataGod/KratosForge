"""PySpark API that DuckDB's ``spark`` lacks, added so common Fabric and Databricks code runs on
this PC (spark_local). Every addition works on DuckDB relations; nothing here starts Spark.

- ``spark.conf``: get, set, unset, getAll and isModifiable over Spark's keys. Keys this PC can honor
  (the session time zone) take effect; the rest are kept and reported by ``spark.conf.explain()``
  as Fabric-only, so a notebook that sets V-Order or shuffle partitions still runs here.
- ``spark.sparkContext``: version, applicationId, defaultParallelism, getConf, setLogLevel...
- ``spark.catalog.tableExists`` against the attached lakehouse (and temp views).
- DataFrame: agg, selectExpr, fillna / dropna / na, describe / summary, isEmpty, sample, tail,
  explain, unpivot / melt, createTempView, and the partitioning calls (repartition, coalesce,
  persist...) that only change how Spark spreads work, which are kept as no-ops.
- RDDs, foreach and UDF-style row processing raise with a pointer to Fabric.

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
        if key == "spark.sql.session.timeZone":
            self._session.conn.execute("SET TimeZone = ?", [text])  # fails, with DuckDB's reason, for an unknown zone
        self._set[key] = text

    def get(self, key: str, default: Any = None) -> str | None:
        if key in self._set:
            return self._set[key]
        if default is not None:
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
        try:
            return self._conf.get(key, defaultValue)
        except KeyError:
            return None

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
    return sqlglot.transpile(f"SELECT {expr}", read="spark", write="duckdb")[0][len("SELECT "):]


def _new(df: DataFrame, rel: Any) -> DataFrame:
    return DataFrame(rel, df.session)


def _agg_exprs(df: DataFrame, exprs: tuple[Any, ...]) -> list[Any]:
    if len(exprs) == 1 and isinstance(exprs[0], dict):
        return [f"{fn}({_q(col) if col != '*' else '*'}) AS {_q(f'{fn}({col})')}" for col, fn in exprs[0].items()]
    return [e.expr if hasattr(e, "expr") else _duckdb_expr(str(e)) for e in exprs]


def agg(self: DataFrame, *exprs: Any) -> DataFrame:
    parts = _agg_exprs(self, exprs)
    if all(isinstance(p, str) for p in parts):
        return _new(self, self.relation.aggregate(", ".join(parts), ""))
    return _new(self, self.relation.aggregate([p if not isinstance(p, str) else p for p in parts], ""))


def selectExpr(self: DataFrame, *expr: Any) -> DataFrame:  # noqa: N802
    exprs = list(expr[0]) if len(expr) == 1 and isinstance(expr[0], (list, tuple)) else list(expr)
    return _new(self, self.relation.project(", ".join(_duckdb_expr(e) for e in exprs)))


def _numeric(t: str) -> bool:
    return t.upper().split("(")[0] in ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE",
                                       "DECIMAL", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT")


def fillna(self: DataFrame, value: Any, subset: Any = None) -> DataFrame:
    """As in Spark: a number fills numeric columns, text fills text columns, a bool fills booleans;
    a dict gives a value per column."""
    cols, types = self.relation.columns, [str(t) for t in self.relation.types]
    if isinstance(subset, str):
        subset = [subset]
    if isinstance(value, dict):
        wanted = {c: v for c, v in value.items()}
    else:
        def fits(t: str) -> bool:
            if isinstance(value, bool):
                return t.upper() == "BOOLEAN"
            if isinstance(value, (int, float)):
                return _numeric(t)
            return t.upper() == "VARCHAR"
        wanted = {c: value for c, t in zip(cols, types) if fits(t) and (subset is None or c in subset)}
    parts = []
    for c in cols:
        if c in wanted:
            v = wanted[c]
            lit = "TRUE" if v is True else "FALSE" if v is False else repr(v) if isinstance(v, (int, float)) \
                else "'" + str(v).replace("'", "''") + "'"
            parts.append(f"COALESCE({_q(c)}, {lit}) AS {_q(c)}")
        else:
            parts.append(_q(c))
    return _new(self, self.relation.project(", ".join(parts)))


def dropna(self: DataFrame, how: str = "any", thresh: int | None = None, subset: Any = None) -> DataFrame:
    cols = [subset] if isinstance(subset, str) else list(subset or self.relation.columns)
    present = " + ".join(f"CASE WHEN {_q(c)} IS NULL THEN 0 ELSE 1 END" for c in cols)
    if thresh is not None:
        cond = f"({present}) >= {int(thresh)}"
    elif how == "all":
        cond = f"({present}) > 0"
    else:
        cond = f"({present}) = {len(cols)}"
    return _new(self, self.relation.filter(cond))


class _NA:
    def __init__(self, df: DataFrame):
        self._df = df

    def fill(self, value: Any, subset: Any = None) -> DataFrame:
        return fillna(self._df, value, subset)

    def drop(self, how: str = "any", thresh: int | None = None, subset: Any = None) -> DataFrame:
        return dropna(self._df, how, thresh, subset)


def describe(self: DataFrame, *cols: Any) -> DataFrame:
    """count, mean, stddev, min and max per column, as text, like Spark's describe()."""
    names = [c for c in (list(cols[0]) if len(cols) == 1 and isinstance(cols[0], list) else cols)] or list(self.relation.columns)
    types = dict(zip(self.relation.columns, [str(t) for t in self.relation.types]))
    stats = [("count", "count({c})"), ("mean", "avg({c})"), ("stddev", "stddev_samp({c})"),
             ("min", "min({c})"), ("max", "max({c})")]
    selects = []
    for label, f in stats:
        vals = []
        for c in names:
            numeric = _numeric(types.get(c, ""))
            if label in ("mean", "stddev") and not numeric:
                vals.append(f"NULL::VARCHAR AS {_q(c)}")
            else:
                vals.append(f"CAST({f.format(c=_q(c))} AS VARCHAR) AS {_q(c)}")
        selects.append(f"SELECT '{label}' AS summary, {', '.join(vals)} FROM __describe_src")
    rel = self.relation.query("__describe_src", " UNION ALL ".join(selects))
    return _new(self, rel)


def summary(self: DataFrame, *statistics: str) -> DataFrame:
    if statistics and set(statistics) - {"count", "mean", "stddev", "min", "max"}:
        raise NotImplementedError("On this PC, summary() gives count, mean, stddev, min and max. Percentiles: "
                                  "use percentile_approx in agg() or SQL.")
    return describe(self)


def isEmpty(self: DataFrame) -> bool:  # noqa: N802
    return self.relation.limit(1).fetchone() is None


def sample(self: DataFrame, withReplacement: Any = None, fraction: Any = None, seed: int | None = None) -> DataFrame:  # noqa: N803
    if isinstance(withReplacement, float) and fraction is None:  # sample(0.1) and sample(0.1, 42)
        withReplacement, fraction, seed = None, withReplacement, fraction if seed is None else seed
    if withReplacement:
        raise NotImplementedError("sample(withReplacement=True) isn't supported on this PC.")
    pct = float(fraction) * 100
    method = f"bernoulli, {int(seed)}" if seed is not None else "bernoulli"
    return _new(self, self.relation.query("__sample_src", f"SELECT * FROM __sample_src USING SAMPLE {pct}% ({method})"))


def tail(self: DataFrame, num: int) -> list[Any]:
    rows = self.collect()
    return rows[-num:] if num else []


def explain(self: DataFrame, extended: Any = None, mode: str | None = None) -> None:  # noqa: ARG001
    print(self.relation.explain())


def unpivot(self: DataFrame, ids: Any, values: Any, variableColumnName: str, valueColumnName: str) -> DataFrame:  # noqa: N803
    ids = [ids] if isinstance(ids, str) else list(ids or [])
    values = [values] if isinstance(values, str) else list(values or [c for c in self.relation.columns if c not in ids])
    sql = (f"UNPIVOT __unpivot_src ON {', '.join(_q(v) for v in values)} "
           f"INTO NAME {_q(variableColumnName)} VALUE {_q(valueColumnName)}")
    rel = self.relation.query("__unpivot_src", sql)
    keep = [_q(c) for c in ids] + [_q(variableColumnName), _q(valueColumnName)]
    return _new(self, rel.project(", ".join(keep)))


def createTempView(self: DataFrame, name: str) -> None:  # noqa: N802
    if self.session._is_local(name):
        raise ValueError(f"Temporary view {name!r} already exists. Use createOrReplaceTempView.")
    self.createOrReplaceTempView(name)


def toLocalIterator(self: DataFrame, prefetchPartitions: bool = False) -> Any:  # noqa: N802, N803, ARG001
    return iter(self.collect())


def offset(self: DataFrame, num: int) -> DataFrame:
    return _new(self, self.relation.query("__offset_src", f"SELECT * FROM __offset_src OFFSET {int(num)}"))


def subtract(self: DataFrame, other: DataFrame) -> DataFrame:
    """Rows in this data frame and not in ``other``, without duplicates (SQL EXCEPT)."""
    return _new(self, self.relation.except_(other.relation).distinct())


def replace(self: DataFrame, to_replace: Any, value: Any = None, subset: Any = None) -> DataFrame:
    """Replace values: ``replace(1, 2)``, ``replace([1, 2], [3, 4])`` or ``replace({1: 3})``."""
    pairs = dict(to_replace) if isinstance(to_replace, dict) else dict(zip(
        to_replace if isinstance(to_replace, (list, tuple)) else [to_replace],
        value if isinstance(value, (list, tuple)) else [value] * (len(to_replace) if isinstance(to_replace, (list, tuple)) else 1)))
    subset = [subset] if isinstance(subset, str) else subset

    def lit(v: Any) -> str:
        return "NULL" if v is None else repr(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
            else str(v).upper() if isinstance(v, bool) else "'" + str(v).replace("'", "''") + "'"
    parts = []
    for c, t in zip(self.relation.columns, [str(t) for t in self.relation.types]):
        same_kind = [(k, v) for k, v in pairs.items() if (isinstance(k, str) == (t.upper() == "VARCHAR"))
                     and (not isinstance(k, (int, float)) or isinstance(k, bool) or _numeric(t))]
        if (subset is not None and c not in subset) or not same_kind:
            parts.append(_q(c))
            continue
        cases = " ".join(f"WHEN {_q(c)} = {lit(k)} THEN {lit(v)}" for k, v in same_kind)
        parts.append(f"CASE {cases} ELSE {_q(c)} END AS {_q(c)}")
    return _new(self, self.relation.project(", ".join(parts)))


def corr(self: DataFrame, col1: str, col2: str, method: str | None = None) -> float:
    return self.relation.aggregate(f"corr({_q(col1)}, {_q(col2)})", "").fetchone()[0]


def cov(self: DataFrame, col1: str, col2: str) -> float:
    return self.relation.aggregate(f"covar_samp({_q(col1)}, {_q(col2)})", "").fetchone()[0]


def approxQuantile(self: DataFrame, col: Any, probabilities: list[float], relativeError: float) -> list[Any]:  # noqa: N802, N803, ARG001
    cols = [col] if isinstance(col, str) else list(col)
    out = []
    for c in cols:
        row = self.relation.aggregate(", ".join(f"quantile_cont({_q(c)}, {float(p)})" for p in probabilities), "").fetchone()
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


def _same(self: DataFrame, *_a: Any, **_kw: Any) -> DataFrame:
    """Calls that only change how Spark spreads or keeps the work (partitions, caching, hints):
    on this PC the data frame is the same."""
    return self


def _rdd(self: DataFrame, *_a: Any, **_kw: Any) -> Any:
    raise NotImplementedError("RDDs and row-by-row Python (rdd, foreach, foreachPartition, mapInPandas) run in "
                              "Fabric. On this PC, use DataFrame methods, SQL or toPandas().")


ADDED: dict[str, Any] = {
    "agg": agg, "selectExpr": selectExpr, "fillna": fillna, "dropna": dropna, "describe": describe,
    "summary": summary, "isEmpty": isEmpty, "sample": sample, "tail": tail, "explain": explain,
    "unpivot": unpivot, "melt": unpivot, "createTempView": createTempView, "registerTempTable": DataFrame.createOrReplaceTempView,
    "toLocalIterator": toLocalIterator, "offset": offset, "subtract": subtract, "replace": replace, "corr": corr,
    "cov": cov, "approxQuantile": approxQuantile, "randomSplit": randomSplit,
    "createOrReplaceGlobalTempView": DataFrame.createOrReplaceTempView,
    "repartition": _same, "repartitionByRange": _same, "coalesce": _same, "persist": _same, "unpersist": _same,
    "checkpoint": _same, "localCheckpoint": _same, "hint": _same, "sortWithinPartitions": DataFrame.orderBy,
    "foreach": _rdd, "foreachPartition": _rdd, "mapInPandas": _rdd, "mapInArrow": _rdd,
}


def install() -> None:
    """Add the methods to DuckDB's DataFrame (once; spark_local calls it)."""
    for name, fn in ADDED.items():
        if name not in DataFrame.__dict__:
            setattr(DataFrame, name, fn)
    if "na" not in DataFrame.__dict__:
        DataFrame.na = property(lambda self: _NA(self))  # type: ignore[attr-defined]
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
    "mapInPandas": "runs in Fabric", "mapInArrow": "runs in Fabric", "writeStream": "streaming runs in Fabric",
    "withWatermark": "streaming runs in Fabric", "dropDuplicatesWithinWatermark": "streaming runs in Fabric",
    "isStreaming": "streaming runs in Fabric", "readStream": "streaming runs in Fabric", "streams": "streaming runs in Fabric",
    "udf": "Python UDFs run in Fabric", "udtf": "Python UDTFs run in Fabric", "pandas_api": "pandas-on-Spark runs in Fabric; use toPandas()",
}

NO_OP = {"repartition", "repartitionByRange", "coalesce", "persist", "unpersist", "checkpoint", "localCheckpoint",
         "hint", "cache"}


def coverage() -> list[dict[str, str]]:
    """Each PySpark 3.5 method: "works", "no-op" (accepted; nothing to do on one PC) or "Fabric"
    (raises, with why). A method is "works" only if calling it doesn't raise DuckDB's not-implemented error."""
    from duckdb.experimental.spark.exception import ContributionsAcceptedError

    from .spark_local import LocalSparkSession
    install()
    rows = []
    for area, names, owner in (("DataFrame", PYSPARK_DATAFRAME, DataFrame), ("SparkSession", PYSPARK_SESSION, LocalSparkSession)):
        for name in names:
            attr = None
            for klass in owner.__mro__:
                if name in klass.__dict__:
                    attr = klass.__dict__[name]
                    break
            if name in NOT_HERE:
                state = "Fabric"
            elif attr is None or _not_implemented(attr, ContributionsAcceptedError):
                state = "missing"
            elif name in NO_OP:
                state = "no-op"
            else:
                state = "works"
            rows.append({"area": area, "name": name, "state": state, "why": NOT_HERE.get(name, "")})
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
