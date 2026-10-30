INSERT INTO silver_clean SELECT * FROM src_orders_day3;
INSERT INTO silver_clean SELECT * FROM src_orders_day3;

SELECT order_id, status, amount FROM silver_clean ORDER BY order_id;
