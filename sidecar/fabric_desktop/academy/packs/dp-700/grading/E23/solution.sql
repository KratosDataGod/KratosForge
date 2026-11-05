CREATE OR REPLACE TABLE dim_customer AS
SELECT 0 AS customer_key, -1 AS customer_id, 'Unknown' AS name, 'Unknown' AS region
UNION ALL
SELECT row_number() OVER (ORDER BY c.customer_id), c.customer_id, c.name, coalesce(r.region_name, 'Unknown')
FROM (SELECT DISTINCT customer_id, name, region FROM stg_customers) AS c
LEFT JOIN ref_region AS r ON r.region_code = lower(c.region);

SELECT customer_key, customer_id, name, region FROM dim_customer ORDER BY customer_key;
