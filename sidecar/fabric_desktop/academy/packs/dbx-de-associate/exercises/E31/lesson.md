# Quality rules and a quarantine table in plain PySpark

Lakeflow Declarative Pipelines have expectations that warn, drop or fail (exercise E30). Outside a
pipeline, the same idea is a few lines of PySpark: compute why each row is bad, then split.

```python
reason = (
    F.when(F.col("device_id").isNull(), "missing_device")
    .when(F.col("temperature").isNull(), "missing_temperature")
    .when((F.col("temperature") < -50) | (F.col("temperature") > 60), "temperature_out_of_range")
)
checked = readings.withColumn("reason", reason)          # NULL when no rule is broken
valid = checked.filter(F.col("reason").isNull()).drop("reason")
quarantine = checked.filter(F.col("reason").isNotNull())
```

**Why not `filter(rule)` and `filter(~rule)`?** SQL logic has three values. A comparison with NULL is
neither true nor false but NULL, and `filter` keeps only true rows, so `filter(temp <= 60)` and
`filter(~(temp <= 60))` both drop a row whose temperature is NULL. That row vanishes from both
tables without a trace. Test for NULL explicitly, as the `when` chain does, and every row lands in
exactly one table.

**Edges.** "Between -50 and 60" includes both ends: `col.between(-50, 60)` is
`col >= -50 AND col <= 60`. A quarantine that rejects the edges loses real readings.

**Order the rules.** `when` stops at the first rule a row breaks, so the reason names the most basic
problem first. Keep the rule list in one place so the valid table and the quarantine can't disagree.

Your code is checked on the exercise data and again on rows you haven't seen. On this PC, `spark` is
DuckDB's PySpark-compatible API, not Apache Spark. `Column.between` isn't available here yet, so
write the two comparisons.
