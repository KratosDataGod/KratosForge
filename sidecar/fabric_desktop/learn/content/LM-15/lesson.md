# Window functions: running totals and rankings

`GROUP BY` turns many rows into one. A window function keeps every row and adds a value worked
out from rows related to it: a running total, a rank, the previous month's figure.

## OVER says which rows

```sql
sum(amount) OVER (
    PARTITION BY region          -- start again for each region
    ORDER BY month               -- in this order
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW   -- from the first row to this one
)
```

With an empty `OVER ()` every row sees the whole table. Without `PARTITION BY` the total runs
across regions. Write the frame (`ROWS BETWEEN ...`) out: with `ORDER BY` and no frame, rows with
the same month share one total, which surprises people.

## Ranks and neighbours

`row_number()`, `rank()` and `dense_rank()` number rows in each partition; `lag(amount)` and
`lead(amount)` read the row before or after. They all work the same in Fabric Warehouse,
Databricks and Snowflake. To filter on one, wrap the query or use `QUALIFY` where the platform
has it (LM-10).

## Try it

Show each region's running total by month.
