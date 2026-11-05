SELECT 'customers' AS table_name, customer_id AS key
FROM customers GROUP BY customer_id HAVING count(*) > 1
UNION ALL
SELECT 'customers', customer_id FROM customers WHERE email IS NULL
UNION ALL
SELECT 'customers', customer_id FROM customers WHERE region NOT IN ('North', 'South', 'East', 'West')
UNION ALL
SELECT 'orders', order_id FROM orders WHERE amount < 0
UNION ALL
SELECT 'orders', order_id FROM orders WHERE order_date > current_date
UNION ALL
SELECT 'orders', o.order_id FROM orders AS o
WHERE NOT EXISTS (SELECT 1 FROM stores AS s WHERE s.store_id = o.store_id);
