INSERT INTO silver.customers
SELECT customer_id, name, city, change_ts
FROM bronze.customer_changes
WHERE op <> 'D'
QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY change_ts DESC) = 1;
