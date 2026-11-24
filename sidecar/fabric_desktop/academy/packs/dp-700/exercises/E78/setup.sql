-- E78 practice data. Sales, a customer dimension that keeps history (type 2) and a product dimension.
-- C01 moved from North to South on 2026-03-10, and a sale falls on that very day. C02 changed segment.
-- Customer C09 and product P99 aren't in their dimensions. DuckDB SQL with fixed values.
CREATE SCHEMA silver;
CREATE TABLE silver.sales AS SELECT sale_id::INTEGER AS sale_id, customer_id, product_id,
    sale_date::DATE AS sale_date, amount::DECIMAL(10,2) AS amount FROM (VALUES
  (1, 'C01', 'P01', '2026-03-02', 120.00),
  (2, 'C01', 'P02', '2026-03-09', 35.50),
  (3, 'C01', 'P01', '2026-03-10', 80.00),
  (4, 'C01', 'P03', '2026-03-15', 12.25),
  (5, 'C02', 'P02', '2026-03-05', 60.00),
  (6, 'C02', 'P01', '2026-03-20', 99.90),
  (7, 'C09', 'P03', '2026-03-12', 15.00),
  (8, 'C02', 'P99', '2026-03-21', 42.00)
) AS t(sale_id, customer_id, product_id, sale_date, amount);
-- valid_from is the first day a version applies, valid_to the first day it no longer does.
CREATE TABLE silver.dim_customer AS SELECT customer_key::INTEGER AS customer_key, customer_id, customer_name, segment,
    region, valid_from::DATE AS valid_from, valid_to::DATE AS valid_to, is_current FROM (VALUES
  (1, 'C01', 'Alder Cycles', 'Retail', 'North', '2026-01-01', '2026-03-10', false),
  (2, 'C01', 'Alder Cycles', 'Retail', 'South', '2026-03-10', '9999-12-31', true),
  (3, 'C02', 'Birch Sports', 'Retail', 'East', '2026-01-01', '2026-03-18', false),
  (4, 'C02', 'Birch Sports', 'Wholesale', 'East', '2026-03-18', '9999-12-31', true),
  (5, 'C03', 'Cedar Outdoors', 'Retail', 'West', '2026-01-01', '9999-12-31', true)
) AS t(customer_key, customer_id, customer_name, segment, region, valid_from, valid_to, is_current);
CREATE TABLE silver.dim_product AS SELECT * FROM (VALUES
  ('P01', 'Trail bike', 'Bikes'),
  ('P02', 'Road helmet', 'Helmets'),
  ('P03', 'Rear light', 'Lights')
) AS t(product_id, product_name, category);
