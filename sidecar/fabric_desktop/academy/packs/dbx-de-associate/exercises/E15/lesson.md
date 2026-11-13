# A database table to bronze over JDBC: partitioned reads

**One connection is slow.** `spark.read.jdbc(url, "orders")` with no options reads the whole table
through one connection, in one task, however many cores the cluster has. For a large table that is the
bottleneck.

**Partitioned reads.** Give Spark a numeric column and a range, and it runs several queries at once,
each with its own `WHERE`:

```python
(spark.read.format("jdbc")
    .option("url", url).option("dbtable", "orders")
    .option("partitionColumn", "order_id")
    .option("lowerBound", 1).option("upperBound", 1000)
    .option("numPartitions", 4)
    .load())
```

**The bounds don't filter.** `lowerBound` and `upperBound` only decide where the partitions split
(the stride is `(upperBound - lowerBound) / numPartitions`). Every row is still read: the first
partition takes everything below its upper edge (and NULLs), the last takes everything above its lower
edge. Treating the bounds as a filter silently drops the rows outside them, which is exactly what
happens when the table has grown past last year's `upperBound`.

**Each row once.** The middle partitions use `>= low AND < high`. `BETWEEN low AND high` includes both
ends, so every boundary row would be read by two partitions and land twice in bronze.

**On this PC.** The source database is a SQLite copy and `query()` stands in for one JDBC connection.
What this doesn't show: the JDBC driver, the connection's secret (read with `dbutils.secrets.get` in
Databricks), `fetchsize`, the load your queries put on the source, or Spark running the partitions in
parallel.
