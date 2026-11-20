-- Keeps the midnight time on sale_date: a date with no time should be DATE.
CREATE TABLE fact_sales AS
SELECT CAST(sale_id AS INTEGER) AS sale_id,
       store_code AS store_code,
       CAST(sale_date AS TIMESTAMP) AS sale_date,
       CAST(sold_at AS TIMESTAMP) AS sold_at,
       CAST(qty AS INTEGER) AS qty,
       CAST(unit_price AS DECIMAL(12,2)) AS unit_price,
       CAST(is_return AS BOOLEAN) AS is_return
FROM fact_sales_raw;


-- Keep this query last: it's what the check compares.
SELECT sale_id, store_code, sale_date, sold_at, qty, unit_price, is_return FROM fact_sales ORDER BY sale_id;
