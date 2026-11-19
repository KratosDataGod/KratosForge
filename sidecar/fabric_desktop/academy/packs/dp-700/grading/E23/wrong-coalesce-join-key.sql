-- Puts COALESCE on the join key instead of the looked-up name: 'unknown' isn't in ref_region, so the region stays NULL.
CREATE OR REPLACE TABLE dim_customer AS
SELECT 0 AS customer_key, -1 AS customer_id, 'Unknown' AS name, 'Unknown' AS region
UNION ALL
SELECT row_number() OVER (ORDER BY c.customer_id), c.customer_id, c.name, r.region_name
FROM (SELECT DISTINCT customer_id, name, region FROM stg_customers) AS c
LEFT JOIN ref_region AS r ON r.region_code = lower(coalesce(c.region, 'Unknown'));

SELECT customer_key, customer_id, name, region FROM dim_customer ORDER BY customer_key;
