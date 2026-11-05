CREATE TABLE customer_summary AS
SELECT customer_id, count(*) AS orders, sum(amount) AS revenue
FROM orders
GROUP BY customer_id;

SELECT * FROM customer_summary ORDER BY customer_id;
