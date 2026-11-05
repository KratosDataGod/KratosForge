-- A shop's stores and two million orders, made on this PC from fixed hashes (the same rows every time),
-- stored in order_date order, as a table loaded day by day is.
CREATE TABLE stores AS
SELECT i AS store_id, 'Store ' || lpad(i::VARCHAR, 2, '0') AS store_name,
       ['North', 'South', 'East', 'West'][1 + i % 4] AS region
FROM range(1, 13) AS t(i);

CREATE TABLE sales_by_day AS
SELECT i AS order_id,
       CAST(1 + hash(i, 7, 5) % 12 AS INTEGER) AS store_id,
       DATE '2025-01-01' + CAST(hash(i, 7, 7) % 640 AS INTEGER) AS order_date,
       CAST(5 + hash(i, 7, 8) % 9500 / 10.0 AS DECIMAL(12,2)) AS amount,
       CASE WHEN hash(i, 7, 9) % 20 = 0 THEN 'cancelled'
            WHEN hash(i, 7, 9) % 20 = 1 THEN 'returned' ELSE 'complete' END AS status
FROM range(1, 2000001) AS t(i)
ORDER BY order_date;
