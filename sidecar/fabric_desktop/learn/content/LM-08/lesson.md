# Notebooks: let the engine do the work

A Fabric notebook is a list of cells. A cell can be PySpark (Python), Spark SQL, Scala or R, and
they all read the same tables: the lakehouse attached to the notebook. These two cells give the same
answer:

```python
df = spark.sql("SELECT region, count(*) AS orders FROM stores GROUP BY region")
```

```sql
%%sql
SELECT region, count(*) AS orders FROM stores GROUP BY region
```

## The driver and the executors

Spark spreads the work over several machines, the **executors**. Your notebook's Python runs on one
more, the **driver**. A query's work stays on the executors until you ask for the rows:

- `display(df)` and `df.show()` bring back a limited number of rows.
- `df.collect()` and `df.toPandas()` bring back **every** row, to the driver's memory.

A table that fits today can run the driver out of memory next month. KratosForge's notebook checks
flag `collect()` and `toPandas()` without a limit (NB003).

## Aggregate first

Ask the engine for the answer, not the data. Grouping, filtering and joining in the query means only
the few rows you need come back, whether the engine is Spark in Fabric or DuckDB on this PC.

```python
# Brings back every order, then adds them up in Python
rows = spark.sql("SELECT * FROM orders").toPandas()
# Brings back one row per region
by_region = spark.sql("SELECT region, sum(amount) AS revenue FROM orders GROUP BY region").toPandas()
```

## Try it

The lab has a small retailer's orders and stores, generated on this PC. The starter brings back
every order. Change it to return revenue per store region from complete orders: `region`,
`orders` (how many) and `revenue`, largest revenue first.
