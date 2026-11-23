-- E70 grading data the learner never sees: other customers, three orders on one day, a later order
-- with a smaller id and a small order after a big one. DuckDB SQL.
CREATE SCHEMA silver;
CREATE TABLE silver.orders AS SELECT order_id::INTEGER AS order_id, customer_id, order_date::DATE AS order_date,
    amount::DECIMAL(10,2) AS amount FROM (VALUES
  (43, 'C11', '2026-04-02', 7.00),
  (41, 'C11', '2026-04-02', 15.00),
  (42, 'C11', '2026-04-02', 0.50),
  (40, 'C11', '2026-04-05', 9.99),
  (50, 'C12', '2026-04-01', 100.00),
  (51, 'C12', '2026-04-03', 1.00),
  (44, 'C12', '2026-04-04', 30.00),
  (60, 'C13', '2026-04-03', 12.34)
) AS t(order_id, customer_id, order_date, amount);
