SELECT customer_id, count(*) AS copies
FROM customers
GROUP BY customer_id
HAVING count(*) > 1
ORDER BY customer_id;
