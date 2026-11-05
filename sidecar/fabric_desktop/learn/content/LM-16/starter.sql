SELECT customer, sum(amount) AS total
FROM orders
GROUP BY customer
HAVING sum(amount) > (SELECT avg(amount) FROM orders)
ORDER BY customer;
