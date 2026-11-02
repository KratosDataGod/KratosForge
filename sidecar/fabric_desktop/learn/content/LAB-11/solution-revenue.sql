SELECT sold_on, sum(amount) AS revenue
FROM (SELECT DISTINCT sale_id, sold_on, amount FROM sales)
GROUP BY sold_on
ORDER BY sold_on;
