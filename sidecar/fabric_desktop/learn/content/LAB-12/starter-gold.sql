SELECT c.region, sum(o.amount) AS revenue
FROM raw_orders AS o
JOIN customers AS c ON c.customer_id = o.customer_id
GROUP BY c.region
ORDER BY c.region;
