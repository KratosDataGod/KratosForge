-- Same answer, still a function around the column.
SELECT s.region, sum(o.amount) AS revenue, count(*) AS orders
FROM sales_by_day AS o
JOIN stores AS s USING (store_id)
WHERE year(o.order_date) * 100 + month(o.order_date) = 202603
  AND o.status = 'complete'
GROUP BY s.region
ORDER BY s.region;
