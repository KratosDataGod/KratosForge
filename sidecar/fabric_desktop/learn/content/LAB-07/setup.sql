CREATE TABLE dim_product (product_key INTEGER, product_code VARCHAR, name VARCHAR);
INSERT INTO dim_product VALUES (-1, 'UNKNOWN', 'Unknown'), (1, 'ANV', 'Anvil'), (2, 'HAM', 'Hammer');
CREATE TABLE silver_sales (sale_id INTEGER, sold_on DATE, product_code VARCHAR, amount DECIMAL(12, 2));
INSERT INTO silver_sales VALUES
    (1, DATE '2026-10-01', 'ANV', 100.00),
    (2, DATE '2026-10-01', 'ANV', 50.00),
    (3, DATE '2026-10-01', 'HAM', 20.00),
    (4, DATE '2026-10-02', 'HAM', 15.00),
    (5, DATE '2026-10-02', 'X9', 9.00);
