-- E70 practice data: one row per order, in no particular order. Customer C01 has two orders on
-- 2026-03-03 and C02 two on 2026-03-05, so the order of same-day orders matters. DuckDB SQL; fixed values.
CREATE SCHEMA silver;
CREATE TABLE silver.orders AS SELECT order_id::INTEGER AS order_id, customer_id, order_date::DATE AS order_date,
    amount::DECIMAL(10,2) AS amount FROM (VALUES
  (7, 'C01', '2026-03-03', 4.50),
  (1, 'C01', '2026-03-01', 10.00),
  (4, 'C02', '2026-03-02', 20.00),
  (5, 'C01', '2026-03-03', 5.50),
  (2, 'C01', '2026-03-02', 1.00),
  (9, 'C02', '2026-03-05', 3.25),
  (8, 'C02', '2026-03-05', 12.00),
  (3, 'C03', '2026-03-04', 60.00),
  (6, 'C01', '2026-03-06', 2.00)
) AS t(order_id, customer_id, order_date, amount);
