-- Gold-layer sales for the first quarter: one row per region, category and month.
CREATE TABLE gold_sales AS
SELECT region, category, month, CAST(amount AS DECIMAL(12,2)) AS amount FROM (VALUES
    ('North', 'Bikes',       DATE '2026-01-01', 1200.00),
    ('North', 'Bikes',       DATE '2026-02-01',  800.00),
    ('North', 'Accessories', DATE '2026-01-01',  150.00),
    ('North', 'Accessories', DATE '2026-03-01',  250.00),
    ('South', 'Bikes',       DATE '2026-01-01',  900.00),
    ('South', 'Bikes',       DATE '2026-03-01', 1100.00),
    ('South', 'Clothing',    DATE '2026-02-01',  300.00),
    ('West',  'Accessories', DATE '2026-02-01',  120.00),
    ('West',  'Clothing',    DATE '2026-02-01',  180.00),
    ('West',  'Clothing',    DATE '2026-03-01',  200.00)
) AS t(region, category, month, amount);
