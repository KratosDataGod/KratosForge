SELECT o.order_id, c.name, o.amount
FROM orders AS o
JOIN customers AS c ON c.customer_id = o.customer_id
ORDER BY o.order_id;
