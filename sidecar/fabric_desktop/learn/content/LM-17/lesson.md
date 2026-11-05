# Anti-joins and safe de-duplication

Two everyday questions trip people up: "which rows have no match?" and "keep one row per key".

## NOT EXISTS, not NOT IN

`customer_id NOT IN (SELECT customer_id FROM orders)` looks right until one order has an empty
customer_id. Then, for every customer, the comparison with that NULL is unknown (LM-13), and the
query returns nothing at all. `NOT EXISTS` asks the question you meant:

```sql
SELECT c.customer_id, c.name
FROM customers AS c
WHERE NOT EXISTS (SELECT 1 FROM orders AS o WHERE o.customer_id = c.customer_id);
```

It works the same in Fabric Warehouse, Databricks and Snowflake, and engines plan it as an
anti-join, which is fast.

## One row per key

`DISTINCT` only removes rows that match in every column. To keep the latest row per customer,
number each customer's rows newest first and keep the first:

```sql
SELECT customer_id, email
FROM (SELECT *, row_number() OVER (PARTITION BY customer_id ORDER BY updated_at DESC) AS n
      FROM customer_updates)
WHERE n = 1;
```

Decide what "latest" means when two rows share a timestamp, and add a tie-breaker to the
`ORDER BY` if they can.

## Try it

Find the customers who never ordered, then each customer's latest email.
