# Clean bronze into silver with PySpark

A bronze table keeps data as it arrived: every column is text, some keys are missing, dates come in
two formats, amounts carry thousands separators and one load wrote some rows twice. Silver is the
first layer people can trust, so cleaning happens on the way in.

The pattern is one DataFrame expression read from top to bottom:

1. **Filter** what can't be used: `filter(F.col("order_id").isNotNull())`.
2. **Standardize types**: `F.trim(...)` before `.cast("bigint")`; `F.to_date(col, format)` for each
   date format, combined with `F.coalesce` so the first format that parses wins.
3. **Clean values**: `F.regexp_replace(col, ",", "")` before casting to `decimal(10,2)`, and
   `F.when(... == "", None)` so a blank becomes NULL rather than a wrong number.
4. **Normalize labels**: `F.lower(F.trim(...))`, with a default for blanks.
5. **Deduplicate**: `dropDuplicates(["order_id"])` when the duplicates are exact copies.
6. **Write so a rerun is safe**: `mode("overwrite")` replaces the table; `append` would add the same
   rows again on every run.

Your code is checked on the exercise data and again on rows you haven't seen, so write rules, not
fixes for particular rows.

On this PC, `spark` is DuckDB's PySpark-compatible API reading and writing Delta tables in the
exercise folder. It is not Apache Spark: a value that can't be cast stops the run here, while Spark's
result depends on its ANSI setting.
