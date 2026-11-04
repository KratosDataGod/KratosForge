DELETE FROM target WHERE order_id IN (SELECT order_id FROM batch);
INSERT INTO target (order_id, amount) SELECT order_id, amount FROM batch;

SELECT order_id, amount FROM target ORDER BY order_id;
