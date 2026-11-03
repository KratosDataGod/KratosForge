CREATE SCHEMA sales;
CREATE TABLE sales.orders (order_id INTEGER, amount INTEGER);
INSERT INTO sales.orders VALUES (1, 10), (2, 20), (3, 30);
CREATE TABLE orders (order_id INTEGER, amount INTEGER);
INSERT INTO orders VALUES (1, 10), (2, 20);
