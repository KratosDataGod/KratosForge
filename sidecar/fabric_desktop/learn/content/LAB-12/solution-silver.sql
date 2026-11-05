WITH latest AS (
    SELECT *, row_number() OVER (PARTITION BY order_id ORDER BY loaded_at DESC) AS rn
    FROM raw_orders
)
SELECT order_id, customer_id, amount
FROM latest
WHERE rn = 1 AND customer_id IS NOT NULL AND amount > 0
ORDER BY order_id;
