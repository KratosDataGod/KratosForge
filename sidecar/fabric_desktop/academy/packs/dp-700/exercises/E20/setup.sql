-- E20 practice data: today's orders in two regions. DuckDB SQL; fixed values.
CREATE SCHEMA bronze;
CREATE TABLE bronze.orders AS SELECT * FROM (VALUES
  (1, 'east', 10.00::DECIMAL(10,2)),
  (2, 'east', 15.50::DECIMAL(10,2)),
  (3, 'west', 8.25::DECIMAL(10,2)),
  (4, 'east', 4.75::DECIMAL(10,2)),
  (5, 'west', 20.00::DECIMAL(10,2))
) AS t(order_id, region, amount);
