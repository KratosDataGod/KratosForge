CREATE TABLE bronze_orders AS
SELECT * FROM (VALUES
    ('1001', '2026-03-01', '40.00',  'complete', TIMESTAMP '2026-03-02 01:00:00'),
    ('1002', '2026-03-01', '15.50',  'open',     TIMESTAMP '2026-03-02 01:00:00'),
    ('1003', '2026-03-02', '99.00',  'Complete', TIMESTAMP '2026-03-03 01:00:00'),
    ('1002', '2026-03-01', '15.50',  'complete', TIMESTAMP '2026-03-03 01:00:00'),
    ('1004', '2026-03-03', '12.25',  'cancelled', TIMESTAMP '2026-03-04 01:00:00')
) AS t(order_id, order_date, amount, status, loaded_at);
