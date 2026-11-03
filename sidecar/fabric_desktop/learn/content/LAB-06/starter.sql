CREATE TABLE silver_orders AS SELECT * FROM bronze_orders;

SELECT order_id, order_date, amount FROM silver_orders ORDER BY order_id;
