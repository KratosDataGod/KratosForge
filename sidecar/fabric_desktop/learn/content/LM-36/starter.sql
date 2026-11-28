-- This fact carries the order number and status (degenerate and descriptive fields)
-- and joins the customer on the current version only.
SELECT o.order_sk, c.customer_sk, o.order_id, o.status, o.amount, o.net_amount
FROM silver_orders o
JOIN dim_customer c ON c.customer_dk = o.customer_sk AND c.is_current
ORDER BY o.order_sk;
