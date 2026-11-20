-- COALESCEs the bad amount to 0 instead of leaving the row out, so order 2 is kept as a free order.
CREATE OR REPLACE TABLE silver_orders AS
SELECT CAST(o.order_id AS INTEGER) AS order_id, o.customer_id, CAST(o.order_ts AS TIMESTAMP) AS order_ts,
       coalesce(TRY_CAST(o.amount AS DECIMAL(12,2)), 0) AS amount, lower(trim(o.status)) AS status, c.region
FROM (SELECT DISTINCT * FROM raw_orders) AS o
LEFT JOIN raw_customers AS c ON c.customer_id = o.customer_id;

SELECT * FROM silver_orders ORDER BY order_id;
