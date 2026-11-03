-- One row per problem: the table it's in, and the row's key.
SELECT 'orders' AS table_name, order_id AS key
FROM orders
WHERE amount < 0;
