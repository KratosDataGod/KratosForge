CREATE OR REPLACE TABLE silver_orders AS
SELECT CAST(order_id AS INTEGER) AS order_id,
       CAST(order_date AS DATE) AS order_date,
       CAST(amount AS DECIMAL(12, 2)) AS amount
FROM (SELECT *, row_number() OVER (PARTITION BY order_id ORDER BY ingested_at DESC) AS n
      FROM bronze_orders
      WHERE TRY_CAST(order_date AS DATE) IS NOT NULL)
WHERE n = 1;

SELECT order_id, order_date, amount FROM silver_orders ORDER BY order_id;
