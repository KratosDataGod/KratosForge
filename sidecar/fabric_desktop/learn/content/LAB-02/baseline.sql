SELECT s.store_name,
       SUM(o.amount) AS revenue_2026
FROM orders AS o
JOIN stores AS s ON s.store_id = o.store_id
WHERE CAST(o.order_date AS VARCHAR) LIKE '2026%'
  AND o.status IN (SELECT DISTINCT lower(status) FROM orders
                   WHERE status <> 'cancelled' AND status <> 'returned')
GROUP BY s.store_name
ORDER BY revenue_2026 DESC;
