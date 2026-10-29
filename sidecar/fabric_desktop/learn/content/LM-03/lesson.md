# Joins without surprise row counts

A join puts columns from two tables side by side, matching rows on a key. Most join bugs show up as
the wrong number of rows, so count them before and after.

## Match on the key

`JOIN ... ON` keeps the pairs of rows whose keys match. Without `ON`, or with `CROSS JOIN`, every
row meets every other row: 1,000 orders and 1,000 customers become a million rows. Rule SQ006 flags
that, because it is rarely what the question needs.

```sql
SELECT o.order_id, c.name
FROM orders AS o
JOIN customers AS c ON c.customer_id = o.customer_id;
```

## A key that isn't unique multiplies rows

When a customer appears twice in `customers`, each of their orders matches twice and the order is
counted twice. Before joining, check the key is unique on the side you look up:

```sql
SELECT customer_id, count(*)
FROM customers
GROUP BY customer_id
HAVING count(*) > 1;
```

Fix it at the source if you can. If not, join to a de-duplicated version, such as
`(SELECT DISTINCT customer_id, name FROM customers)`, and say why in a comment.

## Rows with no match

An inner `JOIN` drops an order whose customer isn't in `customers`. `LEFT JOIN` keeps every row of
the left table and leaves the right side's columns empty (NULL) when nothing matches. Choose on
purpose: dropping rows silently changes totals.

## Try it

List every order once, with its customer's name and amount, in `order_id` order. Order 105's
customer is missing; keep the order with an empty name.
