CREATE TABLE customers (customer_id INTEGER, name VARCHAR, segment VARCHAR);
INSERT INTO customers VALUES (1, 'Contoso', 'Enterprise'), (2, 'Fabrikam', 'SMB'), (3, 'Tailspin', 'SMB');
CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, order_date DATE, amount DECIMAL(12, 2));
INSERT INTO orders VALUES
    (1, 1, DATE '2026-10-01', 100.00), (2, 1, DATE '2026-10-05', 50.00),
    (3, 2, DATE '2026-10-03', 20.00), (4, 9, DATE '2026-10-04', 5.00);
