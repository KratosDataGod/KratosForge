CREATE TABLE dim_customer AS
SELECT row_number() OVER () AS customer_key, customer_code, name
FROM src_customers;

CREATE TABLE fact_sales AS
SELECT sale_id, customer_code, amount
FROM src_sales;
