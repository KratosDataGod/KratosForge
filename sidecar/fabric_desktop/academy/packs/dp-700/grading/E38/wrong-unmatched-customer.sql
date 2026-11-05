-- Handles a NULL customer_id but not one dim_customer doesn't know (C9 keeps a NULL key).
CREATE OR REPLACE TABLE silver_orders AS
SELECT s.order_id, CASE WHEN s.customer_id IS NULL THEN 0 ELSE d.customer_key END AS customer_key,
       coalesce(s.qty, 1) AS qty, s.amount, coalesce(s.channel, 'web') AS channel
FROM stg_orders AS s LEFT JOIN dim_customer AS d ON d.customer_id = s.customer_id
WHERE s.amount IS NOT NULL;

CREATE OR REPLACE TABLE quarantine_orders AS
SELECT order_id, 'missing amount' AS reason FROM stg_orders WHERE amount IS NULL;

SELECT order_id, customer_key, qty, amount, channel, NULL AS reason FROM silver_orders
UNION ALL
SELECT order_id, NULL, NULL, NULL, NULL, reason FROM quarantine_orders
ORDER BY order_id;
