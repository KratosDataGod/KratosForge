SELECT s.name, sum(x.amount) AS revenue
FROM stores AS s, sales AS x
GROUP BY s.name
ORDER BY s.name;
