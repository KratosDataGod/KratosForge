CREATE TABLE dim_customer AS
SELECT row_number() OVER (ORDER BY customer_code) AS customer_key, customer_code, name
FROM (SELECT DISTINCT customer_code, name FROM src_customers);

CREATE TABLE fact_sales AS
SELECT s.sale_id, d.customer_key, s.amount
FROM src_sales AS s
JOIN dim_customer AS d ON d.customer_code = s.customer_code;
