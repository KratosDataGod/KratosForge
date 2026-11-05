# Broken: a cast that quietly turns amounts into NULL

`try_cast(x AS DECIMAL(12,2))` returns NULL for any value it can't read, where `cast` fails. That
makes a job stop failing, and it also makes it stop telling you anything. Here, every amount with a
currency sign or a thousands separator became NULL, and revenue fell with no error.

**Finding it.** Count what the cast lost: compare rows where the source had a value with rows where
the result is NULL.

```python
payments.filter(F.trim("amount") != "").count()            # values that came in
silver.filter(F.col("amount").isNull()).count()            # values that are now NULL
payments.filter(F.expr("try_cast(amount AS DECIMAL(12,2)) IS NULL")).show()
```

The rows that `show()` prints tell you which formats the source uses.

**Fixing it.** Clean first, then cast. Remove what isn't part of the number, such as `$`, `,` and
spaces, with `regexp_replace(amount, '[$, ]', '')`, and turn what's left blank into NULL with
`nullif(..., '')`. Keep `try_cast` only for values that really aren't amounts (`N/A`, `unknown`),
which should be NULL. Never replace them with 0: a missing amount and a zero amount mean different
things to finance.

In general, after any cast that can return NULL, check how many NULLs it made. A data quality rule
(exercise E31) can quarantine the rows instead of keeping them as NULL.

Your fix is checked on the exercise data and again on rows you haven't seen. On this PC, `spark` is
DuckDB's PySpark-compatible API, not Apache Spark: a plain `cast` of `N/A` gives NULL here, but on
Databricks with ANSI mode on it fails the job, which is why the job used `try_cast`.
