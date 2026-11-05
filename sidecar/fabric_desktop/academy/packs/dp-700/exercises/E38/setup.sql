CREATE TABLE dim_customer (customer_key INTEGER, customer_id VARCHAR, name VARCHAR);
INSERT INTO dim_customer VALUES (0, 'UNKNOWN', 'Unknown'), (1, 'C1', 'Ana Ruiz'), (2, 'C2', 'Ben Okafor');

CREATE TABLE stg_orders (order_id INTEGER, customer_id VARCHAR, qty INTEGER, amount DECIMAL(12,2), channel VARCHAR);
INSERT INTO stg_orders VALUES
    (1, 'C1', 2,    10.00, 'store'),
    (2, NULL, 1,     5.00, 'web'),
    (3, 'C2', NULL,  8.00, NULL),
    (4, 'C9', 1,    NULL,  'store'),
    (5, 'C2', 3,    12.00, 'app'),
    (6, 'C9', 2,     3.00, 'app');
