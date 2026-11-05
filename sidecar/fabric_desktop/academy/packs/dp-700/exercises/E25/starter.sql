-- 1. Make sure every customer_id in stg_sales has a row in dim_customer
--    (new keys after max(customer_key) in customer_id order, name 'Pending', is_inferred true).
-- 2. Load stg_sales into fact_sales with each sale's customer_key.
-- 3. Apply stg_customers_late to dim_customer.


SELECT f.sale_id, f.customer_key, d.customer_id, d.name, d.is_inferred, f.amount
FROM fact_sales AS f JOIN dim_customer AS d USING (customer_key)
ORDER BY f.sale_id;
