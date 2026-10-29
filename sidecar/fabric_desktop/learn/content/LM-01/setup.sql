CREATE TABLE orders AS
SELECT * FROM (VALUES
    (1, 'Contoso',  'North', 120.50),
    (2, 'Contoso',  'North',  80.00),
    (3, 'Fabrikam', 'North', 250.00),
    (4, 'Fabrikam', 'South', 900.00),
    (5, 'Tailspin', 'North', 100.00),
    (6, 'Tailspin', 'West',  310.00),
    (7, 'Contoso',  'North', 420.00)
) AS t(order_id, customer, region, amount);
