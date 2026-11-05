-- Build gold_sales_wide(sale_id, sale_date, month 'YYYY-MM', product_name, category, store_name, region,
-- quantity, amount): one row per sale; a product or store that isn't in its dimension shows 'Unknown'.
CREATE OR REPLACE TABLE gold_sales_wide AS
SELECT ...;

SELECT * FROM gold_sales_wide ORDER BY sale_id;
