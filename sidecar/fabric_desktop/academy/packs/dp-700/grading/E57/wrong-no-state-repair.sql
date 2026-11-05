-- Code repaired, state not: the old watermark reloads 1003 and 1004.
-- run 1
INSERT INTO silver_orders
SELECT order_id, order_date, amount, arrived_at
FROM source_orders
WHERE arrived_at > (SELECT last_watermark FROM load_state);

UPDATE load_state SET last_watermark = (SELECT max(arrived_at) FROM silver_orders);

-- run 2
INSERT INTO silver_orders
SELECT order_id, order_date, amount, arrived_at
FROM source_orders
WHERE arrived_at > (SELECT last_watermark FROM load_state);

UPDATE load_state SET last_watermark = (SELECT max(arrived_at) FROM silver_orders);

SELECT 'order' AS kind, CAST(order_id AS VARCHAR) AS id, arrived_at AS at FROM silver_orders
UNION ALL
SELECT 'watermark', NULL, last_watermark FROM load_state
ORDER BY kind, id;
