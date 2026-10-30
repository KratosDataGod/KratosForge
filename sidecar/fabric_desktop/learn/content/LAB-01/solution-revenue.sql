SELECT o.customer_id, count(*) AS orders, SUM(o.amount) AS revenue
FROM orders AS o
WHERE o.status = 'complete'
  AND o.amount >= 0
  AND o.order_date <= current_date
  AND EXISTS (SELECT 1 FROM stores AS s WHERE s.store_id = o.store_id)
GROUP BY o.customer_id
ORDER BY revenue DESC, o.customer_id
LIMIT 10;
