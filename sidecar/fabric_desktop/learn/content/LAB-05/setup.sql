CREATE TABLE dim_customer (
    customer_key INTEGER, customer_id INTEGER, city VARCHAR,
    valid_from DATE, valid_to DATE, is_current BOOLEAN
);
INSERT INTO dim_customer VALUES
    (1, 101, 'Seattle', DATE '2025-01-01', NULL, true),
    (2, 102, 'Austin', DATE '2025-01-01', NULL, true),
    (3, 103, 'Boston', DATE '2025-01-01', NULL, true);
CREATE TABLE customer_changes (customer_id INTEGER, city VARCHAR, changed_on DATE);
INSERT INTO customer_changes VALUES
    (101, 'Portland', DATE '2026-03-01'),
    (103, 'Boston', DATE '2026-03-01'),
    (104, 'Denver', DATE '2026-03-01');
