CREATE TABLE gold_daily_sales (sold_on DATE, product_key INTEGER, revenue DECIMAL(12, 2));
INSERT INTO gold_daily_sales (sold_on, product_key, revenue)
SELECT s.sold_on, coalesce(d.product_key, -1), sum(s.amount)
FROM silver_sales AS s
LEFT JOIN dim_product AS d ON d.product_code = s.product_code
GROUP BY s.sold_on, coalesce(d.product_key, -1);

SELECT * FROM gold_daily_sales ORDER BY sold_on, product_key;
