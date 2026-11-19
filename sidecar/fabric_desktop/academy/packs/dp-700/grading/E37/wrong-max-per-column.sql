-- Takes max() of every column per order: order 3 gets max('open', 'complete') = 'open', not its latest status.
CREATE OR REPLACE TABLE silver_orders AS
SELECT order_id, max(status) AS status, max(amount) AS amount, max(loaded_at) AS loaded_at
FROM silver_orders
GROUP BY order_id;

SELECT order_id, status, amount FROM silver_orders ORDER BY order_id;
