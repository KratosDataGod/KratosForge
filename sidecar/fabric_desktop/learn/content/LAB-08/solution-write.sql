DELETE FROM silver.customer_net;
INSERT INTO silver.customer_net (customer_id, net)
SELECT customer_id, sum(amount - coalesce(discount, 0))
FROM silver.orders
GROUP BY customer_id;

SELECT customer_id, net FROM silver.customer_net ORDER BY customer_id;
