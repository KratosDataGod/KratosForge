-- E68 practice data: silver.orders as yesterday's load left it, and today's batch of order updates. DuckDB SQL; fixed values.
CREATE SCHEMA silver;
CREATE SCHEMA bronze;
-- One row per order: its latest known state.
CREATE TABLE silver.orders AS SELECT * FROM (VALUES
  (1, 'placed', 10.00::DECIMAL(10,2), TIMESTAMP '2026-06-01 09:00:00'),
  (2, 'placed', 20.00::DECIMAL(10,2), TIMESTAMP '2026-06-01 09:00:00'),
  (3, 'placed', 30.00::DECIMAL(10,2), TIMESTAMP '2026-06-01 09:00:00'),
  (4, 'shipped', 40.00::DECIMAL(10,2), TIMESTAMP '2026-06-01 09:00:00')
) AS t(order_id, status, amount, updated_at);
-- Today's batch. The source sends every change, so an order can appear more than once,
-- and it sometimes sends the same change twice.
CREATE TABLE bronze.order_updates AS SELECT * FROM (VALUES
  (2, 'cancelled', 0.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 11:30:00'),
  (2, 'shipped', 20.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 10:00:00'),
  (3, 'shipped', 30.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 10:15:00'),
  (3, 'shipped', 30.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 10:15:00'),
  (5, 'placed', 50.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 12:00:00'),
  (5, 'placed', 55.00::DECIMAL(10,2), TIMESTAMP '2026-06-02 12:05:00')
) AS t(order_id, status, amount, updated_at);
