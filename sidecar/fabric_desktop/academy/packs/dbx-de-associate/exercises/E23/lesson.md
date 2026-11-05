# Columns and rows: add, split, rename, drop, filter and explode

Most silver tables reshape their source. The DataFrame methods for it read top to bottom:

- **Filter rows**: `filter(F.col("status") != "discontinued")` (`where` is the same method).
- **Add or replace a column**: `withColumn("price", (F.col("price_cents") / 100).cast("decimal(10,2)"))`.
  `withColumns({...})` adds several at once.
- **Split a string**: `F.split(col, pattern)` returns an array. The pattern is a regular expression,
  so a literal `|` is written `"\\|"`. Take parts with `F.element_at(arr, 1)` (1-based) or
  `arr.getItem(0)` (0-based).
- **Rename**: `withColumnRenamed("product_id", "id")`.
- **Drop**: `drop("legacy_code", ...)`, or keep exactly what you want with `select(...)`.
- **One row per array element**: `F.explode(arr)` repeats the row for each element. A NULL or empty
  array gives no rows; `F.explode_outer` keeps the row with a NULL instead. `F.posexplode` adds the
  element's position.

Clean after exploding: `F.lower(F.trim(F.col("tag")))`, because `"outdoor, Sale"` splits into
`"outdoor"` and `" Sale"`.

Your code is checked on the exercise data and again on rows you haven't seen. On this PC, `spark` is
DuckDB's PySpark-compatible API, not Apache Spark. `getItem` on an array column isn't available
here yet; `F.element_at` is, and it means the same in Spark.
