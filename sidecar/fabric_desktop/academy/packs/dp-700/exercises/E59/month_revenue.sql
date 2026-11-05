-- Month-end revenue by region for March 2026. It used to be instant; now it reads every row.
-- Make it at least twice as fast without changing its answer.
SELECT s.region, sum(o.amount) AS revenue, count(*) AS orders
FROM sales_by_day AS o
JOIN stores AS s USING (store_id)
WHERE year(o.order_date) = 2026 AND month(o.order_date) = 3
  AND o.status = 'complete'
GROUP BY s.region
ORDER BY s.region;
