-- Nightly incremental load of orders. It reports success, but order 1006 never arrives.
-- Repair it. Then leave the job in this file twice, as if the scheduler ran it twice.

-- run 1
INSERT INTO silver_orders
SELECT order_id, order_date, amount, arrived_at
FROM source_orders
WHERE order_date > (SELECT CAST(last_watermark AS DATE) FROM load_state);

UPDATE load_state SET last_watermark = (SELECT CAST(max(order_date) AS TIMESTAMP) FROM silver_orders);

-- run 2: paste the repaired job again here (state repairs, if any, belong before run 1)


SELECT 'order' AS kind, CAST(order_id AS VARCHAR) AS id, arrived_at AS at FROM silver_orders
UNION ALL
SELECT 'watermark', NULL, last_watermark FROM load_state
ORDER BY kind, id;
