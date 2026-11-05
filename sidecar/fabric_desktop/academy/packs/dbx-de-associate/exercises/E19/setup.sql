-- E19 practice data: bronze.orders as it landed, every column text. Deterministic; nothing random.
CREATE SCHEMA bronze;
CREATE TABLE bronze.orders AS SELECT * FROM (VALUES
  ('1001', ' 501', '2026-03-01 08:15:00', '120.00',   'Shipped'),
  ('1002', '502',  '03/01/2026',          '1,250.50', 'shipped'),
  (' 1003', '503', '2026-03-02 09:00:00', '75.25',    'NEW'),
  ('1004', '501',  '03/02/2026',          '',         'new'),
  ('1005', '504',  '2026-03-03 17:45:10', '2,000.00', 'Cancelled'),
  ('1005', '504',  '2026-03-03 17:45:10', '2,000.00', 'Cancelled'),
  (NULL,   '505',  '2026-03-03 18:00:00', '15.00',    'new'),
  ('1006', '502',  '03/04/2026',          '310.10',   ''),
  ('1007', NULL,   '2026-03-04 10:30:00', '42.00',    'SHIPPED'),
  ('1008', '506',  '2026-03-05 11:11:11', '9.99',     'Returned'),
  ('1009', '507',  '03/05/2026',          '1,000.00', 'shipped'),
  (NULL,   '508',  '03/06/2026',          '5.00',     'new'),
  ('1010', '508',  '2026-03-06 23:59:59', '0.00',     'New')
) AS t(order_id, customer_id, order_ts, amount, status);
