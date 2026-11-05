-- E20: clean bronze.orders into silver.orders in Databricks SQL. Run shows what a query returns;
-- Check my work runs the whole answer on fresh data.
--
-- 1. Drop rows without an order_id.
-- 2. Trim and cast order_id and customer_id to BIGINT.
-- 3. order_date: a DATE from order_ts, which comes as 'yyyy-MM-dd HH:mm:ss' or 'MM/dd/yyyy'.
-- 4. amount: remove the thousands commas; blank means unknown (NULL); DECIMAL(10,2).
-- 5. status: trimmed and lower case; blank becomes 'unknown'.
-- 6. One row per order: the load wrote some rows twice.
-- 7. Write silver.orders so that running this again leaves the same table.

SELECT order_id, customer_id, order_ts, amount, status
FROM bronze.orders;
