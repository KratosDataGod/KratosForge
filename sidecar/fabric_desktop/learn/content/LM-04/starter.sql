SELECT DISTINCT region, COUNT(*) AS sales, SUM(amount) AS total
FROM sales
GROUP BY region;
