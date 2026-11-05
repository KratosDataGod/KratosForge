CREATE OR REPLACE TABLE silver_orders AS
SELECT CAST(o.order_id AS INTEGER) AS order_id,
       o.customer_id,
       CAST(o.order_ts AS TIMESTAMP) AS order_ts,
       TRY_CAST(o.amount AS DECIMAL(12,2)) AS amount,
       lower(trim(o.status)) AS status,
       c.region
FROM (SELECT DISTINCT * FROM raw_orders) AS o
LEFT JOIN raw_customers AS c ON c.customer_id = o.customer_id
WHERE TRY_CAST(o.amount AS DECIMAL(12,2)) IS NOT NULL;

SELECT * FROM silver_orders ORDER BY order_id;
