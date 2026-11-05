-- E21 grading data the learner never sees: other ids, a customer id shared by three regions, and two
-- store orders with no customer. DuckDB SQL.
CREATE SCHEMA bronze;
CREATE TABLE bronze.web_orders AS SELECT * FROM (VALUES
  (21, 500, 'APAC', 10.00),
  (22, 500, 'EU',   20.00),
  (23, 501, 'US',    5.00)
) AS t(order_id, customer_id, region, amount);
CREATE TABLE bronze.store_orders AS SELECT * FROM (VALUES
  ('US', 30.00, 24, 500),
  ('EU', 44.40, 25, 777),
  ('APAC', 1.00, 26, 888)
) AS t(region, amount, order_id, customer_id);
CREATE TABLE bronze.customers AS SELECT * FROM (VALUES
  (500, 'APAC', 'Ito'),
  (500, 'EU',   'Ines'),
  (500, 'US',   'Ike'),
  (501, 'US',   'Jo')
) AS t(customer_id, region, name);
