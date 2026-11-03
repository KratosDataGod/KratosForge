# Incremental loads: a watermark that misses nothing

A full load copies every row each time. That's simple and always right, but it gets slower as the
source grows. An **incremental load** copies only what's new since the last load, and remembers
where it got to in a **watermark**: one value, such as a timestamp, saved after each load.

```sql
-- This load: everything after the watermark
SELECT * FROM source_orders
WHERE arrived_at > (SELECT last_watermark FROM load_state);
-- Then save the newest arrived_at it copied as the next watermark
```

## Pick a column that only grows

The watermark column has to grow every time a row is added or changed, or rows get missed.

| Column | Good watermark? | Why |
| --- | --- | --- |
| When the row arrived or changed, such as `arrived_at` or a modified time | Yes | Every new or changed row gets a later value |
| An identity or sequence id | Only for new rows | A changed row keeps its old id, so its change is missed |
| A business date, such as `order_date` | No | An order can arrive days after its date, behind the watermark |

A row that arrives late, dated before the watermark, is the usual way an incremental load
quietly loses data. Nothing fails; the row is just never copied.

## Where the next watermark comes from

Set the next watermark from the rows you copied: the largest `arrived_at` among them. Don't use the
clock (`now()`) at the end of the load. A row that lands while the load is running would have an
`arrived_at` before `now()` but after the rows you read, and the next load would skip it for good.

Use `>` against the watermark, not `>=`, so the last load's newest row isn't copied again. If the
source can stamp two rows with the same time across two loads, `>=` with a MERGE (LM-11) is the
safer pair, because copying a row twice then does no harm.

The same pattern works in a Python notebook, with DuckDB or Polars on this PC or Spark in Fabric
and Databricks: read the watermark, filter, write, then save the new watermark only after the
write succeeds.

## Try it

`source_orders` has six orders and `load_state` holds the last load's watermark. First, return
the rows this load should copy. Then return the watermark the next load should start from.
