-- E33 grading data the learner never sees: other separators and signs in other places, and more
-- values that aren't amounts at all. DuckDB SQL.
CREATE SCHEMA bronze;
CREATE TABLE bronze.payments AS SELECT * FROM (VALUES
  ('h1', '$ 10.00'),
  ('h2', '12,345,678.90'),
  ('h3', 'unknown'),
  ('h4', '   '),
  ('h5', '7'),
  ('h6', '$1,000')
) AS t(payment_id, amount);
