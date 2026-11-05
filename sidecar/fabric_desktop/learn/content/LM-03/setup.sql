CREATE TABLE customers AS
SELECT * FROM (VALUES
    (1, 'Contoso'),
    (2, 'Fabrikam'),
    (2, 'Fabrikam'),
    (3, 'Tailspin')
) AS t(customer_id, name);

CREATE TABLE orders AS
SELECT * FROM (VALUES
    (101, 1, 50.00),
    (102, 2, 75.00),
    (103, 2, 20.00),
    (104, 3, 10.00),
    (105, 4, 30.00)
) AS t(order_id, customer_id, amount);
