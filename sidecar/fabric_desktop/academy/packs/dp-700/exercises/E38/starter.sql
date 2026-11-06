-- The agreed rules:
--   missing channel  -> 'web'
--   missing qty      -> 1
--   missing or unknown customer -> customer_key 0 (the Unknown member)
--   missing amount   -> the row goes to quarantine_orders(order_id, reason) with reason 'missing amount'
-- Build silver_orders(order_id, customer_key, qty, amount, channel) and quarantine_orders(order_id, reason).


SELECT order_id, customer_key, qty, amount, channel, NULL AS reason FROM silver_orders
UNION ALL
SELECT order_id, NULL, NULL, NULL, NULL, reason FROM quarantine_orders
ORDER BY order_id;
