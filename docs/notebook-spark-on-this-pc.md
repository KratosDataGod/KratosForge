# Spark on this PC

In a notebook on this PC, `spark` is DuckDB's PySpark-compatible API wired to the attached lakehouse ([notebooks](notebooks.md#fabric-notebook-code-on-this-pc)). There is no Spark cluster: everything runs on this PC, and when the notebook runs in Fabric the same code runs on Fabric's real Spark. This page lists what of PySpark 3.5's API works here, so you know before you run.

## Settings: `spark.conf`

`spark.conf.set`, `get`, `unset`, `getAll` and `isModifiable` work, and so does `SparkSession.builder.config(...)`. Every key you set is kept and returned, so a notebook that sets Fabric settings still runs here:

- **Applied on this PC:** `spark.sql.session.timeZone` (UTC unless you set it) and `spark.app.name`.
- **Kept, take effect in Fabric:** everything else, such as `spark.sql.shuffle.partitions`, `spark.sql.parquet.vorder.default`, `spark.microsoft.delta.optimizeWrite.enabled`, `spark.sql.ansi.enabled` and executor or driver settings. Known keys return Fabric's defaults until you set them.

`spark.conf.explain()` prints each setting and whether it applies here. `spark.conf.get` of a key that isn't set and has no default stops with a message, as in Spark; pass a default to avoid it.

## `spark.sparkContext`

`version` and `spark.version` say 3.5.0, the PySpark API this follows. `applicationId`, `defaultParallelism` (this PC's processors), `startTime`, `sparkUser()`, `getConf()`, `setLogLevel`, `setJobDescription`, `setJobGroup` and local properties work; `uiWebUrl` is None because there's no Spark UI. RDDs (`parallelize`, `textFile`, `broadcast`, `accumulator`) run in Fabric.

## DataFrame methods

Calls that only change how Spark spreads or keeps work across a cluster (`repartition`, `coalesce`, `persist`, `cache`, `checkpoint`, `hint`) are accepted and do nothing, since everything runs in one process here. `spark.catalog.tableExists` checks temp views and the attached lakehouse. The tables below are generated from the code (`spark_api.coverage_markdown()`), and a test fails if they fall out of step.

`spark.createDataFrame` takes a list of column names, a `StructType` or a DDL string such as
`"id int NOT NULL, name string, tags array<int>, address struct<city:string, zip:string>"`. A `NOT NULL` field
refuses a row with a null there, as Spark does (also inside structs, and for array elements or map values a `StructType` declares
non-nullable, with PySpark's
`[FIELD_NOT_NULLABLE_WITH_NAME]` error; `verifySchema=False` skips the check); a column `COMMENT` is accepted and left out, since it belongs to a
table's catalog. `df.schema` shows every field as nullable on this PC.

`sample`, `dropna` and `unpivot` check their arguments as Spark does: a fraction outside 0 to 1, a `how`
other than `any` or `all`, and an empty list of value columns are errors rather than quietly clamped or widened.
`na.fill`, `na.drop`, `na.replace` and `describe` name a column that isn't there with Spark's
`[UNRESOLVED_COLUMN]` error instead of skipping it, and `replace` refuses lists of different lengths.
`agg` takes Spark's `{column: function}` form after `groupBy` and `pivot` too, and names the result as Spark does
(`avg(x)` for `"mean"`, `count(1)` for `{"*": "count"}`).
`drop` and `withColumnRenamed` match names ignoring case and skip a column that isn't there, as Spark does, and
`dropDuplicates` names a missing column with `[UNRESOLVED_COLUMN]`. Dropping every column is an error here: Spark keeps a frame with no columns,
which DuckDB can't hold. `drop` also takes `F.col("ID")` for `id`. `summary` takes statistics, not columns, and
adds the quartiles (`25%`, `50%`, `75%`) as Spark does; describe a few columns with `describe`.
`substring` counts position 0 as 1 and `concat` is NULL when any input is NULL, as in Spark, in
`F.substring`, `F.concat`, `selectExpr` and `spark.sql` alike. `initcap` lower-cases the rest of each word, as Spark does
(`hELLO` becomes `Hello`). `unix_timestamp` takes a date or timestamp as well as text, reads it in the session
time zone, and is NULL for text that doesn't match the format, as in Spark. Its format reads fractions of a second (`SSS`)
and quoted text such as `'T'` and `'Z'`; a pattern letter this PC can't read is an error rather than a NULL.
A pattern without a year reads its date from 1970-01-01, as Spark does. Quoted text that repeats a pattern letter (`'yyyy'`)
isn't read back as written, so such a pattern gives NULL; keep quoted text to words such as `'T'`, `'Z'` or `'at'`.
`groupBy()` with no columns aggregates the whole frame, and `first` and `last` with `ignorenulls=True` (or
`first(x, true)` in SQL, in a window too) skip NULLs, as in Spark. `slice(arr, start, length)` takes a length and counts a
negative start from the end, and `add_months` returns a date, as in Spark. `slice` refuses index 0 and a negative
length with Spark's error, and `add_months` reads text as a date (text that isn't a date gives NULL).
`element_at` refuses an array's index 0, and `months_between` counts the time of day and rounds to 8 digits
(unless `roundOff` is false), as in Spark. An empty `struct<>` isn't supported here,
since DuckDB can't store a struct with no fields; give it at least one. `NOT NULL` on a field inside a struct
is accepted in SQL but not checked, since DuckDB has no such constraint. In SQL, a selected `add_months`,
`months_between`, `substring` or `slice` without an alias gets a short name close to Spark's, such as
`add_months(d, 1)`; Spark's names differ for literals (it drops their quotes) and add defaults such as
`months_between`'s `roundOff`.

### DataFrame: 79 of 102

| Method | On this PC |
| --- | --- |
| `agg` | Works |
| `alias` | Works |
| `approxQuantile` | Works |
| `cache` | Accepted, does nothing on one PC |
| `checkpoint` | Accepted, does nothing on one PC |
| `coalesce` | Accepted, does nothing on one PC |
| `colRegex` | Not yet on this PC |
| `collect` | Works |
| `columns` | Works |
| `corr` | Works |
| `count` | Works |
| `cov` | Works |
| `createGlobalTempView` | Works |
| `createOrReplaceGlobalTempView` | Works |
| `createOrReplaceTempView` | Works |
| `createTempView` | Works |
| `crossJoin` | Works |
| `crosstab` | Not yet on this PC |
| `cube` | Not yet on this PC |
| `describe` | Works |
| `distinct` | Works |
| `drop` | Works |
| `dropDuplicates` | Works |
| `dropDuplicatesWithinWatermark` | Runs in Fabric: streaming runs in Fabric |
| `dropna` | Works |
| `dtypes` | Works |
| `exceptAll` | Works |
| `explain` | Works |
| `fillna` | Works |
| `filter` | Works |
| `first` | Works |
| `foreach` | Runs in Fabric: row-by-row Python runs in Fabric |
| `foreachPartition` | Runs in Fabric: runs in Fabric |
| `freqItems` | Not yet on this PC |
| `groupBy` | Works |
| `groupby` | Works |
| `head` | Works |
| `hint` | Accepted, does nothing on one PC |
| `inputFiles` | Not yet on this PC |
| `intersect` | Works |
| `intersectAll` | Works |
| `isEmpty` | Works |
| `isLocal` | Works |
| `isStreaming` | Works |
| `join` | Works |
| `limit` | Works |
| `localCheckpoint` | Accepted, does nothing on one PC |
| `mapInArrow` | Runs in Fabric: runs in Fabric |
| `mapInPandas` | Runs in Fabric: runs in Fabric |
| `melt` | Works |
| `na` | Works |
| `observe` | Not yet on this PC |
| `offset` | Works |
| `orderBy` | Works |
| `pandas_api` | Runs in Fabric: pandas-on-Spark runs in Fabric; use toPandas() |
| `persist` | Accepted, does nothing on one PC |
| `printSchema` | Works |
| `randomSplit` | Works |
| `rdd` | Runs in Fabric: RDDs run in Fabric |
| `registerTempTable` | Works |
| `repartition` | Accepted, does nothing on one PC |
| `repartitionByRange` | Accepted, does nothing on one PC |
| `replace` | Works |
| `rollup` | Not yet on this PC |
| `sameSemantics` | Not yet on this PC |
| `sample` | Works |
| `sampleBy` | Not yet on this PC |
| `schema` | Works |
| `select` | Works |
| `selectExpr` | Works |
| `semanticHash` | Not yet on this PC |
| `show` | Works |
| `sort` | Works |
| `sortWithinPartitions` | Works |
| `sparkSession` | Works |
| `stat` | Works |
| `storageLevel` | Not yet on this PC |
| `subtract` | Works |
| `summary` | Works |
| `tail` | Works |
| `take` | Works |
| `to` | Not yet on this PC |
| `toDF` | Works |
| `toJSON` | Not yet on this PC |
| `toLocalIterator` | Works |
| `toPandas` | Works |
| `transform` | Works |
| `union` | Works |
| `unionAll` | Works |
| `unionByName` | Works |
| `unpersist` | Accepted, does nothing on one PC |
| `unpivot` | Works |
| `where` | Works |
| `withColumn` | Works |
| `withColumnRenamed` | Works |
| `withColumns` | Works |
| `withColumnsRenamed` | Works |
| `withMetadata` | Not yet on this PC |
| `withWatermark` | Runs in Fabric: streaming runs in Fabric |
| `write` | Works |
| `writeStream` | Works |
| `writeTo` | Not yet on this PC |

### SparkSession: 14 of 17

| Method | On this PC |
| --- | --- |
| `builder` | Works |
| `catalog` | Works |
| `conf` | Works |
| `createDataFrame` | Works |
| `getActiveSession` | Works |
| `newSession` | Works |
| `range` | Works |
| `read` | Works |
| `readStream` | Works |
| `sparkContext` | Works |
| `sql` | Works |
| `stop` | Works |
| `streams` | Runs in Fabric: streaming runs in Fabric |
| `table` | Works |
| `udf` | Runs in Fabric: Python UDFs run in Fabric |
| `udtf` | Runs in Fabric: Python UDTFs run in Fabric |
| `version` | Works |

## Not on this PC

**Runs on** knows what this PC's Spark refuses, from the same list the run uses, so a notebook that uses any of it shows as running in Fabric only before you run it ([where a notebook runs](notebooks.md#where-a-notebook-runs)).

RDDs, Python UDFs, row-by-row Python (`foreach`, `mapInPandas`), pandas-on-Spark, continuous and Kafka or Event Hubs streams ([streaming on this PC](notebooks.md#fabric-notebook-code-on-this-pc) runs as availableNow), Scala and R run in Fabric. A notebook that uses them can still be edited here and run with **Runs on: Fabric** ([where a notebook runs](notebooks.md#where-a-notebook-runs)).
