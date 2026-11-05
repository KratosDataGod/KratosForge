-- E32 grading data the learner never sees: other ids, another unknown product, a 'returned' order,
-- another duplicate and other missing values. DuckDB SQL.
CREATE SCHEMA bronze;
CREATE TABLE bronze.raw_orders AS SELECT * FROM (VALUES
  ('31', '2026-04-10', 'P4', '5', '1.10', ' Complete'),
  ('32', '2026-04-10', 'P5', '2', '7.00', 'complete'),
  ('32', '2026-04-10', 'P5', '2', '7.00', 'complete'),
  ('33', '2026-04-11', 'P4', '1', '1.10', 'returned'),
  ('34', '2026-04-11', 'P8', '6', '3.30', 'complete'),
  ('35', '2026-04-12', 'P5', NULL, '7.00', 'complete'),
  ('36', NULL, 'P4', '1', '1.10', 'complete'),
  ('37', '2026-04-12', 'P4', '10', '1.10', 'COMPLETE')
) AS t(order_id, order_date, product_id, qty, unit_price, status);
CREATE TABLE bronze.products AS SELECT * FROM (VALUES
  ('P4', 'Lights'),
  ('P5', 'Pumps')
) AS t(product_id, category);
