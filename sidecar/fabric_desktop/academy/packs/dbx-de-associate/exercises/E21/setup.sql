-- E21 practice data: web and store orders land in separate bronze tables whose columns come in a
-- different order, and customers are keyed by customer_id and region together. DuckDB SQL;
-- deterministic, nothing random.
CREATE SCHEMA bronze;
CREATE TABLE bronze.web_orders AS SELECT * FROM (VALUES
  (1, 100, 'EU', 25.00),
  (2, 101, 'EU', 40.00),
  (3, 100, 'US', 15.50),
  (4, 102, 'US', 60.00)
) AS t(order_id, customer_id, region, amount);
CREATE TABLE bronze.store_orders AS SELECT * FROM (VALUES
  ('EU', 12.00, 5, 101),
  ('US', 80.00, 6, 103),
  ('EU',  9.99, 7, 999)
) AS t(region, amount, order_id, customer_id);
CREATE TABLE bronze.customers AS SELECT * FROM (VALUES
  (100, 'EU', 'Ana'),
  (100, 'US', 'Abe'),
  (101, 'EU', 'Ben'),
  (102, 'US', 'Cy'),
  (103, 'US', 'Dee')
) AS t(customer_id, region, name);
