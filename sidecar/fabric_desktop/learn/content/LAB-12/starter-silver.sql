SELECT order_id, customer_id, amount
FROM raw_orders
WHERE customer_id IS NOT NULL AND amount > 0
ORDER BY order_id;
