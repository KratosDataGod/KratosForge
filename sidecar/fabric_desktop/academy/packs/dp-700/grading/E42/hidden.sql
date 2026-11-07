-- E42 grading data the learner never sees: other orders, a late event and an order with two events. DuckDB SQL.
CREATE SCHEMA bronze;
CREATE SCHEMA silver;
-- Order events, appended by the source as they happen. One order can have several.
CREATE TABLE bronze.order_events AS SELECT * FROM (VALUES
  (21, 'placed', 3.00::DECIMAL(10,2), TIMESTAMP '2026-06-01 07:00:00'),
  (22, 'shipped', 4.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 09:00:00'),
  (23, 'placed', 6.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 10:00:00'),
  (23, 'shipped', 6.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 15:00:00'),
  (24, 'placed', 8.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 11:00:00')
) AS t(order_id, status, amount, event_time);
-- Each order's latest known state, as yesterday's load left it.
CREATE TABLE silver.orders AS SELECT * FROM (VALUES
  (21, 'shipped', 3.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 08:00:00'),
  (22, 'placed', 4.00::DECIMAL(10,2), TIMESTAMP '2026-06-01 08:00:00')
) AS t(order_id, status, amount, event_time);
