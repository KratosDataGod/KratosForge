-- Drops every row with any missing value.
CREATE OR REPLACE TABLE silver_orders AS
SELECT s.order_id, d.customer_key, s.qty, s.amount, s.channel
FROM stg_orders AS s JOIN dim_customer AS d ON d.customer_id = s.customer_id
WHERE s.qty IS NOT NULL AND s.amount IS NOT NULL AND s.channel IS NOT NULL;

CREATE OR REPLACE TABLE quarantine_orders (order_id INTEGER, reason VARCHAR);

SELECT order_id, customer_key, qty, amount, channel, NULL AS reason FROM silver_orders
UNION ALL
SELECT order_id, NULL, NULL, NULL, NULL, reason FROM quarantine_orders
ORDER BY order_id;
