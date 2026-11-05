SELECT CAST(order_id AS INTEGER) AS order_id,
       CAST(order_date AS DATE) AS order_date,
       CAST(amount AS DECIMAL(12,2)) AS amount,
       lower(status) AS status
FROM (
    SELECT *, row_number() OVER (PARTITION BY order_id ORDER BY loaded_at DESC) AS load_rank
    FROM bronze_orders
) AS ranked
WHERE load_rank = 1
ORDER BY order_id;
