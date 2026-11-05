SELECT order_id, count(*) AS copies
FROM silver_orders
GROUP BY order_id
ORDER BY order_id;
