-- E67 practice data: silver.daily_sales as the nightly loads left it, and a correction file for one business day. DuckDB SQL; fixed values.
CREATE SCHEMA silver;
CREATE SCHEMA bronze;
-- Three business days, loaded one night each.
CREATE TABLE silver.daily_sales AS SELECT * FROM (VALUES
  (1, DATE '2026-03-01', 'north', 12.00::DECIMAL(10,2)),
  (2, DATE '2026-03-01', 'south', 8.50::DECIMAL(10,2)),
  (3, DATE '2026-03-01', 'north', 4.25::DECIMAL(10,2)),
  (4, DATE '2026-03-02', 'north', 15.00::DECIMAL(10,2)),
  (5, DATE '2026-03-02', 'south', 9.00::DECIMAL(10,2)),
  (6, DATE '2026-03-02', 'south', 22.00::DECIMAL(10,2)),
  (7, DATE '2026-03-03', 'north', 6.75::DECIMAL(10,2)),
  (8, DATE '2026-03-03', 'south', 11.00::DECIMAL(10,2))
) AS t(sale_id, sale_date, store, amount);
-- The source's corrected file for one business day, received a day later. It holds the whole day:
-- sale 4's amount was wrong, sale 6 was voided, and sale 9 was missed.
CREATE TABLE bronze.sales_correction AS SELECT * FROM (VALUES
  (4, DATE '2026-03-02', 'north', 15.50::DECIMAL(10,2), DATE '2026-03-03'),
  (5, DATE '2026-03-02', 'south', 9.00::DECIMAL(10,2), DATE '2026-03-03'),
  (9, DATE '2026-03-02', 'north', 3.00::DECIMAL(10,2), DATE '2026-03-03')
) AS t(sale_id, sale_date, store, amount, received_date);
