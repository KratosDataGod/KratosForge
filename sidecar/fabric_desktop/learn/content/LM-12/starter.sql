SELECT order_id, order_date, amount, arrived_at
FROM source_orders
WHERE order_date > (SELECT CAST(last_watermark AS DATE) FROM load_state)
ORDER BY order_id;
