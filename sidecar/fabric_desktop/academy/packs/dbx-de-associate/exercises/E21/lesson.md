# Combine DataFrames: unions and joins

**Stacking rows.** `df1.union(df2)` matches columns by position, as SQL's `UNION ALL` does. When two
sources have the same columns in a different order, it puts `region` under `order_id` without
complaint, or fails on a type it can't reconcile. `df1.unionByName(df2)` matches by name, and
`unionByName(df2, allowMissingColumns=True)` fills a column one side lacks with NULL. Neither removes
duplicates: add `.distinct()` when you want that.

**Adding columns from another table.** `orders.join(customers, on=[...], how=...)`:

- `how="inner"` keeps only orders that found a customer. An order whose customer hasn't arrived yet
  disappears, and revenue drops without an error.
- `how="left"` keeps every order and leaves the customer's columns NULL when there's no match. Use
  `F.coalesce(col, F.lit("unknown"))` to label them.
- Other types: `right`, `full` (`outer`), `left_semi` (orders that have a customer, with only the
  order's columns), `left_anti` (orders that don't), and `crossJoin` (every pair, which is rarely
  what you want on large tables).

**Join on the whole key.** When customers are identified by `customer_id` and `region` together, a
join on `customer_id` alone matches one order to every region's customer with that id, and the order
is counted twice. Pass every key column: `on=["customer_id", "region"]`. A list of names also keeps
one copy of each key column in the result; `on=orders.customer_id == customers.customer_id` keeps both.

Your code is checked on the exercise data and again on rows you haven't seen. On this PC, `spark` is
DuckDB's PySpark-compatible API, not Apache Spark: it shows the result of a join, not how Spark plans
it (broadcast or shuffle, exercise E22).
