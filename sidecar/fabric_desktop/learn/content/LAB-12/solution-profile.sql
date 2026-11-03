SELECT 'duplicate order_id' AS problem, count(*) - count(DISTINCT order_id) AS row_count FROM raw_orders
UNION ALL
SELECT 'missing customer', count(*) FILTER (WHERE customer_id IS NULL) FROM raw_orders
UNION ALL
SELECT 'amount not positive', count(*) FILTER (WHERE amount <= 0) FROM raw_orders
ORDER BY problem;
