CREATE TABLE orders AS
SELECT * FROM (VALUES
    (1, 'North', 120.00::DECIMAL(12,2)),
    (2, 'North',  80.00::DECIMAL(12,2)),
    (3, 'South',  45.50::DECIMAL(12,2)),
    (4, 'North', 120.00::DECIMAL(12,2)),
    (5, 'South',  60.00::DECIMAL(12,2)),
    (6, 'West',   15.25::DECIMAL(12,2)),
    (7, 'South',  12.00::DECIMAL(12,2))
) AS t(order_id, region, amount);
