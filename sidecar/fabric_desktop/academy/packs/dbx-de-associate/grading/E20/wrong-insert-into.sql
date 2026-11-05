CREATE TABLE IF NOT EXISTS silver.orders (
  order_id BIGINT, customer_id BIGINT, order_date DATE, amount DECIMAL(10,2), status STRING);
INSERT INTO silver.orders
SELECT DISTINCT
  CAST(trim(order_id) AS BIGINT) AS order_id,
  CAST(trim(customer_id) AS BIGINT) AS customer_id,
  CASE WHEN order_ts LIKE '__/__/____' THEN to_date(order_ts, 'MM/dd/yyyy')
       ELSE to_date(order_ts, 'yyyy-MM-dd HH:mm:ss') END AS order_date,
  CAST(nullif(replace(trim(amount), ',', ''), '') AS DECIMAL(10,2)) AS amount,
  coalesce(nullif(lower(trim(status)), ''), 'unknown') AS status
FROM bronze.orders
WHERE order_id IS NOT NULL;
