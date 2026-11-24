-- E78 grading data the learner never sees. Other customers, three versions of one customer, a sale on
-- each change day, the day before one, a customer with no versions and a product with no row. DuckDB SQL.
CREATE SCHEMA silver;
CREATE TABLE silver.sales AS SELECT sale_id::INTEGER AS sale_id, customer_id, product_id,
    sale_date::DATE AS sale_date, amount::DECIMAL(10,2) AS amount FROM (VALUES
  (21, 'K10', 'Q1', '2026-05-01', 10.00),
  (22, 'K10', 'Q2', '2026-05-07', 20.00),
  (23, 'K10', 'Q1', '2026-05-08', 30.00),
  (24, 'K10', 'Q3', '2026-05-20', 40.00),
  (25, 'K10', 'Q2', '2026-05-21', 50.00),
  (26, 'K11', 'Q1', '2026-05-03', 5.00),
  (27, 'K12', 'Q3', '2026-05-04', 7.50),
  (28, 'K11', 'Q9', '2026-05-30', 8.00)
) AS t(sale_id, customer_id, product_id, sale_date, amount);
CREATE TABLE silver.dim_customer AS SELECT customer_key::INTEGER AS customer_key, customer_id, customer_name, segment,
    region, valid_from::DATE AS valid_from, valid_to::DATE AS valid_to, is_current FROM (VALUES
  (11, 'K10', 'Kite Works', 'Retail', 'North', '2026-04-01', '2026-05-08', false),
  (12, 'K10', 'Kite Works', 'Retail', 'West', '2026-05-08', '2026-05-21', false),
  (13, 'K10', 'Kite Works', 'Wholesale', 'West', '2026-05-21', '9999-12-31', true),
  (14, 'K11', 'Larch Ltd', 'Online', 'South', '2026-04-01', '9999-12-31', true)
) AS t(customer_key, customer_id, customer_name, segment, region, valid_from, valid_to, is_current);
CREATE TABLE silver.dim_product AS SELECT * FROM (VALUES
  ('Q1', 'Gravel bike', 'Bikes'),
  ('Q2', 'Bottle cage', 'Parts'),
  ('Q3', 'Front light', 'Lights')
) AS t(product_id, product_name, category);
