# Deduplicate, then aggregate

Aggregates are only as good as the rows under them, so deduplication comes first.

**Exact redeliveries.** When a feed can deliver the same event twice, it carries an id for it:
`dropDuplicates(["event_id"])` keeps one row per id. `dropDuplicates()` with no columns keeps one of
each identical row. Never deduplicate on business columns such as `customer_id` and `amount`: two real
orders of the same amount by one customer look the same and one of them disappears.

**The latest version of a row.** For slowly changing data, rank each key's versions and keep the
first:

```python
latest = Window.partitionBy("customer_id").orderBy(F.col("valid_from").desc())
current = versions.withColumn("rn", F.row_number().over(latest)).filter("rn = 1")
```

**Aggregates.** `groupBy("country").agg(...)` with:

- `F.count("*")` for rows, `F.countDistinct("customer_id")` for distinct customers;
- `F.sum`, `F.avg` (or `F.mean`), `F.min`, `F.max`;
- `F.approx_count_distinct(col, rsd)` estimates distinct counts on very large data with a bounded
  relative error (default 5%). It's much cheaper than an exact count, but its answer can change, so
  nothing is checked here with it.

`df.describe()` and `df.summary()` print count, mean, stddev, min and max (summary adds percentiles)
for a quick look at a DataFrame; they're for exploring, not for gold tables.

Your code is checked on the exercise data and again on rows you haven't seen. On this PC, `spark` is
DuckDB's PySpark-compatible API, not Apache Spark.
