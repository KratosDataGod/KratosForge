CREATE TABLE sales (product VARCHAR, region VARCHAR, sold_on DATE, amount DECIMAL(10, 2));
INSERT INTO sales VALUES
    ('Anvil', 'North', DATE '2026-02-01', 100.00),
    ('Anvil', 'North', DATE '2026-05-01', 50.00),
    ('Anvil', 'South', DATE '2026-02-01', 70.00),
    ('Hammer', 'North', DATE '2026-03-01', 40.00),
    ('Hammer', 'North', DATE '2025-12-31', 90.00),
    ('Tongs', 'South', DATE '2026-04-01', 25.00);
