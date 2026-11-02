CREATE TABLE batch_jan (order_id INTEGER, amount DECIMAL(10, 2));
INSERT INTO batch_jan VALUES (1, 10.00), (2, 20.00);
CREATE TABLE batch_feb (order_id INTEGER, amount DECIMAL(10, 2), currency VARCHAR);
INSERT INTO batch_feb VALUES (3, 30.00, 'EUR'), (4, 40.00, 'USD');
