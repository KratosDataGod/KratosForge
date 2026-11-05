-- E24 grading data the learner never sees: a triple delivery, a customer who moved twice, two real
-- orders of the same amount by one customer, and a country with one customer and several orders.
-- DuckDB SQL.
CREATE SCHEMA bronze;
CREATE TABLE bronze.order_events AS SELECT * FROM (VALUES
  ('h1', 7, 5.00), ('h1', 7, 5.00), ('h1', 7, 5.00), ('h2', 7, 7.00),
  ('h3', 8, 100.00), ('h4', 9, 1.00), ('h5', 9, 1.00), ('h6', 9, 3.00)
) AS t(event_id, customer_id, amount);
CREATE TABLE bronze.customer_versions AS SELECT * FROM (VALUES
  (7, 'DE', TIMESTAMP '2026-01-01 00:00:00'),
  (7, 'AT', TIMESTAMP '2026-02-01 00:00:00'),
  (7, 'CH', TIMESTAMP '2026-03-01 00:00:00'),
  (8, 'AT', TIMESTAMP '2026-01-05 00:00:00'),
  (9, 'DE', TIMESTAMP '2026-01-06 00:00:00')
) AS t(customer_id, country, valid_from);
