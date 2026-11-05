CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount DECIMAL(10, 2));
INSERT INTO orders VALUES
    (1, 10, 25.00), (2, 11, 40.00), (2, 11, 40.00),
    (3, NULL, 15.00), (4, 12, -5.00), (5, NULL, 30.00);
