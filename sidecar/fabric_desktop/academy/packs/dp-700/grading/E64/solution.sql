WITH lines AS (
    SELECT order_id, sum(amount) AS line_total FROM order_lines GROUP BY order_id
), paid AS (
    SELECT order_id, sum(amount) AS paid_total FROM payments GROUP BY order_id
)
SELECT o.region,
       date_trunc('month', o.order_date) AS order_month,
       count(*) AS orders,
       sum(l.line_total) AS line_total,
       coalesce(sum(p.paid_total), 0) AS paid_total
FROM orders AS o
JOIN lines AS l ON l.order_id = o.order_id
LEFT JOIN paid AS p ON p.order_id = o.order_id
GROUP BY ALL
ORDER BY region, order_month;
