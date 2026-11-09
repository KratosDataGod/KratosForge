-- C1 practice data: a made-up shop, three days of files waiting in landing. DuckDB SQL; fixed values.
-- Nothing has landed yet: bronze, silver and the load log start empty.
CREATE SCHEMA landing;
CREATE SCHEMA bronze;
CREATE SCHEMA silver;
CREATE SCHEMA ops;
CREATE TABLE landing.orders AS SELECT * FROM (VALUES
  (1001, 1, 'P1', 2, 5.00::DECIMAL(10,2), TIMESTAMP '2026-10-01 09:10:00', '2026-10-01'),
  (1002, 2, 'P2', 1, 12.50::DECIMAL(10,2), TIMESTAMP '2026-10-01 10:00:00', '2026-10-01'),
  (1002, 2, 'P2', 1, 12.50::DECIMAL(10,2), TIMESTAMP '2026-10-01 10:00:00', '2026-10-01'),
  (1003, NULL, 'P1', 1, 5.00::DECIMAL(10,2), TIMESTAMP '2026-10-01 10:40:00', '2026-10-01'),
  (1004, 7, 'P3', 3, 2.00::DECIMAL(10,2), TIMESTAMP '2026-10-01 11:30:00', '2026-10-01'),
  (1005, 1, 'P3', 0, 2.00::DECIMAL(10,2), TIMESTAMP '2026-10-02 12:00:00', '2026-10-02'),
  (1006, 3, 'P2', 2, 12.50::DECIMAL(10,2), TIMESTAMP '2026-10-02 14:00:00', '2026-10-02'),
  (1002, 2, 'P2', 2, 12.50::DECIMAL(10,2), TIMESTAMP '2026-10-01 10:00:00', '2026-10-02'),
  (1007, 2, 'P1', 4, 5.00::DECIMAL(10,2), TIMESTAMP '2026-10-02 16:00:00', '2026-10-02'),
  (1008, 1, 'P1', 1, 5.00::DECIMAL(10,2), TIMESTAMP '2026-10-03 08:00:00', '2026-10-03'),
  (1009, 3, 'P3', 5, 2.00::DECIMAL(10,2), TIMESTAMP '2026-10-03 09:30:00', '2026-10-03'),
  (1005, 1, 'P3', 1, 2.00::DECIMAL(10,2), TIMESTAMP '2026-10-02 12:00:00', '2026-10-03'),
  (1010, 2, 'P2', 1, 12.50::DECIMAL(10,2), TIMESTAMP '2026-10-01 22:00:00', '2026-10-03'),
  (1011, 8, 'P4', 2, 3.25::DECIMAL(10,2), TIMESTAMP '2026-10-03 10:15:00', '2026-10-03')
) AS t(order_id, customer_id, product_id, qty, unit_price, order_ts, load_date);
CREATE TABLE landing.customers AS SELECT * FROM (VALUES
  (1, 'Ada Shop', 'Leeds', 'Retail', TIMESTAMP '2026-09-01 00:00:00', '2026-10-01'),
  (2, 'Bo Stores', 'York', 'Retail', TIMESTAMP '2026-09-01 00:00:00', '2026-10-01'),
  (3, 'Cy Trading', 'Hull', 'Wholesale', TIMESTAMP '2026-09-01 00:00:00', '2026-10-01'),
  (2, 'Bo Stores', 'Bath', 'Retail', TIMESTAMP '2026-10-02 12:00:00', '2026-10-02'),
  (1, 'Ada Shop', 'Leeds', 'Retail', TIMESTAMP '2026-10-02 00:00:00', '2026-10-02'),
  (3, 'Cy Trading', 'Hull', 'Retail', TIMESTAMP '2026-10-03 09:00:00', '2026-10-03'),
  (7, 'Gil Market', 'Leeds', 'Wholesale', TIMESTAMP '2026-09-15 00:00:00', '2026-10-03'),
  (1, 'Ada Shop Ltd', 'Leeds', 'Retail', TIMESTAMP '2026-10-03 07:00:00', '2026-10-03'),
  (2, 'Bo Stores', 'Ripon', 'Retail', TIMESTAMP '2026-10-01 20:00:00', '2026-10-03')
) AS t(customer_id, name, city, segment, changed_at, load_date);
CREATE TABLE bronze.orders (order_id INTEGER, customer_id INTEGER, product_id VARCHAR, qty INTEGER, unit_price DECIMAL(10,2), order_ts TIMESTAMP, load_date VARCHAR);
CREATE TABLE bronze.customers (customer_id INTEGER, name VARCHAR, city VARCHAR, segment VARCHAR, changed_at TIMESTAMP, load_date VARCHAR);
CREATE TABLE bronze.products AS SELECT * FROM (VALUES ('P1', 'Oat bar', 'Snacks'), ('P2', 'Coffee beans 1kg', 'Drinks'), ('P3', 'Sparkling water', 'Drinks'), ('P4', 'Rice crackers', 'Snacks')) AS t(product_id, product_name, category);
CREATE TABLE silver.orders (order_id INTEGER, customer_id INTEGER, product_id VARCHAR, qty INTEGER, unit_price DECIMAL(10,2), order_ts TIMESTAMP, load_date VARCHAR);
CREATE TABLE ops.load_log (load_date VARCHAR, rows_landed BIGINT);
