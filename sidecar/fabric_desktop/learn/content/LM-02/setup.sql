CREATE TABLE staging_payments AS
SELECT * FROM (VALUES
    ('1', '2026-01-05', '19.99'),
    ('2', '2026-01-06', '5.10'),
    ('3', '2026-02-30', '12.00'),
    ('4', '2026-01-07', '0.70'),
    ('5', 'not a date', '3.30'),
    ('6', '2026-01-09', '0.20')
) AS t(payment_id, paid_on, amount);
