-- E31 practice data: sensor readings with missing devices, times and temperatures, and readings
-- outside the sensor's range, including both edges of it. DuckDB SQL; deterministic, nothing random.
CREATE SCHEMA bronze;
CREATE TABLE bronze.readings AS SELECT * FROM (VALUES
  (1, 'd1', TIMESTAMP '2026-03-01 08:00:00',  21.5),
  (2, 'd1', TIMESTAMP '2026-03-01 09:00:00',  60.0),
  (3, NULL, TIMESTAMP '2026-03-01 09:00:00',  20.0),
  (4, 'd2', NULL,                             19.0),
  (5, 'd2', TIMESTAMP '2026-03-01 10:00:00',  NULL),
  (6, 'd2', TIMESTAMP '2026-03-01 11:00:00',  75.2),
  (7, 'd3', TIMESTAMP '2026-03-01 11:30:00', -50.0),
  (8, 'd3', TIMESTAMP '2026-03-01 12:00:00', -51.0)
) AS t(reading_id, device_id, reading_ts, temperature);
