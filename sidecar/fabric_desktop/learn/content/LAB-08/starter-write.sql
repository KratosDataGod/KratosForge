INSERT OVERWRITE TABLE silver.customer_net
SELECT customer_id, sum(amount - nvl(discount, 0)) AS net
FROM spark_catalog.default.orders
GROUP BY customer_id;

SELECT customer_id, net FROM silver.customer_net ORDER BY customer_id;
