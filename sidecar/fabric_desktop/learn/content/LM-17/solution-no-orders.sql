SELECT c.customer_id, c.name
FROM customers AS c
WHERE NOT EXISTS (SELECT 1 FROM orders AS o WHERE o.customer_id = c.customer_id)
ORDER BY c.customer_id;
