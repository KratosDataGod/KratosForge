CREATE SCHEMA silver;
CREATE TABLE silver.orders (order_id INTEGER, customer_id INTEGER, amount DECIMAL(10, 2), discount DECIMAL(10, 2));
INSERT INTO silver.orders VALUES (1, 1, 100.00, 10.00), (2, 1, 50.00, NULL), (3, 2, 80.00, NULL), (4, 3, 40.00, 5.00);
CREATE TABLE silver.customer_net (customer_id INTEGER, net DECIMAL(12, 2));
INSERT INTO silver.customer_net VALUES (1, 999.00), (9, 1.00);
