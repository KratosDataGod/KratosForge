SELECT 'duplicate order_id' AS problem, count(*) - count(DISTINCT order_id) AS row_count FROM raw_orders;
