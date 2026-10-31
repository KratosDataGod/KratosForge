SELECT order_id, order_date, amount, arrived_at
FROM source_orders
WHERE arrived_at > (SELECT last_watermark FROM load_state)
ORDER BY order_id;
