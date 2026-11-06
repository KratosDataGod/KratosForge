-- Monthly revenue by region: what was ordered (line_total) and what was paid (paid_total).
-- Finance says both totals went up a lot when payments were added, and the query got slower.
-- Run EXPLAIN ANALYZE on it and look at how many rows each join returns.
SELECT o.region,
       date_trunc('month', o.order_date) AS order_month,
       count(DISTINCT o.order_id) AS orders,
       sum(DISTINCT l.amount) AS line_total,
       sum(DISTINCT p.amount) AS paid_total
FROM orders AS o
JOIN order_lines AS l ON l.order_id = o.order_id
LEFT JOIN payments AS p ON p.order_id = o.order_id
GROUP BY ALL
ORDER BY region, order_month;
