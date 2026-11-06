-- E51 practice data: the silver table as the notebook left it yesterday, and today's batch with the new source schema. DuckDB SQL; fixed values.
CREATE SCHEMA silver;
CREATE SCHEMA bronze;
-- What the notebook has loaded so far.
CREATE TABLE silver.orders AS SELECT * FROM (VALUES
  (1, DATE '2026-03-01', 10.00::DECIMAL(10,2), 'complete'),
  (2, DATE '2026-03-01', 20.50::DECIMAL(10,2), 'complete'),
  (3, DATE '2026-03-02', 7.25::DECIMAL(10,2), 'complete')
) AS t(order_id, order_date, amount, status);
-- Today's batch. Since this morning the source sends order_amount instead of amount, and a new channel column.
CREATE TABLE bronze.orders_batch AS SELECT * FROM (VALUES
  (4, DATE '2026-03-03', 5.25::DOUBLE, 'complete', 'web'),
  (5, DATE '2026-03-03', 12.00::DOUBLE, 'complete', 'store'),
  (6, DATE '2026-03-03', 30.10::DOUBLE, 'complete', 'web')
) AS t(order_id, order_date, order_amount, status, channel);
