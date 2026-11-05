-- E33 practice data: order amounts as the payment system exports them, with currency signs,
-- thousands separators, padding, blanks and the text N/A. DuckDB SQL; deterministic, nothing random.
CREATE SCHEMA bronze;
CREATE TABLE bronze.payments AS SELECT * FROM (VALUES
  ('p1', '120.00'),
  ('p2', '1,250.50'),
  ('p3', '$75.25'),
  ('p4', ' 42.00 '),
  ('p5', ''),
  ('p6', 'N/A'),
  ('p7', '$2,000.00'),
  ('p8', '0.99')
) AS t(payment_id, amount);
