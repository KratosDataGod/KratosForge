INSERT INTO target SELECT * FROM batch;

SELECT order_id, amount FROM target ORDER BY order_id;
