-- Loads the right rows but never moves the watermark, so tomorrow's load reads them all again.
MERGE INTO silver_orders AS t
USING (
    SELECT * FROM src_orders
    WHERE modified_at > (SELECT high_water FROM etl_watermark WHERE table_name = 'orders')
) AS s
ON t.order_id = s.order_id
WHEN MATCHED THEN UPDATE SET status = s.status, amount = s.amount, modified_at = s.modified_at
WHEN NOT MATCHED THEN INSERT (order_id, status, amount, modified_at) VALUES (s.order_id, s.status, s.amount, s.modified_at);

SELECT o.order_id, o.status, o.amount, w.high_water
FROM silver_orders AS o, etl_watermark AS w
WHERE w.table_name = 'orders'
ORDER BY o.order_id;
