-- 1. Load into silver_orders only the source rows that changed since the watermark: new orders are
--    added, changed orders are updated, nothing is doubled.
-- 2. Move the watermark to the last change you loaded.
-- 3. Leave this query at the end so the check can read the result.

SELECT o.order_id, o.status, o.amount, w.high_water
FROM silver_orders AS o, etl_watermark AS w
WHERE w.table_name = 'orders'
ORDER BY o.order_id;
