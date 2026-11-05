-- Keeps every sale but leaves the unknown product blank.
CREATE OR REPLACE TABLE gold_sales_wide AS
SELECT f.sale_id, d.date AS sale_date, strftime(d.date, '%Y-%m') AS month, p.product_name, p.category,
       s.store_name, s.region, f.quantity, f.amount
FROM fact_sales AS f
LEFT JOIN dim_date AS d ON d.date_key = f.date_key
LEFT JOIN dim_product AS p ON p.product_key = f.product_key
LEFT JOIN dim_store AS s ON s.store_key = f.store_key;

SELECT * FROM gold_sales_wide ORDER BY sale_id;
