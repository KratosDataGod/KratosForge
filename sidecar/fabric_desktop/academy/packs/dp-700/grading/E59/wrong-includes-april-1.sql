-- <= 1 April adds a day.
SELECT s.region, sum(o.amount) AS revenue, count(*) AS orders
FROM sales_by_day AS o
JOIN stores AS s USING (store_id)
WHERE o.order_date BETWEEN DATE '2026-03-01' AND DATE '2026-04-01'
  AND o.status = 'complete'
GROUP BY s.region
ORDER BY s.region;
