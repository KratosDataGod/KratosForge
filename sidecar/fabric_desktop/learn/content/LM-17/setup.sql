CREATE TABLE customers (customer_id INTEGER, name VARCHAR);
INSERT INTO customers VALUES (1, 'Contoso'), (2, 'Fabrikam'), (3, 'Tailspin'), (4, 'Northwind');
CREATE TABLE orders (order_id INTEGER, customer_id INTEGER);
INSERT INTO orders VALUES (10, 1), (11, 1), (12, 3), (13, NULL);
CREATE TABLE customer_updates (customer_id INTEGER, email VARCHAR, updated_at TIMESTAMP);
INSERT INTO customer_updates VALUES
    (1, 'ana@old.example', TIMESTAMP '2026-01-01 09:00'),
    (1, 'ana@new.example', TIMESTAMP '2026-03-01 09:00'),
    (2, 'ben@example.com', TIMESTAMP '2026-02-01 09:00'),
    (2, 'ben@example.com', TIMESTAMP '2026-02-01 09:00'),
    (3, 'cy@example.com', TIMESTAMP '2026-01-15 09:00');
