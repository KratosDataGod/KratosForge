# Window functions: latest rows, running totals, previous values and ranks

A window function computes a value for each row from a set of related rows (its window) without
collapsing them the way GROUP BY does. `OVER (PARTITION BY ... ORDER BY ...)` says which rows are
related and in what order.

**Keep the latest version of each row.** Change feeds write a new row per change. Number each key's
rows newest first and keep the first:

```sql
SELECT * FROM sales.order_events
QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY updated_at DESC) = 1
```

`QUALIFY` filters on a window function the way `HAVING` filters on an aggregate. Filter on other
columns only after you have picked the latest row: a `WHERE status <> 'cancelled'` before it removes
the cancellation itself, so an older version of a cancelled order comes back.

**Running totals.** `sum(amount) OVER (PARTITION BY customer_id ORDER BY order_date, order_id ROWS
BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)`. Spell out the frame. Databricks documents only that a
frame left out stops at the current row; Apache Spark (and DuckDB here) take it as `RANGE BETWEEN
UNBOUNDED PRECEDING AND CURRENT ROW` when there's an ORDER BY, which adds every row with the same
order_date at once, so two orders on one day get the same running total. A ROWS frame and a tie-breaker
in the ORDER BY mean the same thing on every engine.

**The previous row.** `lag(amount) OVER (PARTITION BY customer_id ORDER BY ...)` is NULL on each
customer's first order. Without PARTITION BY, the first order of one customer gets the last amount
of the one before. `lead` looks forward the same way.

**Ranks.** `row_number` never ties, `rank` gives ties the same number and then skips (1, 1, 3), and
`dense_rank` gives ties the same number without gaps (1, 1, 2).

Your query is checked on the exercise data and again on rows you haven't seen, with ties on dates and
amounts. On this PC it is translated to DuckDB SQL and run there; the parity table shows that
row_number, lag and dense_rank answer as Databricks documents.
