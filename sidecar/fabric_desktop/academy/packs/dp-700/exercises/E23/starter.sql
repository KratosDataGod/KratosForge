-- Build dim_customer(customer_key, customer_id, name, region):
--   one row per customer_id, keys 1, 2, 3... in customer_id order,
--   region from ref_region ('Unknown' when there is none),
--   and an Unknown member: customer_key 0, customer_id -1, name 'Unknown', region 'Unknown'.
CREATE OR REPLACE TABLE dim_customer AS
SELECT ...;

SELECT customer_key, customer_id, name, region FROM dim_customer ORDER BY customer_key;
