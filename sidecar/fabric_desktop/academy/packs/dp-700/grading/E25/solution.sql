INSERT INTO dim_customer
SELECT (SELECT max(customer_key) FROM dim_customer) + row_number() OVER (ORDER BY customer_id),
       customer_id, 'Pending', true
FROM (SELECT DISTINCT customer_id FROM stg_sales AS s
      WHERE NOT EXISTS (SELECT 1 FROM dim_customer AS d WHERE d.customer_id = s.customer_id));

INSERT INTO fact_sales
SELECT s.sale_id, d.customer_key, s.amount
FROM stg_sales AS s JOIN dim_customer AS d USING (customer_id);

UPDATE dim_customer AS d
SET name = l.name, is_inferred = false
FROM stg_customers_late AS l
WHERE d.customer_id = l.customer_id AND d.is_inferred;

SELECT f.sale_id, f.customer_key, d.customer_id, d.name, d.is_inferred, f.amount
FROM fact_sales AS f JOIN dim_customer AS d USING (customer_key)
ORDER BY f.sale_id;
