# Loads that are safe to run again

Loads fail halfway, get retried by a scheduler, or are run twice by hand. An idempotent load
leaves the table the same however many times it runs. An append-only load doesn't: every retry
adds the same rows again, and totals quietly double.

## Replace by key

Take out what the batch is about to load, then add the batch:

```sql
DELETE FROM target WHERE order_id IN (SELECT order_id FROM batch);
INSERT INTO target (order_id, amount) SELECT order_id, amount FROM batch;
```

Run them in one transaction so a reader never sees the gap. Where a platform has `MERGE` (LM-11),
one statement does both (Databricks and Snowflake do; check your Fabric Warehouse release); in a Spark notebook, Delta's `MERGE` or
`replaceWhere` for a whole partition does the same. Name the columns: `INSERT ... SELECT *` breaks
or fills the wrong column when either table gains one (SQ010).

## Try it

A run loaded two orders and failed. Load the batch again so nothing is doubled.
