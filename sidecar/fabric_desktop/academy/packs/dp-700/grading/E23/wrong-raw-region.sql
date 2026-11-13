-- Keeps the regions as typed: 'north', 'EAST' and a NULL.
CREATE OR REPLACE TABLE dim_customer AS
SELECT 0 AS customer_key, -1 AS customer_id, 'Unknown' AS name, 'Unknown' AS region
UNION ALL
SELECT row_number() OVER (ORDER BY customer_id), customer_id, name, region
FROM (SELECT DISTINCT customer_id, name, region FROM stg_customers);

SELECT customer_key, customer_id, name, region FROM dim_customer ORDER BY customer_key;
