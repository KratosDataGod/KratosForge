-- E42 practice data: today's order events, and silver.orders as yesterday's load left it. DuckDB SQL; fixed values.
CREATE SCHEMA bronze;
CREATE SCHEMA silver;
-- Order events, appended by the source as they happen. One order can have several.
CREATE TABLE bronze.order_events AS SELECT * FROM (VALUES
  (1, 'placed', 10.00::DECIMAL(10,2), TIMESTAMP '2026-05-01 09:00:00'),
  (1, 'shipped', 10.00::DECIMAL(10,2), TIMESTAMP '2026-05-01 12:00:00'),
  (2, 'placed', 5.00::DECIMAL(10,2), TIMESTAMP '2026-05-01 10:00:00'),
  (3, 'placed', 7.00::DECIMAL(10,2), TIMESTAMP '2026-04-30 08:00:00'),
  (4, 'placed', 2.50::DECIMAL(10,2), TIMESTAMP '2026-05-01 11:00:00'),
  (4, 'cancelled', 2.50::DECIMAL(10,2), TIMESTAMP '2026-05-01 11:30:00'),
  (4, 'refunded', 0.00::DECIMAL(10,2), TIMESTAMP '2026-05-01 13:00:00')
) AS t(order_id, status, amount, event_time);
-- Each order's latest known state, as yesterday's load left it.
CREATE TABLE silver.orders AS SELECT * FROM (VALUES
  (3, 'delivered', 7.00::DECIMAL(10,2), TIMESTAMP '2026-05-01 08:00:00'),
  (9, 'delivered', 1.00::DECIMAL(10,2), TIMESTAMP '2026-04-01 08:00:00')
) AS t(order_id, status, amount, event_time);
