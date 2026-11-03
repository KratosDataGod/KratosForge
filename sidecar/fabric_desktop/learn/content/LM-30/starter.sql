SELECT column_name, 'missing' AS problem
FROM contract
WHERE column_name NOT IN (SELECT column_name FROM information_schema.columns WHERE table_name = 'delivered_orders')
ORDER BY column_name;
