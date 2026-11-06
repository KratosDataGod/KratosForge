-- E32 practice data: raw orders as text, the way a CSV lands in Bronze, and the product list.
-- DuckDB SQL; fixed values, nothing random.
CREATE SCHEMA bronze;
CREATE TABLE bronze.raw_orders AS SELECT * FROM (VALUES
  (' 1', '2026-03-01', 'P1', '2', '10.00', 'Complete'),
  ('2', '2026-03-01', 'P2', '1', '25.50', ' complete '),
  ('3', '2026-03-02', 'P1', NULL, '10.00', 'complete'),
  ('4', '2026-03-02', 'P3', '3', '4.25', 'CANCELLED'),
  ('5', '2026-03-03', 'P9', '1', '99.00', 'complete'),
  ('6', NULL, 'P2', '2', '25.50', 'complete'),
  ('7', '2026-03-04', 'P3', '4', '4.25', 'complete'),
  ('7', '2026-03-04', 'P3', '4', '4.25', 'complete'),
  ('8', '2026-03-04', 'P2', '3', '25.50', 'COMPLETE ')
) AS t(order_id, order_date, product_id, qty, unit_price, status);
CREATE TABLE bronze.products AS SELECT * FROM (VALUES
  ('P1', 'Locks'),
  ('P2', 'Helmets'),
  ('P3', 'Bikes')
) AS t(product_id, category);
