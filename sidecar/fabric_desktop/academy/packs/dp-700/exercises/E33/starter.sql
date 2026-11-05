-- Build silver_orders(order_id INTEGER, customer_id, order_ts TIMESTAMP, amount DECIMAL(12,2), status, region):
--   one row per order (order 4 was sent twice), status trimmed and lower case,
--   rows whose amount isn't a number left out, region from raw_customers.
CREATE OR REPLACE TABLE silver_orders AS
SELECT ...;

SELECT * FROM silver_orders ORDER BY order_id;
