SELECT order_id, amount
FROM table_versions
WHERE version = (SELECT max(version) FROM table_versions)
ORDER BY order_id;
