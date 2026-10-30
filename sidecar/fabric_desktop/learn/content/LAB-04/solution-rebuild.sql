CREATE OR REPLACE TABLE silver_orders AS
SELECT order_id, status, amount, loaded_at
FROM (
    SELECT *, row_number() OVER (PARTITION BY order_id ORDER BY loaded_at DESC) AS load_rank
    FROM silver_orders
)
WHERE load_rank = 1;

SELECT order_id, status, amount FROM silver_orders ORDER BY order_id;
