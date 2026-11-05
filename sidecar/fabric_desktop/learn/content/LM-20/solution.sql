SELECT check_name, failures
FROM (
    SELECT 'order_id_unique' AS check_name, count(*) - count(DISTINCT order_id) AS failures FROM orders
    UNION ALL
    SELECT 'amount_not_negative', count(*) FROM orders WHERE amount < 0
    UNION ALL
    SELECT 'customer_id_present', count(*) FROM orders WHERE customer_id IS NULL
)
WHERE failures > 0
ORDER BY check_name;
