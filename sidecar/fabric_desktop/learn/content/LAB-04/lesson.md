# Fix a broken load

A nightly job loads the day's orders into `silver_orders` by appending them. On day 2 the job wrote
its rows, then failed before it could report success, and the scheduler ran it again. Nobody noticed
until a report showed order 3 three times.

Two things went wrong, and they're common:

- **Append-only.** Day 2 sent order 3 again because its status changed. Appending kept the old row
  as well as the new one.
- **Not safe to retry.** Running the same load twice added the same rows twice.

## Repair, then prevent

1. **Find what's doubled.** `GROUP BY` the key, `HAVING count(*) > 1`.
2. **Rebuild** the table with one row per key, the latest load, as in Bronze to Silver (LM-06).
3. **Make the load safe to run again.** A load that leaves the same table whether it runs once or
   three times is called *idempotent*. `MERGE` is the usual way: it updates the rows that are
   already there and inserts only the new ones.

```sql
MERGE INTO silver_clean AS t
USING src_orders_day3 AS s ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET status = s.status
WHEN NOT MATCHED THEN INSERT (order_id, status) VALUES (s.order_id, s.status);
```

Delta tables in a Fabric lakehouse take `MERGE INTO` in Spark SQL with the same shape, and DuckDB
runs it on this PC. On a Fabric Warehouse, check the current T-SQL support for `MERGE` before you
rely on it; an `UPDATE` followed by an `INSERT ... WHERE NOT EXISTS` does the same job.

## Try it

1. Find every `order_id` that appears more than once in `silver_orders`: `order_id` and `copies`.
2. Rebuild `silver_orders` with one row per order, its latest load.
3. Load `src_orders_day3` into `silver_clean` so that running the load twice leaves one row per
   order. Write the load twice in your answer to prove it.
