SELECT c.column_name,
       CASE WHEN i.column_name IS NULL THEN 'missing' ELSE 'wrong type' END AS problem
FROM contract AS c
LEFT JOIN information_schema.columns AS i
       ON i.table_name = 'delivered_orders' AND i.column_name = c.column_name
WHERE i.column_name IS NULL OR i.data_type <> c.data_type
ORDER BY c.column_name;
