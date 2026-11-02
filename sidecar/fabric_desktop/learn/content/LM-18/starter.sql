SELECT s.name, sum(x.amount) AS revenue
FROM stores AS s CROSS JOIN sales AS x
GROUP BY s.name
ORDER BY s.name;
