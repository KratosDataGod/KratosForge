SELECT s.name, sum(x.amount) AS revenue
FROM stores AS s
JOIN sales AS x ON x.store_id = s.store_id
GROUP BY s.name
ORDER BY s.name;
