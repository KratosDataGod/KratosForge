CREATE TABLE dim_customer (
    customer_key INTEGER, customer_id INTEGER, city VARCHAR, email VARCHAR,
    valid_from DATE, valid_to DATE, is_current BOOLEAN
);
INSERT INTO dim_customer VALUES
    (1, 101, 'Seattle', 'ana@example.com',  DATE '2024-01-01', DATE '2025-06-01', false),
    (2, 102, 'Austin',  'ben@example.com',  DATE '2025-01-01', NULL, true),
    (3, 103, 'Boston',  'chen@example.com', DATE '2025-01-01', NULL, true),
    (4, 101, 'Tacoma',  'ana@example.com',  DATE '2025-06-01', NULL, true);

CREATE TABLE customer_changes (customer_id INTEGER, city VARCHAR, email VARCHAR, changed_on DATE);
INSERT INTO customer_changes VALUES
    (101, 'Portland', 'ana.ruiz@example.com', DATE '2026-03-01'),
    (102, 'Austin',   'ben.o@example.com',    DATE '2026-03-01'),
    (103, 'Boston',   'chen@example.com',     DATE '2026-03-01'),
    (104, 'Denver',   'dee@example.com',      DATE '2026-03-01');
