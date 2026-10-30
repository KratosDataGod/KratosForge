SELECT order_id, count(*) AS copies
FROM silver_orders
GROUP BY order_id
HAVING count(*) > 1
ORDER BY order_id;
