SELECT customer_id, sum(amount - coalesce(discount, 0)) AS net
FROM silver.orders
GROUP BY customer_id
ORDER BY customer_id;
