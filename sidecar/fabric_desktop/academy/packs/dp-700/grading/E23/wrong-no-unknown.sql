-- No Unknown member, and the inner join drops the customer with no region.
CREATE OR REPLACE TABLE dim_customer AS
SELECT row_number() OVER (ORDER BY c.customer_id) AS customer_key, c.customer_id, c.name, r.region_name AS region
FROM (SELECT DISTINCT customer_id, name, region FROM stg_customers) AS c
JOIN ref_region AS r ON r.region_code = lower(c.region);

SELECT customer_key, customer_id, name, region FROM dim_customer ORDER BY customer_key;
