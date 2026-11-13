-- E51 grading data the learner never sees: other orders, another channel and a batch row with no channel. DuckDB SQL.
CREATE SCHEMA silver;
CREATE SCHEMA bronze;
-- What the notebook has loaded so far.
CREATE TABLE silver.orders AS SELECT * FROM (VALUES
  (41, DATE '2026-04-01', 1.10::DECIMAL(10,2), 'complete'),
  (42, DATE '2026-04-02', 2.20::DECIMAL(10,2), 'complete')
) AS t(order_id, order_date, amount, status);
-- Today's batch. Since this morning the source sends order_amount instead of amount, and a new channel column.
CREATE TABLE bronze.orders_batch AS SELECT * FROM (VALUES
  (43, DATE '2026-04-03', 3.30::DOUBLE, 'complete', 'app'),
  (44, DATE '2026-04-03', 4.40::DOUBLE, 'complete', NULL),
  (45, DATE '2026-04-04', 5.50::DOUBLE, 'complete', 'store'),
  (46, DATE '2026-04-04', 6.60::DOUBLE, 'complete', 'web')
) AS t(order_id, order_date, order_amount, status, channel);
