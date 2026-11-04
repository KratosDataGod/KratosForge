-- E25: one query over sales.order_events. Every change to an order is a new row, so first keep each
-- order's latest version, then leave out orders whose latest status is 'cancelled'.
--
-- Return, one row per remaining order, ordered by customer_id, order_date, order_id:
--   customer_id, order_id, order_date, amount,
--   running_total     the customer's total so far, in order_date then order_id order
--   previous_amount   the amount of the customer's previous order (NULL for their first)
--   amount_rank       1 for the customer's largest amount; equal amounts share a rank, with no gaps

SELECT *
FROM sales.order_events
ORDER BY order_id, updated_at;
