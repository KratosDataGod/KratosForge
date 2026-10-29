# Your first query on this PC

A query answers one question about a table. It runs here, on this PC, with DuckDB: no Fabric job
starts and no capacity is used.

## Pick the columns

`SELECT` names the columns the answer needs. `SELECT *` returns every column, which is handy for a
first look but reads and moves more data than the question needs.

```sql
SELECT order_id, customer, amount
FROM orders;
```

## Keep the rows that matter

`WHERE` keeps only the rows that match. Combine conditions with `AND` when every one must hold.

```sql
WHERE region = 'North'
  AND amount > 100
```

Compare the column itself, not a function of it. `WHERE amount > 100` lets the engine skip blocks
of rows it knows can't match, and best-practice rule SQ002 explains why a function such as
`YEAR(order_date) = 2026` stops that.

## Put the answer in order

Rows come back in no particular order unless you ask. `ORDER BY amount DESC` puts the largest
first.

## Try it

List the North region's orders over 100, largest first, with `order_id`, `customer` and `amount`.
Order 5 is exactly 100. Should it be in the answer?
