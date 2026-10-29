SELECT region, COUNT(*) AS sales, SUM(amount) AS total
FROM sales
GROUP BY region
HAVING SUM(amount) > 500
ORDER BY total DESC;
