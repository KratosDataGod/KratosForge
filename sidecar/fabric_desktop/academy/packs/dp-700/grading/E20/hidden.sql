-- E20 grading data the learner never sees: four regions, two of them new. DuckDB SQL.
CREATE SCHEMA bronze;
CREATE TABLE bronze.orders AS SELECT * FROM (VALUES
  (11, 'north', 3.00::DECIMAL(10,2)),
  (12, 'east', 6.00::DECIMAL(10,2)),
  (13, 'north', 9.00::DECIMAL(10,2)),
  (14, 'west', 1.50::DECIMAL(10,2)),
  (15, 'south', 2.25::DECIMAL(10,2)),
  (16, 'north', 4.00::DECIMAL(10,2))
) AS t(order_id, region, amount);
