-- Build fact_sales from fact_sales_raw with one CREATE TABLE ... AS SELECT, giving each column
-- the smallest type that still holds every value correctly. The team's standard:
--   whole numbers INTEGER, money DECIMAL(12,2), a date with no time DATE, a moment in time TIMESTAMP,
--   yes/no flags BOOLEAN, and codes that are labels rather than amounts stay text (VARCHAR).
-- Leave fact_sales_raw as it is.

CREATE TABLE fact_sales AS
SELECT *
FROM fact_sales_raw;


-- Keep this query last: it's what the check compares.
SELECT sale_id, store_code, sale_date, sold_at, qty, unit_price, is_return FROM fact_sales ORDER BY sale_id;
