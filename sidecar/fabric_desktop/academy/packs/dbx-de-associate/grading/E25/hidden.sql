-- E25 grading data the learner never sees: other customers, more versions per order, ties on the same
-- date and on the same amount, and an order cancelled and then reinstated. DuckDB SQL.
CREATE SCHEMA sales;
CREATE TABLE sales.order_events (
  order_id BIGINT, customer_id BIGINT, order_date DATE, amount DECIMAL(10,2), status VARCHAR, updated_at TIMESTAMP);
INSERT INTO sales.order_events VALUES
  (11, 40, DATE '2026-04-01',  10.00, 'new',       TIMESTAMP '2026-04-01 08:00:00'),
  (11, 40, DATE '2026-04-01',  15.00, 'new',       TIMESTAMP '2026-04-01 09:00:00'),
  (11, 40, DATE '2026-04-01',  15.00, 'shipped',   TIMESTAMP '2026-04-02 09:00:00'),
  (12, 40, DATE '2026-04-01',  15.00, 'new',       TIMESTAMP '2026-04-01 10:00:00'),
  (13, 40, DATE '2026-04-02',   5.00, 'new',       TIMESTAMP '2026-04-02 11:00:00'),
  (13, 40, DATE '2026-04-02',   5.00, 'cancelled', TIMESTAMP '2026-04-03 11:00:00'),
  (13, 40, DATE '2026-04-02',   6.00, 'new',       TIMESTAMP '2026-04-04 11:00:00'),
  (14, 50, DATE '2026-04-03', 300.00, 'new',       TIMESTAMP '2026-04-03 12:00:00'),
  (14, 50, DATE '2026-04-03', 300.00, 'cancelled', TIMESTAMP '2026-04-04 12:00:00'),
  (15, 50, DATE '2026-04-05',  90.00, 'shipped',   TIMESTAMP '2026-04-05 13:00:00'),
  (16, 50, DATE '2026-04-05',  90.00, 'new',       TIMESTAMP '2026-04-05 14:00:00'),
  (17, 60, DATE '2026-04-06',  12.50, 'new',       TIMESTAMP '2026-04-06 15:00:00');
