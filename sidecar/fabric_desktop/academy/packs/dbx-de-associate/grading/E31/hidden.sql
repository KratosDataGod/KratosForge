-- E31 grading data the learner never sees: a row that breaks two rules (the first one named wins),
-- other edge values and several NULL temperatures. DuckDB SQL.
CREATE SCHEMA bronze;
CREATE TABLE bronze.readings AS SELECT * FROM (VALUES
  (11, NULL, NULL,                             99.0),
  (12, 'k1', TIMESTAMP '2026-04-01 00:00:00',  59.9),
  (13, 'k1', TIMESTAMP '2026-04-01 01:00:00',  NULL),
  (14, 'k2', TIMESTAMP '2026-04-01 02:00:00',  NULL),
  (15, 'k2', TIMESTAMP '2026-04-01 03:00:00', -49.9),
  (16, 'k3', TIMESTAMP '2026-04-01 04:00:00', 60.1)
) AS t(reading_id, device_id, reading_ts, temperature);
