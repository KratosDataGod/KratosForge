SELECT *
FROM orders AS o
JOIN stores AS s ON s.store_id = o.store_id;
