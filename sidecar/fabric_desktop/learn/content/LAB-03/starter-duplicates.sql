SELECT customer_id, count(*) AS copies
FROM customers
GROUP BY customer_id
ORDER BY customer_id;
