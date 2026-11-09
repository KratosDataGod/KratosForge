-- C1 grading data the learner never sees: four days in landing, day 1 already landed and merged. DuckDB SQL.
CREATE SCHEMA landing;
CREATE SCHEMA bronze;
CREATE SCHEMA silver;
CREATE SCHEMA ops;
CREATE TABLE landing.orders AS SELECT * FROM (VALUES
  (2001, 11, 'P2', 1, 12.50::DECIMAL(10,2), TIMESTAMP '2026-10-01 09:00:00', '2026-10-01'),
  (2002, 12, 'P1', 3, 5.00::DECIMAL(10,2), TIMESTAMP '2026-10-01 09:30:00', '2026-10-01'),
  (2003, 13, 'P4', 2, 3.25::DECIMAL(10,2), TIMESTAMP '2026-10-02 08:15:00', '2026-10-02'),
  (2003, 13, 'P4', 2, 3.25::DECIMAL(10,2), TIMESTAMP '2026-10-02 08:15:00', '2026-10-02'),
  (2004, 12, 'P3', -1, 2.00::DECIMAL(10,2), TIMESTAMP '2026-10-02 11:00:00', '2026-10-02'),
  (2005, NULL, 'P2', 1, 12.50::DECIMAL(10,2), TIMESTAMP '2026-10-02 12:00:00', '2026-10-02'),
  (2006, 14, 'P1', 2, 5.00::DECIMAL(10,2), TIMESTAMP '2026-10-02 13:00:00', '2026-10-02'),
  (2002, 12, 'P1', 0, 5.00::DECIMAL(10,2), TIMESTAMP '2026-10-01 09:30:00', '2026-10-03'),
  (2007, 11, 'P3', 6, 2.00::DECIMAL(10,2), TIMESTAMP '2026-10-03 15:00:00', '2026-10-03'),
  (2008, 13, 'P2', 1, 12.50::DECIMAL(10,2), TIMESTAMP '2026-10-03 16:30:00', '2026-10-03'),
  (2009, 11, 'P4', 4, 3.25::DECIMAL(10,2), TIMESTAMP '2026-10-04 10:00:00', '2026-10-04'),
  (2010, 15, 'P1', 1, 5.00::DECIMAL(10,2), TIMESTAMP '2026-10-04 11:00:00', '2026-10-04'),
  (2011, 13, 'P3', 2, 2.00::DECIMAL(10,2), TIMESTAMP '2026-10-02 07:00:00', '2026-10-04')
) AS t(order_id, customer_id, product_id, qty, unit_price, order_ts, load_date);
CREATE TABLE landing.customers AS SELECT * FROM (VALUES
  (11, 'Kit Deli', 'Derby', 'Retail', TIMESTAMP '2026-09-10 00:00:00', '2026-10-01'),
  (12, 'Lu Foods', 'Bristol', 'Wholesale', TIMESTAMP '2026-09-10 00:00:00', '2026-10-01'),
  (13, 'Mo Mart', 'Leeds', 'Retail', TIMESTAMP '2026-09-10 00:00:00', '2026-10-01'),
  (11, 'Kit Deli', 'Derby', 'Wholesale', TIMESTAMP '2026-10-03 12:00:00', '2026-10-03'),
  (13, 'Mo Mart', 'Leeds', 'Retail', TIMESTAMP '2026-10-03 00:00:00', '2026-10-03'),
  (12, 'Lu Foods Group', 'Bristol', 'Wholesale', TIMESTAMP '2026-10-03 01:00:00', '2026-10-03'),
  (13, 'Mo Mart', 'Wakefield', 'Retail', TIMESTAMP '2026-10-02 07:30:00', '2026-10-04'),
  (14, 'Ny Grocer', 'York', 'Retail', TIMESTAMP '2026-09-20 00:00:00', '2026-10-04'),
  (11, 'Kit Deli', 'Derby', 'Wholesale', TIMESTAMP '2026-10-04 00:00:00', '2026-10-04')
) AS t(customer_id, name, city, segment, changed_at, load_date);
CREATE TABLE bronze.orders AS SELECT * FROM (VALUES
  (2001, 11, 'P2', 1, 12.50::DECIMAL(10,2), TIMESTAMP '2026-10-01 09:00:00', '2026-10-01'),
  (2002, 12, 'P1', 3, 5.00::DECIMAL(10,2), TIMESTAMP '2026-10-01 09:30:00', '2026-10-01')
) AS t(order_id, customer_id, product_id, qty, unit_price, order_ts, load_date);
CREATE TABLE bronze.customers AS SELECT * FROM (VALUES
  (11, 'Kit Deli', 'Derby', 'Retail', TIMESTAMP '2026-09-10 00:00:00', '2026-10-01'),
  (12, 'Lu Foods', 'Bristol', 'Wholesale', TIMESTAMP '2026-09-10 00:00:00', '2026-10-01'),
  (13, 'Mo Mart', 'Leeds', 'Retail', TIMESTAMP '2026-09-10 00:00:00', '2026-10-01')
) AS t(customer_id, name, city, segment, changed_at, load_date);
CREATE TABLE bronze.products AS SELECT * FROM (VALUES ('P1', 'Oat bar', 'Snacks'), ('P2', 'Coffee beans 1kg', 'Drinks'), ('P3', 'Sparkling water', 'Drinks'), ('P4', 'Rice crackers', 'Snacks')) AS t(product_id, product_name, category);
CREATE TABLE silver.orders AS SELECT * FROM (VALUES
  (2001, 11, 'P2', 1, 12.50::DECIMAL(10,2), TIMESTAMP '2026-10-01 09:00:00', '2026-10-01'),
  (2002, 12, 'P1', 3, 5.00::DECIMAL(10,2), TIMESTAMP '2026-10-01 09:30:00', '2026-10-01')
) AS t(order_id, customer_id, product_id, qty, unit_price, order_ts, load_date);
CREATE TABLE ops.load_log AS SELECT * FROM (VALUES ('2026-10-01', 2::BIGINT)) AS t(load_date, rows_landed);
