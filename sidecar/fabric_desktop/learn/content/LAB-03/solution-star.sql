CREATE TABLE dim_customer AS
SELECT row_number() OVER (ORDER BY customer_id) AS customer_key, customer_id, name, region
FROM (SELECT DISTINCT customer_id, name, region FROM customers);

CREATE TABLE dim_product AS
SELECT row_number() OVER (ORDER BY product_id) AS product_key, product_id, product_name, category
FROM products;

CREATE TABLE fact_sales AS
SELECT o.order_id, c.customer_key, p.product_key,
       CAST(strftime(o.order_date, '%Y%m%d') AS INTEGER) AS date_key,
       o.quantity, o.amount
FROM orders AS o
JOIN dim_customer AS c ON c.customer_id = o.customer_id
JOIN dim_product AS p ON p.product_id = o.product_id
WHERE o.status = 'complete';
