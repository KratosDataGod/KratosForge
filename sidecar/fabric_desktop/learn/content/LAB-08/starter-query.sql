SELECT customer_id, sum(amount - nvl(discount, 0)) AS net
FROM spark_catalog.default.orders
GROUP BY customer_id
ORDER BY customer_id;
