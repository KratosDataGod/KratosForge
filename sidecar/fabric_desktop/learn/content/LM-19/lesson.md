# Lazy pipelines: filter and choose columns early

A pipeline that loads a whole table into Python and then filters it moves every row and column
off disk, into memory, and then throws most of them away. A lazy pipeline describes the whole job
first, so the engine can read only the columns and rows the answer needs.

## The same idea everywhere

```python
# Polars: scan, don't read; nothing runs until collect()
(pl.scan_parquet("sales/*.parquet")
   .filter((pl.col("region") == "North") & (pl.col("sold_on") >= date(2026, 1, 1)))
   .group_by("product").agg(pl.col("amount").sum().alias("revenue"))
   .collect())

# DuckDB from Python: SQL over the files, one result back
duckdb.sql("SELECT product, sum(amount) ... WHERE region = 'North' ...").pl()
```

Spark DataFrames in Fabric and Databricks notebooks are lazy too: `df.filter(...).select(...)`
plans, and only an action such as `write` or `count` runs. Calling `.toPandas()` or `.collect()`
early pulls everything to one machine (rule NB003).

## What the engine can skip

With the filter and columns known up front, Parquet and Delta readers skip whole files and row
groups whose min and max can't match, and read only the columns named. Keep the filter on the bare
column: `year(sold_on) = 2026` hides it from that skipping (SQ002).

## Try it

Return North's 2026 revenue by product, all in one query.
