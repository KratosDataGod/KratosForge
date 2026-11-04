-- E25 practice data: every change to an order lands as a new row in sales.order_events, so an order can
-- appear several times. DuckDB SQL; deterministic, nothing random.
CREATE SCHEMA sales;
CREATE TABLE sales.order_events (
  order_id BIGINT, customer_id BIGINT, order_date DATE, amount DECIMAL(10,2), status VARCHAR, updated_at TIMESTAMP);
INSERT INTO sales.order_events VALUES
  (1, 10, DATE '2026-03-01', 100.00, 'new',       TIMESTAMP '2026-03-01 08:00:00'),
  (1, 10, DATE '2026-03-01', 120.00, 'shipped',   TIMESTAMP '2026-03-02 09:00:00'),
  (2, 10, DATE '2026-03-03',  80.00, 'new',       TIMESTAMP '2026-03-03 10:00:00'),
  (3, 10, DATE '2026-03-03', 120.00, 'new',       TIMESTAMP '2026-03-03 11:00:00'),
  (4, 10, DATE '2026-03-05',  60.00, 'new',       TIMESTAMP '2026-03-05 12:00:00'),
  (4, 10, DATE '2026-03-05',  60.00, 'cancelled', TIMESTAMP '2026-03-06 08:00:00'),
  (5, 20, DATE '2026-03-02', 200.00, 'new',       TIMESTAMP '2026-03-02 13:00:00'),
  (6, 20, DATE '2026-03-04',  50.00, 'new',       TIMESTAMP '2026-03-04 14:00:00'),
  (6, 20, DATE '2026-03-04',  75.00, 'new',       TIMESTAMP '2026-03-04 15:00:00'),
  (6, 20, DATE '2026-03-04',  70.00, 'shipped',   TIMESTAMP '2026-03-05 16:00:00'),
  (7, 20, DATE '2026-03-06', 200.00, 'shipped',   TIMESTAMP '2026-03-06 17:00:00'),
  (8, 30, DATE '2026-03-07',  30.00, 'new',       TIMESTAMP '2026-03-07 18:00:00');
