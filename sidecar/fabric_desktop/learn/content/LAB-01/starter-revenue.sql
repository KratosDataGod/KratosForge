SELECT customer_id, count(*) AS orders, SUM(amount) AS revenue
FROM orders
GROUP BY customer_id
ORDER BY revenue DESC
LIMIT 10;
