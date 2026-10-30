# Lakehouses and the medallion layers

A Fabric **lakehouse** is a place in OneLake that holds two things: **tables**, stored as Delta
tables (Parquet files plus a log of changes), and **files**, anything else you land there, such as
CSV or JSON. Spark notebooks, the SQL analytics endpoint and Power BI all read the same tables, so
there is one copy of the data.

## Bronze, Silver and Gold

The medallion pattern splits the work into layers, each a step closer to what people use:

| Layer | What it holds | Rule of thumb |
| --- | --- | --- |
| Bronze | Data as it arrived, every load kept | Never fix anything here, so you can always start again |
| Silver | Cleaned, typed rows, one per key | The layer other teams build on |
| Gold | Tables shaped for one use, such as a report's star schema | Named in the business's words |

In KratosForge each layer is its own lakehouse, with schemas turned on. Moving from Bronze to Silver
is where most of the cleaning happens:

- **Types.** Raw files often arrive as text. Silver casts each column to the type it should be
  (LM-02).
- **One row per key.** A source that sends an order again, because it changed, gives Bronze two
  rows. Silver keeps the latest.
- **One spelling.** `Complete` and `complete` are the same status, so pick one.

## Keep the latest load

When every Bronze row carries the time it was loaded, a window function picks the newest row of each
key:

```sql
SELECT order_id, order_date, amount, status
FROM (
    SELECT *, row_number() OVER (PARTITION BY order_id ORDER BY loaded_at DESC) AS load_rank
    FROM bronze_orders
) AS ranked
WHERE load_rank = 1;
```

The outer query lists its columns, so `load_rank` stays behind, and the inner query has a name
(`AS ranked`), which T-SQL requires. Written this way it runs in DuckDB on this PC, in a Fabric
notebook's Spark SQL cell and on a Fabric Warehouse, so you can build the step here and run it in
Fabric later.

## Try it

`bronze_orders` holds five rows from three loads, all as text. Write the SELECT that Silver's
orders come from: `order_id` as INTEGER, `order_date` as DATE, `amount` as DECIMAL(12,2) and `status`
in lower case, one row per order, smallest `order_id` first.
