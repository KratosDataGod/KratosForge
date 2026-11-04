CREATE TABLE customers (customer_id INTEGER, region VARCHAR);
INSERT INTO customers VALUES (1, 'East'), (2, 'West'), (3, 'East'), (4, 'North');
CREATE TABLE raw_orders (order_id INTEGER, customer_id INTEGER, amount DECIMAL(10, 2), loaded_at TIMESTAMP);
INSERT INTO raw_orders VALUES
    (101, 1, 100.00, TIMESTAMP '2026-11-01 06:00'),
    (102, 2, 50.00, TIMESTAMP '2026-11-01 06:00'),
    (102, 2, 55.00, TIMESTAMP '2026-11-01 09:00'),
    (103, 3, 30.00, TIMESTAMP '2026-11-01 06:00'),
    (103, 3, -30.00, TIMESTAMP '2026-11-01 09:00'),
    (104, NULL, 70.00, TIMESTAMP '2026-11-01 06:00'),
    (105, 4, 40.00, TIMESTAMP '2026-11-01 06:00'),
    (106, 1, 20.00, TIMESTAMP '2026-11-01 06:00');
