MERGE INTO silver.customers AS t
USING (
  SELECT customer_id, op, name, city, change_ts
  FROM bronze.customer_changes
  QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY change_ts) = 1
) AS s
ON t.customer_id = s.customer_id
WHEN MATCHED AND s.op = 'D' THEN DELETE
WHEN MATCHED THEN UPDATE SET name = s.name, city = s.city, updated_at = s.change_ts
WHEN NOT MATCHED AND s.op <> 'D' THEN
  INSERT (customer_id, name, city, updated_at) VALUES (s.customer_id, s.name, s.city, s.change_ts);
