-- E24 practice data: order events that were sometimes delivered twice (same event_id), and customer
-- profile versions where a customer can have moved country. DuckDB SQL; deterministic, nothing random.
CREATE SCHEMA bronze;
CREATE TABLE bronze.order_events AS SELECT * FROM (VALUES
  ('e1', 1, 10.00), ('e2', 1, 30.00), ('e2', 1, 30.00), ('e3', 2, 25.00),
  ('e4', 3, 40.00), ('e5', 3, 20.00), ('e5', 3, 20.00), ('e6', 4, 15.00)
) AS t(event_id, customer_id, amount);
CREATE TABLE bronze.customer_versions AS SELECT * FROM (VALUES
  (1, 'PT', TIMESTAMP '2026-01-01 00:00:00'),
  (1, 'ES', TIMESTAMP '2026-02-01 00:00:00'),
  (2, 'ES', TIMESTAMP '2026-01-15 00:00:00'),
  (3, 'PT', TIMESTAMP '2026-01-10 00:00:00'),
  (4, 'FR', TIMESTAMP '2026-01-20 00:00:00'),
  (4, 'FR', TIMESTAMP '2026-03-01 00:00:00')
) AS t(customer_id, country, valid_from);
