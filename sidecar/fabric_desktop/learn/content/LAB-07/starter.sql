CREATE VIEW gold_daily_sales AS
SELECT s.sold_on, d.product_key, sum(s.amount) AS revenue
FROM silver_sales AS s
JOIN dim_product AS d ON d.product_code = s.product_code
GROUP BY s.sold_on, d.product_key;

SELECT * FROM gold_daily_sales ORDER BY sold_on, product_key;
