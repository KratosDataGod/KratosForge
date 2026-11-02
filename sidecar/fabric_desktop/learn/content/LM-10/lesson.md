# QUALIFY: filter on a window function

A window function, such as `row_number()` or `rank()`, is worked out after `WHERE` has already
filtered the rows, so `WHERE` can't use it. The usual way round that is a subquery: number the rows
inside, filter them outside (LM-06 does it this way).

**QUALIFY** is a clause that filters on window functions directly. It runs after the window
functions are worked out, the way `HAVING` runs after `GROUP BY`:

| Clause | Filters | Runs |
| --- | --- | --- |
| `WHERE` | Rows | Before grouping |
| `HAVING` | Groups | After `GROUP BY` |
| `QUALIFY` | Rows, using window functions | After the window functions |

## The top row of each group

```sql
SELECT customer_id, email, updated_at
FROM customers
QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY updated_at DESC) = 1;
```

This keeps each customer's newest row in one query. The window's `ORDER BY` decides which row is
first, so when two rows can tie, add a column that breaks the tie, such as the key. Without one,
the engine may pick either row, and two runs can disagree.

`row_number() = 1` keeps exactly one row per group. `rank() = 1` keeps every row that ties for
first, so a group can come back with more than one.

## Where it runs

Snowflake, Databricks SQL and DuckDB all have `QUALIFY`, so the query above runs on this PC as
written. Fabric Warehouse and SQL Server (T-SQL) don't have it: there, write the subquery from
LM-06. When a query has to run in both places, the subquery is the safe choice.

## Try it

`orders` holds seven orders in three regions. Return each region's biggest order: `region`,
`order_id` and `amount`, one row per region. North has two orders of the same amount; keep the
smaller `order_id`. Sort by `region`.
