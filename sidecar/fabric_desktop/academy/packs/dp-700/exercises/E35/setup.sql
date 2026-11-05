CREATE TABLE dim_date AS
SELECT CAST(strftime(d, '%Y%m%d') AS INTEGER) AS date_key, CAST(d AS DATE) AS date
FROM range(DATE '2026-02-26', DATE '2026-03-04', INTERVAL 1 DAY) AS t(d);

CREATE TABLE dim_product (product_key INTEGER, product_name VARCHAR, category VARCHAR);
INSERT INTO dim_product VALUES (1, 'Trail bike', 'Bikes'), (2, 'Road helmet', 'Helmets'), (3, 'Rear light', 'Lights');

CREATE TABLE dim_store (store_key INTEGER, store_name VARCHAR, region VARCHAR);
INSERT INTO dim_store VALUES (1, 'Store 01', 'North'), (2, 'Store 02', 'South');

-- Sale 5 has product_key 99, which dim_product doesn't have.
CREATE TABLE fact_sales (sale_id INTEGER, date_key INTEGER, product_key INTEGER, store_key INTEGER,
                         quantity INTEGER, amount DECIMAL(12,2));
INSERT INTO fact_sales VALUES
    (1, 20260226, 1, 1, 1, 899.00),
    (2, 20260227, 2, 1, 2, 120.00),
    (3, 20260301, 3, 2, 4,  60.00),
    (4, 20260303, 1, 2, 1, 899.00),
    (5, 20260303, 99, 1, 3, 45.00);
