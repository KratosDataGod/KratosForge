# Reading a query plan

The plan is the engine's recipe for your query. When a query is slow or returns too many rows,
the plan usually says why.

## Getting one

Put `EXPLAIN` in front of a query to see the plan without running it, or `EXPLAIN ANALYZE` to run
it and see how many rows and how much time each step took. In Fabric Warehouse use the estimated
or actual execution plan; Databricks and Snowflake show theirs in the query profile.

## Reading it

Read from the bottom up. The leaves are scans, one per table, with any filter pushed into them.
Above them sit joins, then aggregates, then sorting, then the result. Look for:

- **A step whose rows jump.** Joining 3 sales to 2 stores should give 3 rows, not 6.
- **CROSS_PRODUCT** (or a nested loop with no condition): every row meets every row. Rule SQ006
  flags a `CROSS JOIN` or a `JOIN` with no `ON`. Tables listed with commas and no condition linking
  them make the same plan, and the rule doesn't catch that, so the plan is where you see it.
- **A scan with no filter** on a big table when the query only needs a slice of it.

## Try it

`EXPLAIN` the starter, find the cross product, and fix the join.
