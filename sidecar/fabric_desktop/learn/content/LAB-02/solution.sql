SELECT s.store_name,
       SUM(o.amount) AS revenue_2026
FROM orders AS o
JOIN stores AS s ON s.store_id = o.store_id
WHERE o.order_date >= DATE '2026-01-01'
  AND o.order_date <  DATE '2027-01-01'
  AND o.status = 'complete'
GROUP BY s.store_name
ORDER BY revenue_2026 DESC;
