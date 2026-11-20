-- Same answer, but date_trunc() still wraps the column, so no block can be skipped.
SELECT s.region, sum(o.amount) AS revenue, count(*) AS orders
FROM sales_by_day AS o
JOIN stores AS s USING (store_id)
WHERE date_trunc('month', o.order_date) = DATE '2026-03-01'
  AND o.status = 'complete'
GROUP BY s.region
ORDER BY s.region;
