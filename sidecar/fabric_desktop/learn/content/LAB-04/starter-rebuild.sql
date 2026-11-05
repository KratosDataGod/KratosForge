CREATE OR REPLACE TABLE silver_orders AS
SELECT DISTINCT * FROM silver_orders;

SELECT order_id, status, amount FROM silver_orders ORDER BY order_id;
