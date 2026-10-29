SELECT DISTINCT region, count(*) AS sales, SUM(amount) AS total
FROM sales
GROUP BY region;
