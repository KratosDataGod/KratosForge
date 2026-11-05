SELECT 'customer_id_present' AS check_name, count(*) AS failures
FROM orders
WHERE customer_id IS NULL;
