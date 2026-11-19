-- E67 grading data the learner never sees: other days, a correction for a different day that shrinks it to one sale. DuckDB SQL.
CREATE SCHEMA silver;
CREATE SCHEMA bronze;
CREATE TABLE silver.daily_sales AS SELECT * FROM (VALUES
  (51, DATE '2026-05-10', 'east', 1.00::DECIMAL(10,2)),
  (52, DATE '2026-05-11', 'east', 2.00::DECIMAL(10,2)),
  (53, DATE '2026-05-11', 'west', 3.00::DECIMAL(10,2)),
  (54, DATE '2026-05-11', 'west', 4.00::DECIMAL(10,2)),
  (55, DATE '2026-05-12', 'east', 5.00::DECIMAL(10,2)),
  (56, DATE '2026-05-13', 'west', 6.00::DECIMAL(10,2))
) AS t(sale_id, sale_date, store, amount);
CREATE TABLE bronze.sales_correction AS SELECT * FROM (VALUES
  (53, DATE '2026-05-11', 'west', 3.30::DECIMAL(10,2), DATE '2026-05-14')
) AS t(sale_id, sale_date, store, amount, received_date);
