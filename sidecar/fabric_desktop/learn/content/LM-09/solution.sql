CREATE TABLE dim_date AS
SELECT CAST(strftime(order_date, '%Y%m%d') AS INTEGER) AS date_key,
       order_date AS date,
       CAST(year(order_date) AS INTEGER) AS year,
       CAST(month(order_date) AS INTEGER) AS month
FROM (SELECT DISTINCT order_date FROM orders);

CREATE TABLE fact_orders AS
SELECT order_id, CAST(strftime(order_date, '%Y%m%d') AS INTEGER) AS date_key, customer_id, amount
FROM orders;
