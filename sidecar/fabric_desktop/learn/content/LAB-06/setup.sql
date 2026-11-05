CREATE TABLE bronze_orders (order_id VARCHAR, order_date VARCHAR, amount VARCHAR, ingested_at TIMESTAMP);
INSERT INTO bronze_orders VALUES
    ('1', '2026-01-05', '10.50', TIMESTAMP '2026-01-08 01:00'),
    ('2', '2026-01-06', '20.00', TIMESTAMP '2026-01-08 01:00'),
    ('2', '2026-01-06', '25.00', TIMESTAMP '2026-01-09 01:00'),
    ('3', 'not a date', '5.00', TIMESTAMP '2026-01-08 01:00'),
    ('4', '2026-01-07', '7.25', TIMESTAMP '2026-01-08 01:00'),
    ('4', '2026-01-07', '7.25', TIMESTAMP '2026-01-08 01:00');
