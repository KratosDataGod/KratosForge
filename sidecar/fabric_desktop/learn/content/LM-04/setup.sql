CREATE TABLE sales AS
SELECT * FROM (VALUES
    (1, 'North', 120.00),
    (2, 'North', 300.00),
    (3, 'North', 200.00),
    (4, 'South',  50.00),
    (5, 'South',  60.00),
    (6, 'East',  700.00),
    (7, 'West',  250.00),
    (8, 'West',  260.00),
    (9, 'West',   10.00)
) AS t(sale_id, region, amount);
