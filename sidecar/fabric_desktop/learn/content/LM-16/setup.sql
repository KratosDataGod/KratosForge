CREATE TABLE orders (order_id INTEGER, customer VARCHAR, order_date DATE, amount DECIMAL(10, 2));
INSERT INTO orders VALUES
    (1, 'Contoso', DATE '2026-01-05', 100.00),
    (2, 'Contoso', DATE '2026-02-05', 100.00),
    (3, 'Fabrikam', DATE '2026-03-01', 50.00),
    (4, 'Tailspin', DATE '2026-04-01', 300.00),
    (5, 'Fabrikam', DATE '2025-12-30', 500.00),
    (6, 'Northwind', DATE '2026-05-01', 30.00);
