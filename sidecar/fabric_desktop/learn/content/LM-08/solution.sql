SELECT s.region, count(*) AS orders, sum(o.amount) AS revenue
FROM orders AS o
JOIN stores AS s ON s.store_id = o.store_id
WHERE o.status = 'complete'
GROUP BY s.region
ORDER BY revenue DESC;
