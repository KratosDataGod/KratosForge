-- Silver orders, with each row's keys from the key tables.
CREATE TABLE silver_orders (order_sk INTEGER, customer_sk INTEGER, order_id INTEGER, status VARCHAR,
                            order_date DATE, amount DECIMAL(18,2), net_amount DECIMAL(18,2));
INSERT INTO silver_orders VALUES
    (1, 1, 100, 'shipped', DATE '2026-10-02', 50.00, 45.00),
    (2, 1, 101, 'shipped', DATE '2026-10-06', 20.00, 20.00),
    (3, 9, 102, 'returned', DATE '2026-10-06', 10.00, 10.00);

-- A Type 2 dimension: customer_sk is the version key, customer_dk the Silver key.
-- Ada (customer_dk 1) moved from Austin to Denver on 5 October.
CREATE TABLE dim_customer (customer_sk INTEGER, customer_dk INTEGER, city VARCHAR,
                           valid_from DATE, valid_to DATE, is_current BOOLEAN);
INSERT INTO dim_customer VALUES
    (-1, -1, 'Unknown', DATE '1900-01-01', DATE '9999-12-31', true),
    (1, 1, 'Austin', DATE '1900-01-01', DATE '2026-10-05', false),
    (4, 1, 'Denver', DATE '2026-10-05', DATE '9999-12-31', true);
