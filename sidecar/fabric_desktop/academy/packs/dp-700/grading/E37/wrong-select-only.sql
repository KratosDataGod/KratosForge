-- Deduplicates only in the final query and never rebuilds silver_orders, so the table keeps its copies.
SELECT order_id, status, amount
FROM silver_orders
QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY loaded_at DESC) = 1
ORDER BY order_id;
