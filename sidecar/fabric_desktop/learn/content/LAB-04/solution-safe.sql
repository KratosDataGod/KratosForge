MERGE INTO silver_clean AS t
USING src_orders_day3 AS s ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET status = s.status, amount = s.amount, loaded_at = s.loaded_at
WHEN NOT MATCHED THEN INSERT (order_id, status, amount, loaded_at) VALUES (s.order_id, s.status, s.amount, s.loaded_at);

MERGE INTO silver_clean AS t
USING src_orders_day3 AS s ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET status = s.status, amount = s.amount, loaded_at = s.loaded_at
WHEN NOT MATCHED THEN INSERT (order_id, status, amount, loaded_at) VALUES (s.order_id, s.status, s.amount, s.loaded_at);

SELECT order_id, status, amount FROM silver_clean ORDER BY order_id;
