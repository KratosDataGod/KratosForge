CREATE TABLE src_customers AS
SELECT * FROM (VALUES
    ('C-003', 'Tailspin Toys'),
    ('C-001', 'Contoso'),
    ('C-002', 'Fabrikam'),
    ('C-001', 'Contoso')
) AS t(customer_code, name);

CREATE TABLE src_sales AS
SELECT * FROM (VALUES
    (1, 'C-001', 40.00),
    (2, 'C-002', 15.50),
    (3, 'C-003', 99.00),
    (4, 'C-001', 12.25)
) AS t(sale_id, customer_code, amount);
