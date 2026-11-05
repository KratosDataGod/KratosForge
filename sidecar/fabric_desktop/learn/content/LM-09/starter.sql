CREATE TABLE dim_date AS
SELECT order_date AS date, year(order_date) AS year, month(order_date) AS month
FROM orders;

CREATE TABLE fact_orders AS
SELECT order_id, order_date, customer_id, amount
FROM orders;
