-- E68 grading data the learner never sees: other orders, one sent three times with the newest in the middle, and a resent change. DuckDB SQL.
CREATE SCHEMA silver;
CREATE SCHEMA bronze;
-- One row per order: its latest known state.
CREATE TABLE silver.orders AS SELECT * FROM (VALUES
  (71, 'placed', 7.10::DECIMAL(10,2), TIMESTAMP '2026-07-01 08:00:00'),
  (72, 'placed', 7.20::DECIMAL(10,2), TIMESTAMP '2026-07-01 08:00:00'),
  (73, 'delivered', 7.30::DECIMAL(10,2), TIMESTAMP '2026-06-20 08:00:00')
) AS t(order_id, status, amount, updated_at);
-- Today's batch.
CREATE TABLE bronze.order_updates AS SELECT * FROM (VALUES
  (71, 'shipped', 7.10::DECIMAL(10,2), TIMESTAMP '2026-07-02 09:00:00'),
  (71, 'delivered', 7.10::DECIMAL(10,2), TIMESTAMP '2026-07-02 17:00:00'),
  (71, 'packed', 7.10::DECIMAL(10,2), TIMESTAMP '2026-07-02 08:00:00'),
  (72, 'refunded', 0.00::DECIMAL(10,2), TIMESTAMP '2026-07-02 10:00:00'),
  (72, 'refunded', 0.00::DECIMAL(10,2), TIMESTAMP '2026-07-02 10:00:00'),
  (74, 'placed', 7.40::DECIMAL(10,2), TIMESTAMP '2026-07-02 11:00:00'),
  (75, 'placed', 9.00::DECIMAL(10,2), TIMESTAMP '2026-07-02 12:00:00'),
  (75, 'placed', 7.50::DECIMAL(10,2), TIMESTAMP '2026-07-02 12:30:00')
) AS t(order_id, status, amount, updated_at);
