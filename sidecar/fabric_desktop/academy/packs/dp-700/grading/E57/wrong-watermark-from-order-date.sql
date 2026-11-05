-- Filter repaired, watermark still from order_date: run 2 loads 1005 and 1006 again.
-- repair the state written by the broken job
UPDATE load_state SET last_watermark = (SELECT max(arrived_at) FROM silver_orders);

-- run 1
INSERT INTO silver_orders
SELECT order_id, order_date, amount, arrived_at
FROM source_orders
WHERE arrived_at > (SELECT last_watermark FROM load_state);

UPDATE load_state SET last_watermark = (SELECT CAST(max(order_date) AS TIMESTAMP) FROM silver_orders);

INSERT INTO silver_orders
SELECT order_id, order_date, amount, arrived_at
FROM source_orders
WHERE arrived_at > (SELECT last_watermark FROM load_state);

UPDATE load_state SET last_watermark = (SELECT CAST(max(order_date) AS TIMESTAMP) FROM silver_orders);

SELECT 'order' AS kind, CAST(order_id AS VARCHAR) AS id, arrived_at AS at FROM silver_orders
UNION ALL
SELECT 'watermark', NULL, last_watermark FROM load_state
ORDER BY kind, id;
