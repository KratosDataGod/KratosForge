CREATE TABLE stores (store_id INTEGER, name VARCHAR);
INSERT INTO stores VALUES (1, 'North'), (2, 'South');
CREATE TABLE sales (sale_id INTEGER, store_id INTEGER, amount DECIMAL(10, 2));
INSERT INTO sales VALUES (1, 1, 10.00), (2, 1, 20.00), (3, 2, 5.00);
