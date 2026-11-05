# CTEs: queries you can read top to bottom

A common table expression (CTE) names a step of a query with `WITH`. Each step reads like a
sentence, later steps can use earlier ones, and you can run a step on its own to check it.

```sql
WITH spend AS (                       -- step 1: one row per customer
    SELECT customer, sum(amount) AS total
    FROM orders
    WHERE order_date >= DATE '2026-01-01' AND order_date < DATE '2027-01-01'
    GROUP BY customer
)
SELECT customer, total                -- step 2: compare with the average of step 1
FROM spend
WHERE total > (SELECT avg(total) FROM spend);
```

## Average of what?

"Above average" needs one average, and the steps make it plain which: here, the average of the
customers' totals. A nested query that averages single orders reads almost the same and answers
a different question.

## When to use one

Use a CTE when a query has more than one idea in it, or uses the same subquery twice. It costs
nothing in DuckDB, Fabric Warehouse, Databricks or Snowflake: the optimizer plans the whole
query either way. Filter years with a date range, not `year(order_date)` (SQ002).

## Try it

Find the customers who spent more than the average customer in 2026.
