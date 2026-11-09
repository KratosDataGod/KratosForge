# Nested JSON to Delta: struct fields and explode

**What the schema looks like.** When Auto Loader or `spark.read.json` reads JSON, an object becomes a
`struct` and a list becomes an `array`. A device object holding a site object is
`struct<id: string, site: struct<code: string, country: string>>`, and a list of readings is
`array<struct<metric: string, value: double>>`. `df.printSchema()` shows the tree.

**Reading fields inside a struct.** A dot path reads a field: `F.col("device.id")`, and one level
deeper, `F.col("device.site.code")`. In SQL it's the same, `device.site.code`. `F.col("device.*")`
expands every field of a struct into columns of its own.

**One row per element of an array.**

- `F.explode("readings")` gives one row per element. A row whose array is empty or NULL gives no row
  at all, so a device that sent nothing disappears without an error.
- `F.explode_outer("readings")` keeps that row once, with the element NULL.
- In Apache Spark, `posexplode` and `posexplode_outer` also give each element's position, starting
  at 0. This exercise doesn't need it.

Each element here is a struct, so read its fields with `F.col("reading.metric")`.

**Retries.** A feed that retries can land the same event twice. Drop duplicates on the event's key
before you flatten: after the explode, one duplicate event is several duplicate rows.

Your code is checked on the exercise data and again on rows you haven't seen. On this PC, `spark` is
DuckDB's PySpark-compatible API, not Apache Spark, and the bronze table starts with its schema already
inferred: reading raw JSON files with Auto Loader is not part of this exercise.
