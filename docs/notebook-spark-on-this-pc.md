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

Calls that only change how Spark spreads or keeps work across a cluster (`repartition`, `coalesce`, `persist`, `cache`, `checkpoint`, `hint`) are accepted and do nothing, since everything runs in one process here. `spark.catalog` answers about temp views and the attached lakehouse: `tableExists`, `listColumns` (which marks a lakehouse table's partition columns, from its Delta log), `currentDatabase` (the lakehouse's name; refused for a lakehouse with schemas or no lakehouse) and `setCurrentDatabase`. `listTables`, `listDatabases`, `listCatalogs` and `listFunctions` are refused by name, since on this PC they would list DuckDB's own objects; the Explorer and `notebookutils.lakehouse.listTables()` list the lakehouse's tables. The tables below are generated from the code (`spark_api.coverage_markdown()`), and a test fails if they fall out of step.

`spark.createDataFrame` takes a list of column names, a `StructType` or a DDL string such as
`"id int NOT NULL, name string, tags array<int>, address struct<city:string, zip:string>"`. A `NOT NULL` field
refuses a row with a null there, as Spark does (also inside structs, and for array elements or map values a `StructType` declares
non-nullable, with PySpark's
`[FIELD_NOT_NULLABLE_WITH_NAME]` error; `verifySchema=False` skips the check); a column `COMMENT` is accepted and left out, since it belongs to a
table's catalog. `df.schema` shows every field as nullable on this PC. A `NaN` in the data stays `NaN`, as in Spark (inside an array or struct it
becomes NULL with the DuckDB this app ships), and `F.isnan` of a NULL is false.

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

These `F` functions work as in Spark, with Spark's names, types and NULLs: `bool_and`, `every`, `bool_or`, `some`,
`count_if`, `bit_and`, `bit_or` and `bit_xor`; `log` (one argument is the natural logarithm), `ln`, `log2`, `log10`
and `log1p`, NULL at or below zero where DuckDB's would stop the cell; `pow` and `power` (a double), `factorial`
(NULL below 0 or above 20) and `bin`; `locate`, `substring_index`, `translate`, `char_length` and
`character_length`; and `split` with a `limit` (at most that many parts, the last holding the rest).
`rand(seed)` and `randn(seed)` give the same numbers each run for a seed, also in `filter`, but not the
numbers Spark gives, which depend on how Spark splits the data.

`format_string` and `printf` follow Java's formatter, as Spark does, in `F`, `selectExpr` and `spark.sql`:
a NULL argument prints as `null` (`false` for `%b`), `%x` and `%o` print a negative number as its two's
complement, `%f` rounds half up, and a double prints as Java writes it (`1.0E20`). `%d` of a double is an
error, as in Spark. `%g`, `%c`, `%h`, `%t` and the `#`, `(` and `<` flags stop the cell with a message instead
of printing something else. `conv` rewrites a number between bases as Spark's does. `selectExpr` and
`F.expr` read Spark SQL as `spark.sql` does and name a column as Spark does, such as `(id + 1)`.

`crosstab` (also `df.stat.crosstab`) counts each pair of values as Spark does: values written as Java writes them,
`null` for NULL, and the columns in sorted order. `colRegex` matches whole column names, ignoring case, with a
regex that reads Java's syntax. `toJSON` gives each row's JSON text, leaving out a row's or a struct's NULL fields
(a map keeps them), with `collect`, `first`, `take` and `count`; Spark returns an RDD, which this PC doesn't have.
`sampleBy` keeps each key's share of rows; a seed gives the same rows each time here, not the rows Spark picks.
A timestamp in `toJSON` is written as UTC (`...Z`), as Spark writes it in a UTC session.

`describe` and `summary` give each value as Spark writes it as text, with `stddev` to Spark's last digit (Spark's
running update over the rows in order), and the mean and stddev of a text column taken as numbers. With no column
named, `groupBy(...).max()`, `min()`, `sum()`, `avg()` and `mean()` take every number column, grouping ones too, and a
column that isn't a number is an error, as in Spark; the sum of whole numbers is a `bigint`. `printSchema` uses Spark's
type names (`long`, `integer`, `short`, `byte`) and shows a nested type one part per line, down to the level given.
`randomSplit` checks the weights as Spark does; its parts never overlap and together hold every row, and a seed gives
the same split each time, but not the rows Spark picks (Spark's depend on its partitions).

`spark.createDataFrame(rows)` with no types given infers the schema as PySpark does: a tuple, `Row` or namedtuple
inside a row is a struct (fields `_1`, `_2`, ... when it has no names), a dict inside a row is a map, a dict row's
keys come in sorted order, unnamed columns are `_1`, `_2`, ..., an int is a `bigint` and a `Decimal` a
`decimal(38,18)`. Every row's types are merged, and, as in Spark, types that can't merge (`1` and `2.5` in one
column) or a column with only NULLs is an error. Rows holding a `datetime` still take DuckDB's own inference
(columns `col0`, `col1`). A dict whose values don't share one type (`{"a": 1, "b": 2.5}`) is refused with
`CANNOT_INFER_TYPE_FOR_FIELD`; Spark refuses it too, with a field-type error. A struct's field is named `r.b` whether you write `r.getField("b")`, `r.b` or `r["b"]`,
and a map's key `m[k]`, as in Spark.

`url_decode` and `url_encode` in Spark SQL work as Spark's (Java's): `+` decodes to a space, a `%` that doesn't start
two hex digits is Spark's `CANNOT_DECODE_URL` error, and encoding keeps only `A-Z a-z 0-9 . - * _` (a space is `+`).
Decoding to bytes that aren't UTF-8 is refused by name, since Spark puts U+FFFD in their place. A quote or backslash in
a map key, a struct field name or an unpivoted column's name is read as written.

`withField` and `dropFields` edit a struct column as Spark's do: a field added, replaced or dropped, also in a nested
struct (`"e.f"`), a field matched whatever its case, a NULL struct left NULL, and the column named
`update_fields(a, ...)`. The fields a struct has are read from the frame, so an edited struct works as a whole column of
`select`, `withColumn` or `withColumns`, or as its field (`.c`); inside another function or a filter it's refused by
name: select it first and use it from there.

`select("a.*")` and `select(col("a.*"))` give every column of the DataFrame aliased `a` (after
`df.alias("a").join(df2.alias("b"), ...)`), and `select("s.*")` every field of struct `s`, as in Spark. A star that
names neither is Spark's `CANNOT_RESOLVE_STAR_EXPAND` error. After a select, the result no longer knows its columns'
aliases, so `a.id` works within the select but not in a later `sort` or `filter`: use the column's name there.

### DataFrame: 85 of 102

| Method | On this PC |
| --- | --- |
| `agg` | Works |
| `alias` | Works |
| `approxQuantile` | Works |
| `cache` | Accepted, does nothing on one PC |
| `checkpoint` | Accepted, does nothing on one PC |
| `coalesce` | Accepted, does nothing on one PC |
| `colRegex` | Works |
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
| `crosstab` | Works |
| `cube` | Works |
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
| `rollup` | Works |
| `sameSemantics` | Not yet on this PC |
| `sample` | Works |
| `sampleBy` | Works |
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
| `toJSON` | Works |
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
