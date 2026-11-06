-- Defaults the amount to 0 instead of quarantining it.
CREATE OR REPLACE TABLE silver_orders AS
SELECT s.order_id, coalesce(d.customer_key, 0) AS customer_key, coalesce(s.qty, 1) AS qty,
       coalesce(s.amount, 0) AS amount, coalesce(s.channel, 'web') AS channel
FROM stg_orders AS s LEFT JOIN dim_customer AS d ON d.customer_id = s.customer_id;

CREATE OR REPLACE TABLE quarantine_orders (order_id INTEGER, reason VARCHAR);

SELECT order_id, customer_key, qty, amount, channel, NULL AS reason FROM silver_orders
UNION ALL
SELECT order_id, NULL, NULL, NULL, NULL, reason FROM quarantine_orders
ORDER BY order_id;
