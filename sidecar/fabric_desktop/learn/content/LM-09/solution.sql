CREATE TABLE dim_date AS
SELECT CAST(year(order_date) * 10000 + month(order_date) * 100 + day(order_date) AS INTEGER) AS date_key,
       order_date AS date,
       CAST(year(order_date) AS INTEGER) AS year,
       CAST(month(order_date) AS INTEGER) AS month
FROM (SELECT DISTINCT order_date FROM orders) AS dates;

CREATE TABLE fact_orders AS
SELECT order_id, CAST(year(order_date) * 10000 + month(order_date) * 100 + day(order_date) AS INTEGER) AS date_key,
       customer_id, CAST(amount AS DECIMAL(12,2)) AS amount
FROM orders;
