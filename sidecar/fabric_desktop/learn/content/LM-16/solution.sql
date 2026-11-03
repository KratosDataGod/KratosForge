WITH spend AS (
    SELECT customer, sum(amount) AS total
    FROM orders
    WHERE order_date >= DATE '2026-01-01' AND order_date < DATE '2027-01-01'
    GROUP BY customer
)
SELECT customer, total
FROM spend
WHERE total > (SELECT avg(total) FROM spend)
ORDER BY customer;
