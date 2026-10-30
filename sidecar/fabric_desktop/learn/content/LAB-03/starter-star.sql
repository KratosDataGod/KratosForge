CREATE TABLE dim_customer AS
SELECT row_number() OVER (ORDER BY customer_id) AS customer_key, customer_id, name, region
FROM customers;

CREATE TABLE dim_product AS
SELECT row_number() OVER (ORDER BY product_id) AS product_key, product_id, product_name, category
FROM products;

CREATE TABLE fact_sales AS
SELECT order_id, customer_id, product_id, order_date, quantity, amount
FROM orders;
