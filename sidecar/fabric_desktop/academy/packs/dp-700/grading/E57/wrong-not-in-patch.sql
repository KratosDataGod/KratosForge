-- Papers over it with NOT IN; still filters on order_date, so 1006 never loads.
INSERT INTO silver_orders
SELECT order_id, order_date, amount, arrived_at
FROM source_orders
WHERE order_date >= (SELECT CAST(last_watermark AS DATE) FROM load_state)
  AND order_id NOT IN (SELECT order_id FROM silver_orders);

UPDATE load_state SET last_watermark = (SELECT CAST(max(order_date) AS TIMESTAMP) FROM silver_orders);

INSERT INTO silver_orders
SELECT order_id, order_date, amount, arrived_at
FROM source_orders
WHERE order_date >= (SELECT CAST(last_watermark AS DATE) FROM load_state)
  AND order_id NOT IN (SELECT order_id FROM silver_orders);

UPDATE load_state SET last_watermark = (SELECT CAST(max(order_date) AS TIMESTAMP) FROM silver_orders);

SELECT 'order' AS kind, CAST(order_id AS VARCHAR) AS id, arrived_at AS at FROM silver_orders
UNION ALL
SELECT 'watermark', NULL, last_watermark FROM load_state
ORDER BY kind, id;
