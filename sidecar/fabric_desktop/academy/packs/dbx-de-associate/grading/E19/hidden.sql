-- E19 grading data the learner never sees: the same kinds of defects, other values, plus statuses
-- with stray spaces, so an answer that only fits the practice rows fails.
CREATE SCHEMA bronze;
CREATE TABLE bronze.orders AS SELECT * FROM (VALUES
  ('2001', '601',  '2026-04-10 07:00:00', '3,400.75', ' Shipped '),
  ('2002', ' 602', '04/10/2026',          '18.00',    'new '),
  ('2003', '603',  '2026-04-11 12:00:00', '',         'RETURNED'),
  ('2003', '603',  '2026-04-11 12:00:00', '',         'RETURNED'),
  (NULL,   '604',  '04/11/2026',          '11.11',    'new'),
  ('2004', NULL,   '04/12/2026',          '10,000.00','  cancelled'),
  (' 2005', '605', '2026-04-12 15:30:00', '0.50',     ''),
  ('2006', '606',  '04/13/2026',          '99.90',    'Shipped')
) AS t(order_id, customer_id, order_ts, amount, status);
