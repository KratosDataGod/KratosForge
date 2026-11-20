-- Labels the unknown product but forgets the store: a sale whose store isn't in dim_store shows blanks.
CREATE OR REPLACE TABLE gold_sales_wide AS
SELECT f.sale_id,
       d.date AS sale_date,
       strftime(d.date, '%Y-%m') AS month,
       coalesce(p.product_name, 'Unknown') AS product_name,
       coalesce(p.category, 'Unknown') AS category,
       s.store_name,
       s.region,
       f.quantity,
       f.amount
FROM fact_sales AS f
LEFT JOIN dim_date AS d ON d.date_key = f.date_key
LEFT JOIN dim_product AS p ON p.product_key = f.product_key
LEFT JOIN dim_store AS s ON s.store_key = f.store_key;

SELECT * FROM gold_sales_wide ORDER BY sale_id;
