-- DISTINCT removes exact copies, but order 3 keeps its old open row beside the complete one.
CREATE OR REPLACE TABLE silver_orders AS
SELECT DISTINCT * EXCLUDE (loaded_at), max(loaded_at) OVER (PARTITION BY order_id, status) AS loaded_at FROM silver_orders;

SELECT DISTINCT order_id, status, amount FROM silver_orders ORDER BY order_id;
